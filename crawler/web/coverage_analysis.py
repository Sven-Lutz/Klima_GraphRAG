"""Snapshot-bound, read-only municipality coverage diagnostics; proxies are never policy scores."""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import tempfile
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from research.bavaria.federation import source_connection
from research.gold_studio.store import RESERVE, digest

SCHEMA = "klima-municipality-coverage-v1"
MAX_CACHE_BYTES = 16 * 1024**2
TOPICS = {
    "heat": ("Hitzevorsorge", r"hitz"),
    "adaptation": ("Klimaanpassung", r"klimaanpass|klimafolg"),
    "heat_planning": ("Wärmeplanung/-versorgung", r"wärmeplan|waermeplan|wärmeversorg|fernwärm"),
    "renewables": ("Erneuerbare Energien", r"photovolta|solar|windkraft|erneuerbar"),
    "mobility": ("Mobilität", r"radverkehr|radweg|mobilität|mobilitaet|öpnv|oepnv|nahverkehr"),
    "flood": ("Hochwasser/Starkregen", r"hochwasser|starkregen|überflut|ueberflut"),
    "targets": ("Klimaziele/-bilanz", r"klimaneutral|klimaziel|treibhausgas|co2"),
    "land": ("Entsiegelung/Stadtgrün", r"entsiegel|begrün|begruen|biodivers|stadtgrün"),
}
YEAR = re.compile(r"\b(?:19\d{2}|20\d{2}|2100)\b")
VG = re.compile(r"verwaltungsgemeinschaft|\bvg\b", re.I)


