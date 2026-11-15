"""Read-only XLSX ingestion with explicit municipality and date denominators."""

import csv
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

URL = (
    "https://www.statistikportal.de/sites/default/files/2026-05/"
    "20260131_Anschriften_der_Gemeinde_und_Stadtverwaltungen.xlsx"
)
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_official(path):
    with zipfile.ZipFile(path) as archive:
        strings = [
            "".join(x.itertext())
            for x in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)
        ]
        rows = []
        for row in ET.fromstring(archive.read("xl/worksheets/sheet6.xml")).findall(
            "s:sheetData/s:row", NS
        ):
            values = {}
            for cell in row:
                value = cell.findtext("s:v", "", NS)
                if cell.attrib.get("t") == "s":
                    value = strings[int(value)]
                values[re.sub(r"\d", "", cell.attrib["r"])] = value
            if values.get("A") == "09":
                rows.append(values)
    regions = {r["F"]: r["H"] for r in rows if r.get("C") == "20"}
    districts = {r["F"]: r["H"] for r in rows if r.get("C") == "40"}
    result = {}
    for row in rows:
        if row.get("C") != "60":
            continue
        ags = row["G"]
        if not re.fullmatch(r"09\d{6}", ags):
            raise ValueError(f"Invalid municipality AGS: {ags}")
        item = dict(
            ags=ags,
            official_name=row["H"],
            municipality_type=row["E"],
            district=districts[ags[:5]],
            region=regions[ags[:3]],
            population=int(row["O"]),
            population_date="2024-12-31",
            registry_date="2026-01-31",
            official_contacts=[],
        )
        if ags in result:
            for field in ("official_name", "population", "municipality_type"):
                if result[ags][field] != item[field]:
                    raise ValueError(f"Conflicting official {field}: {ags}")
        else:
            result[ags] = item
        result[ags]["official_contacts"].append(
            {"administration": row.get("I"), "email": row.get("M")}
        )
    if len(result) != 2056 or len(regions) != 7:
        raise ValueError("Official denominator changed: review registry protocol")
    return result, sum(r.get("C") == "60" for r in rows)


def build_registry(official, seeds, output):
    records, raw_count = read_official(official)
    candidates = {r["ags"]: r for r in csv.DictReader(Path(seeds).open())}
    for ags, record in records.items():
        candidate = candidates.get(ags, {})
        record.update(
            name=candidate.get("name", record["official_name"]),
            homepage_candidate=candidate.get("homepage_url") or None,
            domain_status="UNVERIFIED_WIKIDATA_CANDIDATE",
            coverage_status="NOT_CHECKED",
            checked_at=None,
            domain_candidate_source=candidate.get("source"),
            source_url=URL,
            source_sha256=hashlib.sha256(Path(official).read_bytes()).hexdigest(),
        )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
            for _, r in sorted(records.items())
        )
    )
    return {
        "municipalities": len(records),
        "raw_municipality_address_rows": raw_count,
        "regions": dict(Counter(r["region"] for r in records.values())),
        "seed_missing_ags": sorted(set(records) - set(candidates)),
        "seed_extra_ags": sorted(set(candidates) - set(records)),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "source_sha256": next(iter(records.values()))["source_sha256"],
        "source_url": URL,
        "registry_date": "2026-01-31",
        "population_date": "2024-12-31",
    }
