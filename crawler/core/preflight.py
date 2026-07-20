"""Explicit, read-only preflight checks for pipeline entry points.

Preflight checks deliberately never create a database, repair schema, or mark
work as complete. They make missing predecessor outputs and schema drift fail
before a worker or query invocation can act on incomplete state.
"""

from __future__ import annotations

import re
import sqlite3
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path


class PreflightError(RuntimeError):
    """Raised when an entry point's explicit prerequisites are not satisfied."""


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Read a SQLite varint without opening a SQLite connection."""
    value = 0
    for index in range(9):
        if offset + index >= len(data):
            raise PreflightError("SQLite preflight encountered a truncated record.")
        byte = data[offset + index]
        if index == 8:
            return (value << 8) | byte, offset + 9
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, offset + index + 1
    raise PreflightError("SQLite preflight encountered an invalid varint.")


def _decode_sqlite_record(payload: bytes) -> list[object]:
    """Decode the small sqlite_master records needed for schema validation."""
    header_size, offset = _read_varint(payload, 0)
    if header_size > len(payload):
        raise PreflightError("SQLite preflight encountered an oversized record header.")
    serial_types: list[int] = []
    while offset < header_size:
        serial_type, offset = _read_varint(payload, offset)
        serial_types.append(serial_type)

    values: list[object] = []
    data_offset = header_size
    for serial_type in serial_types:
        if serial_type == 0:
            values.append(None)
            continue
        if serial_type == 8:
            values.append(0)
            continue
        if serial_type == 9:
            values.append(1)
            continue
        if serial_type in {1, 2, 3, 4, 5, 6}:
            length = {1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8}[serial_type]
            value_bytes = payload[data_offset : data_offset + length]
            if len(value_bytes) != length:
                raise PreflightError("SQLite preflight encountered a truncated integer.")
            values.append(int.from_bytes(value_bytes, "big", signed=True))
        elif serial_type == 7:
            value_bytes = payload[data_offset : data_offset + 8]
            if len(value_bytes) != 8:
                raise PreflightError("SQLite preflight encountered a truncated float.")
            values.append(struct.unpack(">d", value_bytes)[0])
            length = 8
        elif serial_type >= 12:
            length = (serial_type - 12) // 2
            value_bytes = payload[data_offset : data_offset + length]
            if len(value_bytes) != length:
                raise PreflightError("SQLite preflight encountered an overflow record.")
            values.append(
                value_bytes.decode("utf-8", errors="replace") if serial_type % 2 else value_bytes
            )
        else:
            raise PreflightError("SQLite preflight encountered an unsupported serial type.")
        data_offset += length
    return values


def _sqlite_schema_rows(db_path: Path) -> list[list[object]]:
    """Read sqlite_master directly, avoiding WAL sidecars from SQLite connections."""
    with db_path.open("rb") as database:
        header = database.read(100)
        if len(header) != 100 or header[:16] != b"SQLite format 3\x00":
            raise PreflightError(f"Selected database is not a valid SQLite 3 file: {db_path}")
        page_size = int.from_bytes(header[16:18], "big") or 65536
        if page_size < 512 or page_size & (page_size - 1):
            raise PreflightError(f"Selected database has an invalid SQLite page size: {db_path}")

        def read_page(page_number: int) -> bytes:
            database.seek((page_number - 1) * page_size)
            page = database.read(page_size)
            if len(page) != page_size:
                raise PreflightError("SQLite preflight encountered a truncated database page.")
            return page

        rows: list[list[object]] = []
        visited: set[int] = set()

        def visit(page_number: int) -> None:
            if page_number in visited:
                raise PreflightError("SQLite preflight encountered a cyclic schema b-tree.")
            visited.add(page_number)
            page = read_page(page_number)
            header_offset = 100 if page_number == 1 else 0
            page_type = page[header_offset]
            cell_count = int.from_bytes(page[header_offset + 3 : header_offset + 5], "big")
            pointer_offset = header_offset + (8 if page_type == 0x0D else 12)
            cell_pointers = [
                int.from_bytes(
                    page[pointer_offset + index * 2 : pointer_offset + index * 2 + 2], "big"
                )
                for index in range(cell_count)
            ]
            if page_type == 0x05:
                child_pages = [
                    int.from_bytes(page[cell_pointer : cell_pointer + 4], "big")
                    for cell_pointer in cell_pointers
                ]
                child_pages.append(
                    int.from_bytes(page[header_offset + 8 : header_offset + 12], "big")
                )
                for child_page in child_pages:
                    visit(child_page)
                return
            if page_type != 0x0D:
                raise PreflightError("SQLite preflight found an unsupported schema b-tree page.")
            for cell_pointer in cell_pointers:
                payload_size, cell_offset = _read_varint(page, cell_pointer)
                _, cell_offset = _read_varint(page, cell_offset)  # rowid
                payload = page[cell_offset : cell_offset + payload_size]
                if len(payload) != payload_size:
                    raise PreflightError(
                        "SQLite schema record spans overflow pages; use a checkpointed database "
                        "or extend the non-mutating schema inspector."
                    )
                rows.append(_decode_sqlite_record(payload))

        visit(1)
        return rows


def _table_columns(create_sql: str) -> set[str]:
    """Extract column names from a CREATE TABLE statement in sqlite_master."""
    uncommented_sql = re.sub(r"--[^\r\n]*", "", create_sql)
    body = uncommented_sql[uncommented_sql.find("(") + 1 : uncommented_sql.rfind(")")]
    columns: set[str] = set()
    for definition in body.split(","):
        token = definition.strip().split(maxsplit=1)[0].strip('`"[]') if definition.strip() else ""
        if token.upper() not in {"CHECK", "CONSTRAINT", "FOREIGN", "PRIMARY", "UNIQUE"}:
            columns.add(token)
    return columns


def validate_sqlite_database(
    db_path: Path,
    *,
    required_columns: Mapping[str, Sequence[str]],
    required_nonempty_tables: Sequence[str] = (),
) -> Path:
    """Validate a selected SQLite database through non-mutating file reads.

    The selected path is returned unchanged. This lets callers pass exactly
    the path that was preflighted through the rest of an invocation.
    """
    if not db_path.is_file():
        raise PreflightError(f"Selected database does not exist or is not a file: {db_path}")

    try:
        schema = {
            str(row[1]): str(row[4])
            for row in _sqlite_schema_rows(db_path)
            if len(row) >= 5 and row[0] == "table" and row[1] and row[4]
        }
        for table, columns in required_columns.items():
            if table not in schema:
                raise PreflightError(
                    f"Selected database is missing required table '{table}': {db_path}. "
                    "Run the required predecessor stage first."
                )
            present_columns = _table_columns(schema[table])
            missing_columns = set(columns) - present_columns
            if missing_columns:
                raise PreflightError(
                    f"Required table '{table}' has incompatible schema; missing "
                    f"columns {sorted(missing_columns)} in {db_path}."
                )

    except PreflightError:
        raise
    except OSError as exc:
        raise PreflightError(
            f"Selected database cannot be read safely: {db_path} ({type(exc).__name__})."
        ) from exc

    return db_path


def validate_worker_preflight(db_path: Path) -> Path:
    """Validate the seed-job state required before a crawl worker can run."""
    return validate_sqlite_database(
        db_path,
        required_columns={
            "seed_jobs": (
                "municipality_id",
                "seed_url",
                "status",
                "claimed_by",
                "claimed_at",
                "heartbeat_at",
                "done_at",
                "last_error",
                "attempt_count",
            )
        },
    )


def validate_query_preflight(db_path: Path) -> Path:
    """Validate embedded chunks, the required predecessor output for retrieval."""
    selected_db_path = validate_sqlite_database(
        db_path,
        required_columns={"chunks": ("chunk_id", "text", "embedding")},
    )
    read_only_uri = f"{selected_db_path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(read_only_uri, uri=True) as connection:
            embedded_chunk = connection.execute(
                "SELECT 1 FROM chunks WHERE embedding IS NOT NULL LIMIT 1"
            ).fetchone()
    except sqlite3.Error as exc:
        raise PreflightError(
            f"Selected database cannot validate embedded chunks: {selected_db_path} "
            f"({type(exc).__name__})."
        ) from exc
    if embedded_chunk is None:
        raise PreflightError(
            f"Required predecessor output 'chunks.embedding' is empty in {selected_db_path}. "
            "Run generate_embeddings.py before querying."
        )
    return selected_db_path
