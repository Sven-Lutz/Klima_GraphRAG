"""One-command offline demonstrator and explicit read-only query interface."""

import argparse
import json
import sys
from html import escape
from pathlib import Path

from research.climate_policy import store
from research.climate_policy.answer import analyze
from research.experiments.m4a_parsing_admission.diagnose import guard

EXAMPLES = [
    "Welche Förderquote und Obergrenzen nennt Straubing für Begrünung und Entsiegelung?",
    (
        "Welche Kosten, Förderung und erwarteten Energ"
        "ieeinsparungen berichtet Buttenheim für Weißg"
        "eräte?"
    ),
    "Welche Ladepunkte und Schnellladesäulen nennt Rosenheim für BayWa?",
    "Welche historischen Anschlüsse und Wohneinheiten nennt Wildpoldsried zur Nahwärme?",
    (
        "Welche Laufzeit nennt Straubing beim Klimasch"
        "utzmanagement und beweist dies eine Fertigste"
        "llung?"
    ),
    "Welche Akteure und Zuständigkeiten nennt Rosenheim zum MVV?",
    "Welche Förderung und Finanzierung berichten Straubing und Buttenheim?",
    "Ist die Energieeinsparung in Buttenheim tatsächlich gemessen?",
    "Ist der Wärmeplan Wildpoldsried 2026 fertig?",
    "Belegt die Quelle die BayWa-Ladepunkte in Buttenheim?",
    "Welche PV-Leistung und Eigenstromnutzung berichtet Wildpoldsried?",
    "Welche Fahrradabstellanlagen und Förderung nennt Straubing an den Schulen?",
]


def main():
    parser = argparse.ArgumentParser(
        description="Offline-Recherche kommunaler Klimapolitik mit Originalbelegen"
    )
    parser.add_argument("--db", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--method", choices=["bm25", "bm25_german", "sql", "tools"], default="tools")
    ask.add_argument("--json", action="store_true")
    demo = sub.add_parser("demo")
    demo.add_argument("--out", type=Path, default=Path("research/climate_policy/local/demo"))
    args = parser.parse_args()
    sys.addaudithook(guard)
    args.db = (
        args.db
        if args.db is not None
        else Path("research/climate_policy/local")
        / ("evidence-" + store.fingerprint()[:12] + ".sqlite")
    )
    receipt = store.build(args.db)
    if args.command == "build":
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return
    if args.command == "ask":
        result = analyze(args.db, args.question, args.method)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(result["answer"])
            for warning in result["warnings"]:
                print("Hinweis:", warning)
            for c in result["citations"]:
                print(
                    f"[{c['id']}] {c['url']} – Fassung {c['source_sha256'][:12]}, "
                    f"Seite {c['page_ref'] or 'HTML'}, Einheit {c['unit_id']}, "
                    f"Zeichen {c['start_char']}:{c['end_char']}"
                )
                print(c["quote"][:700])
        return
    args.out.mkdir(parents=True, exist_ok=True)
    results = [analyze(args.db, q) for q in EXAMPLES]
    blocks = [
        (
            '<!doctype html><html lang="de"><meta charset='
            '"utf-8"><title>Kommunale Klimapolitik – Forsc'
            "hungsdemonstrator</title>"
        ),
        (
            "<style>body{font:17px system-ui;max-width:100"
            "0px;margin:40px auto;padding:20px;line-height"
            ":1.55}article{border-top:1px solid #ccc;paddi"
            "ng:20px 0}blockquote{background:#f4f6f7;paddi"
            "ng:15px}small{overflow-wrap:anywhere}aside{co"
            "lor:#704600}</style>"
        ),
        (
            "<h1>Kommunale Klimapolitik aus archivierten O"
            "riginalquellen</h1><p>Explorativer lokaler Fo"
            "rschungsprototyp. Historische Aussagen, Progn"
            "osen und fehlende Nachweise werden getrennt. "
            "Keine unabhängig validierte Gesamtbewertung.<"
            "/p>"
        ),
    ]
    for r in results:
        blocks.append(
            "<article><h2>"
            + escape(r["question"])
            + "</h2><p>"
            + escape(r["answer"]).replace("\n", "<br>")
            + "</p>"
        )
        blocks.extend("<aside>" + escape(w) + "</aside>" for w in r["warnings"])
        for c in r["citations"]:
            blocks.append(
                "<details><summary>"
                + escape(
                    c["id"] + " · " + c["publisher"] + " · " + (c["heading_context"] or c["url"])
                )
                + "</summary><blockquote>"
                + escape(c["quote"])
                + "</blockquote><small>"
                + escape(
                    json.dumps({k: v for k, v in c.items() if k != "quote"}, ensure_ascii=False)
                )
                + "</small></details>"
            )
        blocks.append("</article>")
    blocks.append("</html>")
    (args.out / "index.html").write_text("\n".join(blocks))
    (args.out / "examples.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n"
    )
    (args.out / "build_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(str((args.out / "index.html").resolve()))


if __name__ == "__main__":
    main()
