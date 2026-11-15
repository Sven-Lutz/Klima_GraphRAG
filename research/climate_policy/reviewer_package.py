"""Usable original-first independent-review package; never fabricates a review."""

import argparse
import csv
import json
import random
import shutil
from pathlib import Path

from research.climate_policy import store
from research.offline_evaluation.contracts import require


def package(benchmark, out, copy_originals=False):
    require(not out.exists(), "Use absent reviewer output directory")
    data = json.loads(Path(benchmark).read_bytes())
    out.mkdir(parents=True)
    originals = out / "originals"
    originals.mkdir()
    source_info = {
        r["body_sha256"]: r
        for r in (
            json.loads(line) for line in (store.EXPORT / "catalog.jsonl").read_bytes().splitlines()
        )
    }
    for raw in sorted({r["source_sha256"] for r in data["source_checks"].values()}):
        source = source_info[raw]
        src = store.EXPORT / "raw" / raw
        require(store.sha(src) == raw, "Original integrity")
        target = originals / (raw + (".pdf" if source["source_type"] == "pdf" else ".html"))
        if copy_originals:
            shutil.copyfile(src, target)
        else:
            target.symlink_to(src.resolve())
    for reviewer, seed in [("A", 4711), ("B", 8159)]:
        folder = out / reviewer
        folder.mkdir()
        tasks = list(data["tasks"])
        random.Random(seed).shuffle(tasks)
        text = [
            "# Unabhängige Originalprüfung – " + reviewer,
            (
                "Zuerst Originale selbst öffnen und eigen"
                "e Notizen festhalten. Keine Systemantwor"
                "ten oder Agentenreferenzen in dieser Pha"
                "se. Unbekannte Felder bleiben leer."
            ),
        ]
        with (folder / "judgments.csv").open("w", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(
                [
                    "task_id",
                    "reviewer_id",
                    "original_opened",
                    "answerability",
                    "own_answer",
                    "source_sha256",
                    "original_locator",
                    "claim_scope",
                    "role",
                    "value",
                    "unit",
                    "confidence",
                    "notes",
                    "reviewed_at",
                ]
            )
            for task in tasks:
                text.append("\n## " + task["id"] + "\n" + task["question"])
                hashes = {
                    data["source_checks"][uid]["source_sha256"]
                    for uid in task["known_reference_units"]
                }
                for raw in sorted(hashes):
                    text.append("- Originalfassung: " + raw + " · " + source_info[raw]["url"])
                writer.writerow([task["id"]] + [""] * 13)
        (folder / "TASKS.md").write_text("\n".join(text) + "\n")
    sealed = out / "after_independent_notes"
    sealed.mkdir()
    (sealed / "PROVISIONAL_REFERENCES.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    )
    (out / "PROTOCOL.md").write_text(
        (
            "# Original-first Blind-Review und Adjudi"
            "kation\n\nA und B sind zwei tatsächlich un"
            "abhängige Fachpersonen außerhalb der\nMet"
            "hodenentwicklung; dritte Person adjudizi"
            "ert. Namen/Qualifikation und\nInteressenk"
            "onflikte vor Beginn festhalten. Material"
            " nicht automatisch an Personen\nsenden.\n\n"
            "1. Jede Person erhält nur ihren Aufgaben"
            "bogen und Originale. Sie öffnet die\ntats"
            "ächlichen Originale, prüft Geltungsberei"
            "ch, historischen Bezug, Messung/Prognose"
            "\nund eigene Antwortbarkeit. Quellen-/Zei"
            "t-/Belegnotizen vor Referenzeinsicht sep"
            "arat\nspeichern. Aufgaben sind nicht repr"
            "äsentativ; Materialexposition dokumentie"
            "ren.\n2. Erst nach unveränderlich gespeic"
            "herten eigenen Urteilen wird\nafter_indep"
            "endent_notes geöffnet. Die dortigen Refe"
            "renzen sind Agentenentwürfe, kein\nGold. "
            "Systemausgaben separat und verdeckt nach"
            " Methode beurteilen; keine Systemnamen\na"
            "ls Qualitätsindiz.\n3. Vor Adjudikation A"
            "greement nur über tatsächlich doppelt be"
            "arbeitete gemeinsame\nFelder, mit Nennern"
            ", Klassenverteilungen und Unsicherheit b"
            "erechnen. Kein erfundener\nMindestwert, k"
            "ein Agreement bei leeren Formularen.\n4. "
            "Abweichungen mit beiden Originalurteilen"
            " erhalten; Adjudikator begründet jede\nEn"
            "tscheidung und ihre Evidenz. UNKNOWN nic"
            "ht als Nein umcodieren. Geänderte Refere"
            "nzen\nerhalten eine neue Version, keine Ä"
            "nderung alter Piloturteile.\n5. Größere B"
            "enchmark benötigt vorab amtlichen AGS-Au"
            "swahlrahmen, neue unabhängige\nQuellen-/G"
            "emeindegruppen, Anpassungs-/Zeit-/Konfli"
            "ktfälle, Ressourcen-/Rechteklärung\nund b"
            "edarfsbegründete Stichprobengröße. Diese"
            "s kleine Paket ersetzt diese Erweiterung"
            "\nnicht.\n\nOriginale sind lokal verlinkt ("
            "Standard) oder mit --copy-originals kopi"
            "ert. Links sind\nnicht transportabel; für"
            " einen separat autorisierten Offline-Tra"
            "nsfer --copy-originals\nin ein neues Ziel"
            " verwenden. Keine Rohquellen publizieren"
            ". Leere CSVs bedeuten keine\ndurchgeführt"
            "e menschliche Prüfung. Research Gold: PE"
            "NDING_EXTERNAL_VALIDATION.\n"
        )
    )
    print(str(out.resolve()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--copy-originals", action="store_true")
    args = parser.parse_args()
    package(args.benchmark, args.out, args.copy_originals)
