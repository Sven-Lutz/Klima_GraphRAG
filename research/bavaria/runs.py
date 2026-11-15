"""Append-only local lifecycle records for materialized research stages."""

import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

from .acquisition import now


@contextmanager
def stage(args):
    path = Path(str(args.output) + ".run.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    identifier = str(uuid.uuid4())
    base = {
        "schema": "bavaria-stage-run-v1",
        "run_id": identifier,
        "stage": args.command,
        "database": str(Path(args.db).resolve()),
        "provider": "local",
        "model": getattr(args, "model", None),
        "prompt_version": "deterministic-no-generation",
        "seed": None,
        "parameters": vars(args),
        "input_ids": [str(Path(args.db).resolve())],
        "output_ids": [str(Path(args.output).resolve())],
    }

    def emit(status, error=None):
        with path.open("a") as stream:
            stream.write(
                json.dumps(
                    {**base, "status": status, "at": now(), "error_class": error}, sort_keys=True
                )
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())

    emit("pending")
    emit("running")
    try:
        yield
    except BaseException as exc:
        emit("failed", type(exc).__name__)
        raise
    else:
        emit("succeeded")
