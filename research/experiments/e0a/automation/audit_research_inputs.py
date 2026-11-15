#!/usr/bin/env python3
"""Read-only, standard-library audit of E0a research metadata.

No LLM, API, network, data write, or Gold annotation.
Usage: python3 audit_research_inputs.py --research-dir research/experiments/e0a \
       --leads-csv path/to/EXPLORATORY_SOURCE_CANDIDATES.csv
"""
import argparse
import collections
import csv
import hashlib
import json
import pathlib
import re
import sys
from urllib.parse import urlsplit

TASK_IDS = {f"{case}-T{i}" for case in "AB" for i in range(1,5)}


def read_rows(path):
    if not path.is_file():
        raise FileNotFoundError(f"Missing research file: {path}")
    with path.open(encoding="utf-8-sig", newline="") as fp:
        return list(csv.DictReader(fp))


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def audit(research_dir, leads_csv=None):
    tasks = read_rows(research_dir / "tasks_instantiated.csv")
    cases = read_rows(research_dir / "cases.csv")
    findings = []
    task_key = "task_id"
    observed = [r.get(task_key, "").strip() for r in tasks]
    if len(tasks) != 8 or set(observed) != TASK_IDS or len(observed) != len(set(observed)):
        findings.append({"severity":"error","check":"task_frame","detail":f"Expected exactly eight unique task IDs A-T1..B-T4, found {observed}"})
    else:
        findings.append({"severity":"pass","check":"task_frame","detail":"Exactly eight expected task IDs"})
    ags = [r.get("ags", "").strip() for r in cases]
    if len(cases) != 2 or any(not re.fullmatch(r"09\d{6}",x) for x in ags) or len(set(ags)) != len(ags):
        findings.append({"severity":"error","check":"municipality_frame","detail":f"Expected two distinct Bavarian AGS, found {ags}"})
    else:
        findings.append({"severity":"pass","check":"municipality_frame","detail":"Two distinct Bavarian AGS in cases.csv"})
    manifest_path = research_dir / "MANIFEST.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            # Manifest layout varies between revisions. Validate only recognized filename/hash records.
            recognized = []
            def visit(o):
                if isinstance(o, dict):
                    filename = o.get("file") or o.get("path") or o.get("filename")
                    digest = o.get("sha256") or o.get("sha256_hex")
                    if isinstance(filename,str) and isinstance(digest,str) and re.fullmatch(r"[a-fA-F0-9]{64}",digest):
                        recognized.append((filename,digest.lower()))
                    for k,v in o.items():
                        if isinstance(v,dict) and isinstance(v.get("sha256"),str) and re.fullmatch(r"[a-fA-F0-9]{64}",v["sha256"]):
                            if isinstance(k,str) and ("." in k or "/" in k):
                                recognized.append((k,v["sha256"].lower()))
                        visit(v)
                elif isinstance(o,list):
                    for v in o:visit(v)
            visit(manifest)
            matches = 0
            for name,digest in recognized:
                if name == "MANIFEST.json":
                    findings.append({"severity":"warning","check":"manifest_self_hash","detail":"Self-hash of manifest is not independently verifiable as immutable manifest"})
                    continue
                safe_path = (research_dir / name).resolve()
                if not safe_path.is_relative_to(research_dir.resolve()):
                    findings.append({"severity":"error","check":"manifest_path","detail":f"Unsafe file path {name}"})
                elif not safe_path.is_file():
                    findings.append({"severity":"error","check":"manifest_missing","detail":name})
                elif sha256(safe_path) != digest:
                    findings.append({"severity":"error","check":"manifest_hash","detail":name})
                else:matches+=1
            findings.append({"severity":"pass" if recognized else "warning","check":"manifest_parsed","detail":f"Recognized {len(recognized)} hashed file entries; verified {matches}"})
        except (OSError,json.JSONDecodeError) as e:
            findings.append({"severity":"error","check":"manifest_parsed","detail":str(e)})
    else:
        findings.append({"severity":"warning","check":"manifest_missing","detail":"No MANIFEST.json"})

    overview = {"tasks":len(tasks),"municipalities":len(cases),"source_leads":None,"lead_counts_by_task":{},"source_leads_with_hash":None,"officially_gold_validated":False}
    if leads_csv is not None:
        leads = read_rows(leads_csv)
        by_task = collections.Counter()
        invalid = []
        for row in leads:
            for tid in row.get("task_ids","").split("|"):
                if tid:
                    if tid not in TASK_IDS:invalid.append(tid)
                    by_task[tid]+=1
            parsed = urlsplit(row.get("source_url", ""))
            if parsed.scheme not in ("http","https") or not parsed.netloc:
                findings.append({"severity":"error","check":"source_url","detail":row.get("lead_id","")})
        if invalid:
            findings.append({"severity":"error","check":"source_task_refs","detail":str(sorted(set(invalid)))})
        overview["source_leads"] = len(leads)
        overview["lead_counts_by_task"] = {tid:by_task[tid] for tid in sorted(TASK_IDS)}
        # The exploratory source catalogue has URLs and locators, not frozen source bytes.
        overview["source_leads_with_hash"] = sum(bool(row.get("document_sha256", "").strip()) for row in leads)
        findings.append({"severity":"warning","check":"gold_status","detail":"Exploratory source leads are NOT adjudicated Gold; do not score retrieval recall against them as complete truth"})
        if any(by_task[tid]==0 for tid in TASK_IDS):
            findings.append({"severity":"warning","check":"task_discovery_coverage","detail":"One or more tasks have no candidate lead"})
    return {"schema":"cpi.e0a.audit.v1","overview":overview,"findings":findings}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--research-dir",type=pathlib.Path,required=True)
    parser.add_argument("--leads-csv",type=pathlib.Path)
    args=parser.parse_args()
    result=audit(args.research_dir,args.leads_csv)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 2 if any(x["severity"]=="error" for x in result["findings"]) else 0

if __name__=="__main__":sys.exit(main())
