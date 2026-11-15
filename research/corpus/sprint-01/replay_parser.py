"""Replay existing E2 parser script with frozen imports and new report path."""
from pathlib import Path
import argparse
import contextlib
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import socket
import subprocess
import platform
import sys

ROOT=Path(__file__).resolve().parents[3]
ACQ=ROOT/'research/experiments/e2_v11_acquisition_20261024'

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod)
    return mod

def main(out):
    if out.exists():raise FileExistsError('Use a fresh output directory')
    out.mkdir(parents=True)
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(ROOT))
    def denied(*a,**k):raise RuntimeError('Offline parser replay: network disabled')
    socket.socket=denied;socket.create_connection=denied
    frozen=ACQ/'implementation_snapshot'
    # The actual parser replay and equality check remain the historical code.
    load('crawler.core.models',frozen/'crawler/core/models.py')
    load('crawler.core.parsers.html_parser',frozen/'crawler/core/parsers/html_parser.py')
    load('crawler.core.parsers.pdf_parser',frozen/'crawler/core/parsers/pdf_parser.py')
    script=ACQ/'reparse.py';text=script.read_text()
    for old,new in [("Path(name).read_bytes()","(r/'implementation_snapshot'/name).read_bytes()"),("(o/'reparse_validation.json')",'Path('+repr(str(out/'reparse_validation.json'))+')')]:
        if text.count(old)!=1:raise ValueError('Unexpected legacy replay structure: '+old)
        text=text.replace(old,new)
    captured=io.StringIO()
    with contextlib.redirect_stdout(captured):
        exec(compile(text,str(script),'exec'),{'__file__':str(script),'__name__':'__audit__'})
    (out/'stdout.txt').write_text(captured.getvalue())
    deps={}
    for package in ['beautifulsoup4','lxml','pypdf','pdfplumber','pymupdf']:
        try:deps[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:deps[package]=None
    replayed=json.loads((out/'reparse_validation.json').read_text())
    poppler=subprocess.run(['pdftotext','-v'],capture_output=True,text=True)
    report={'status':'PASS','python':platform.python_version(),'pdftotext_version':poppler.stderr.splitlines()[0],'command':'PYTHONDONTWRITEBYTECODE=1 .venv/bin/python research/corpus/sprint-01/replay_parser.py --out '+str(out),'historical_text_units_reproduced':replayed['offline_units_reproduced'],'method':'Existing reparse.py executed; original code hash checks redirected to frozen implementation_snapshot and legacy report redirected to this new output. No change to text equality assertion.','driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'legacy_reparse_sha256':hashlib.sha256(script.read_bytes()).hexdigest(),'dependencies':deps,'scope':'Reproducible segmentation is not semantic completeness. Known D053 source loss is intentionally reproduced.'}
    (out/'run_manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);main(ap.parse_args().out.resolve())