class CoverageAnalysis:
    def __init__(self, service, directory):
        self.service = service
        self.workspace = service.workspace
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.binding = {
            "schema": SCHEMA,
            "snapshot_id": service.database_snapshot_id,
            "topic_rules": {key: pattern for key, (_, pattern) in TOPICS.items()},
        }
        self.path = self.directory / (digest(self.binding) + ".json")
        self._report = None

    def _identity(self):
        # The local Bavaria adapter owns the same manifested SQLite identity gates.
        self.service.retriever.check_identity()

    def report(self):
        self._identity()
        if self._report is None:
            if self.path.exists():
                if self.path.stat().st_size > MAX_CACHE_BYTES:
                    raise ValueError("Coverage cache exceeds its bounded budget")
                report = json.loads(self.path.read_text())
                body = {k: v for k, v in report.items() if k != "content_sha256"}
                if report.get("binding") != self.binding or report.get("content_sha256") != digest(
                    body
                ):
                    raise ValueError(
                        "Coverage cache integrity/binding failed; preserve it and choose "
                        "a new cache directory"
                    )
                self._report = report
            else:
                report = self._build()
                report["content_sha256"] = digest(report)
                encoded = json.dumps(report, ensure_ascii=False, sort_keys=True).encode()
                if (
                    len(encoded) > MAX_CACHE_BYTES
                    or shutil.disk_usage(self.directory).free < RESERVE + MAX_CACHE_BYTES
                ):
                    raise ValueError("Reserve plus bounded coverage-cache budget unavailable")
                with tempfile.NamedTemporaryFile(
                    dir=self.directory, prefix="coverage-", suffix=".pending", delete=False
                ) as f:
                    f.write(encoded)
                    pending = Path(f.name)
                pending.replace(self.path)
                self._report = report
        return self._report

    def _build(self):
        self._identity()
        sources, associations = {}, []
        patterns = {key: re.compile(pattern, re.I) for key, (_, pattern) in TOPICS.items()}
        with closing(source_connection(self.workspace.db, self.workspace.source_databases)) as c:
            for row in c.execute("SELECT * FROM documents ORDER BY sha256"):
                path = Path(row["raw_path"])
                try:
                    size = path.stat().st_size
                except OSError:
                    size = None
                sources[row["sha256"]] = dict(
                    source_sha256=row["sha256"],
                    parse_status=row["parse_status"],
                    archive_present=size is not None,
                    archive_size_matches_metadata=size
                    in {row["raw_bytes"], row["compressed_bytes"]}
                    if size is not None
                    else False,
                    original_integrity="NOT_HASH_VERIFIED_BY_THIS_COVERAGE_AUDIT",
                    publication_date_status="UNKNOWN_NOT_RECORDED_IN_ACQUISITION_LEDGER",
                    unit_count=0,
                    unit_kinds={},
                    topic_unit_mentions={key: 0 for key in TOPICS},
                    year_mentions={},
                    shared_frame_ags=[],
                    urls=[],
                    authority_values=[],
                    vg_text_proxy=False,
                )
            associations = [
                dict(r)
                for r in c.execute(
                    "SELECT DISTINCT sha256,ags,url,authority FROM associations ORDER "
                    "BY ags,sha256,url"
                )
            ]
            cursor = c.execute("SELECT sha256,text,kind FROM units ORDER BY sha256,unit_id")
            while True:
                batch = cursor.fetchmany(300)
                if not batch:
                    break
                for row in batch:
                    source = sources[row["sha256"]]
                    source["unit_count"] += 1
                    kind = row["kind"] or "unknown"
                    source["unit_kinds"][kind] = source["unit_kinds"].get(kind, 0) + 1
                    text = row["text"]
                    for key, pattern in patterns.items():
                        source["topic_unit_mentions"][key] += bool(pattern.search(text))
                    for year in set(YEAR.findall(text)):
                        source["year_mentions"][year] = source["year_mentions"].get(year, 0) + 1
                    source["vg_text_proxy"] |= bool(VG.search(text))
        frames = {ags: set() for ags in self.workspace.municipalities}
        orphan_associations = []
        for row in associations:
            sha, ags = row["sha256"], row["ags"]
            if sha not in sources or ags not in frames:
                orphan_associations.append({"source_sha256": sha, "ags": ags})
                continue
            frames[ags].add(sha)
            source = sources[sha]
            for key, value in (
                ("shared_frame_ags", ags),
                ("urls", row["url"]),
                ("authority_values", row["authority"]),
            ):
                if value not in source[key]:
                    source[key].append(value)
        graph_by_frame, graph_by_claim, scope_differences = {}, {}, []
        for relation in self.workspace.relation_rows:
            record = relation["record"]
            sha = record["source_sha256"]
            frame_ags = set(
                record.get("source_frame_ags") or sources.get(sha, {}).get("shared_frame_ags", [])
            )
            claim_ags = set(record.get("claim_scope_ags") or [])
            for ags in frame_ags:
                graph_by_frame.setdefault(ags, set()).add(relation["relation_id"])
            for ags in claim_ags:
                graph_by_claim.setdefault(ags, set()).add(relation["relation_id"])
            if frame_ags != claim_ags:
                scope_differences.append(
                    dict(
                        relation_id=relation["relation_id"],
                        source_sha256=sha,
                        source_frame_ags=sorted(frame_ags),
                        claim_scope_ags=sorted(claim_ags),
                        interpretation="DISTINCT_ASSOCIATIONS_REVIEW_REQUIRED_NOT_AUTOMATIC_ERROR",
                    )
                )
        rows = []
        for ags, municipality in sorted(self.workspace.municipalities.items()):
            material = [sources[sha] for sha in sorted(frames[ags])]
            units = sum(s["unit_count"] for s in material)
            topic_counts = {
                key: sum(s["topic_unit_mentions"][key] for s in material) for key in TOPICS
            }
            years = Counter()
            kinds = Counter()
            parse = Counter()
            for source in material:
                years.update(source["year_mentions"])
                kinds.update(source["unit_kinds"])
                parse.update([source["parse_status"] or "UNKNOWN"])
            gaps = []
            if not material:
                gaps.append("NO_ARCHIVED_SOURCE")
            if not units:
                gaps.append("NO_PARSED_TEXT")
            if not topic_counts["heat"]:
                gaps.append("NO_HEAT_TERM_PROXY_NOT_PROOF_OF_ABSENT_POLICY")
            if not topic_counts["heat_planning"]:
                gaps.append("NO_HEAT_PLANNING_TERM_PROXY_NOT_PROOF_OF_ABSENT_PLAN")
            rows.append(
                dict(
                    ags=ags,
                    name=municipality["name"],
                    region=municipality.get("region"),
                    district=municipality.get("district"),
                    acquisition_status=municipality.get("crawl_status", "UNKNOWN"),
                    acquisition_checked_at=municipality.get("crawl_checked_at"),
                    source_versions=len(material),
                    source_ids=sorted(frames[ags]),
                    sources_with_text=sum(s["unit_count"] > 0 for s in material),
                    text_units=units,
                    archive_files_present=sum(s["archive_present"] for s in material),
                    archive_metadata_size_matches=sum(
                        s["archive_size_matches_metadata"] for s in material
                    ),
                    parse_status_counts=dict(parse),
                    unit_kind_counts=dict(kinds),
                    source_type_counts={
                        "pdf": sum("pdf_page" in s["unit_kinds"] for s in material),
                        "html_or_text": sum(
                            any(
                                k in s["unit_kinds"]
                                for k in ["html", "heading", "paragraph", "list_item"]
                            )
                            for s in material
                        ),
                        "unclassified": sum(not s["unit_kinds"] for s in material),
                    },
                    topic_unit_mentions=topic_counts,
                    year_unit_mentions=dict(sorted(years.items())),
                    publication_freshness="UNKNOWN_TEXT_YEAR_MENTIONS_ARE_NOT_PUBLICATION_DATES",
                    shared_source_versions=sum(len(s["shared_frame_ags"]) > 1 for s in material),
                    vg_text_source_proxy=sum(s["vg_text_proxy"] for s in material),
                    graph_source_frame_relations=len(graph_by_frame.get(ags, set())),
                    graph_claim_scope_relations=len(graph_by_claim.get(ags, set())),
                    scope_difference_relations=[
                        d["relation_id"]
                        for d in scope_differences
                        if ags in d["source_frame_ags"] or ags in d["claim_scope_ags"]
                    ],
                    gap_flags=gaps,
                    source_municipality_mapping="FRAME_ASSOCIATION_NOT_INDEPENDENTLY_REVIEWED",
                    policy_activity="NOT_MEASURED",
                    scientific_validation="NOT_JUDGED",
                )
            )
        self._identity()
        return dict(
            schema=SCHEMA,
            binding=self.binding,
            created_at=datetime.now(timezone.utc).isoformat(),
            summary=dict(
                municipalities=len(rows),
                source_versions=len(sources),
                text_units=sum(s["unit_count"] for s in sources.values()),
                municipalities_with_text=sum(r["text_units"] > 0 for r in rows),
                municipalities_without_text=sum(r["text_units"] == 0 for r in rows),
                graph_relations=len(self.workspace.relation_rows),
                source_claim_scope_differences=len(scope_differences),
                orphan_associations=len(orphan_associations),
            ),
            topics={key: label for key, (label, _) in TOPICS.items()},
            limitations=[
                "Topic and year counts are explicit text proxies, never policy "
                "quality/activity or publication freshness.",
                "File presence/size is not a hash integrity check or source "
                "redistribution permission.",
                "Source-frame versus claim-scope differences and VG/shared "
                "sources need human review; not automatic assignment errors.",
                "No detected terms or parsed text does not establish absence of "
                "policy or an external source.",
            ],
            municipalities=rows,
            sources=sources,
            scope_differences=scope_differences,
            orphan_associations=orphan_associations,
        )

    def query(self, *, q="", region="", gap="", topic="", offset=0, limit=100):
        if (
            len(q) > 200
            or not 0 <= offset <= 100000
            or not 1 <= limit <= 200
            or (topic and topic not in TOPICS)
        ):
            raise ValueError("Invalid bounded coverage filter")
        data = self.report()
        rows = [
            r
            for r in data["municipalities"]
            if (not q or q.casefold() in (r["ags"] + " " + r["name"]).casefold())
            and (not region or r["region"] == region)
            and (not gap or any(gap in flag for flag in r["gap_flags"]))
            and (not topic or r["topic_unit_mentions"][topic] > 0)
        ]
        return dict(
            schema=SCHEMA,
            snapshot_id=self.service.database_snapshot_id,
            summary=data["summary"],
            topics=data["topics"],
            regions=sorted({r["region"] for r in data["municipalities"] if r["region"]}),
            limitations=data["limitations"],
            content_sha256=data["content_sha256"],
            audit_created_at=data["created_at"],
            items=rows[offset : offset + limit],
            total=len(rows),
            offset=offset,
        )

    def municipality(self, ags):
        report = self.report()
        row = next((r for r in report["municipalities"] if r["ags"] == ags), None)
        if row is None:
            raise ValueError("Unknown municipality")
        return dict(
            **row,
            snapshot_id=self.service.database_snapshot_id,
            sources=[report["sources"][sha] for sha in row["source_ids"]],
            scope_differences=[
                r
                for r in report["scope_differences"]
                if r["relation_id"] in row["scope_difference_relations"]
            ],
        )

    def csv(self):
        data = self.report()
        stream = io.StringIO()
        fields = [
            "ags",
            "name",
            "region",
            "district",
            "source_versions",
            "sources_with_text",
            "text_units",
            "archive_files_present",
            "acquisition_status",
            "acquisition_checked_at",
            "publication_freshness",
            "shared_source_versions",
            "vg_text_source_proxy",
            "graph_source_frame_relations",
            "graph_claim_scope_relations",
            "gap_flags",
            "parse_status_counts",
            "unit_kind_counts",
            "topic_unit_mentions",
            "year_unit_mentions",
            "snapshot_id",
            "scientific_validation",
            "policy_activity",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in data["municipalities"]:
            cell = {**row, "snapshot_id": self.service.database_snapshot_id}
            for key in fields:
                if isinstance(cell.get(key), (dict, list)):
                    cell[key] = json.dumps(cell[key], ensure_ascii=False, sort_keys=True)
                # Spreadsheet programs must not execute untrusted source/municipality text.
                if isinstance(cell.get(key), str) and cell[key].startswith(("=", "+", "-", "@")):
                    cell[key] = "'" + cell[key]
            writer.writerow(cell)
        return stream.getvalue()


def attach_coverage_analysis(app, service, directory):
    coverage = CoverageAnalysis(service, directory)
    app.state.coverage_analysis = coverage

    @app.get("/coverage")
    async def coverage_page():
        return FileResponse(Path(__file__).with_name("static") / "coverage.html")

    @app.get("/api/coverage-analysis")
    async def query(
        q: str = "",
        region: str = "",
        gap: str = "",
        topic: str = "",
        offset: int = 0,
        limit: int = 100,
    ):
        try:
            return coverage.query(
                q=q, region=region, gap=gap, topic=topic, offset=offset, limit=limit
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/coverage-analysis/municipality/{ags}")
    async def detail(ags: str):
        try:
            return coverage.municipality(ags)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/coverage-analysis/export.csv")
    async def csv_export():
        return Response(
            coverage.csv(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="bavaria-coverage.csv"'},
        )
