"""Integration gates for immutable source-to-citation research adapters."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler.core.answer_generation import ProviderTimeoutError

from .core import Workspace, answer, make_manifest


def fixture(root: Path, scope=None, predicate="FUNDS", context=False, actor="Stiftung Beispiel"):
    verb = "plant" if predicate == "PLANS" else "fördert"
    text = f"Die {actor} {verb} das kommunale Projekt Alpha im Jahr 2024."
    scope_text = "Das Vorhaben betrifft ausschließlich Ingolstadt."
    body = (
        "<html><article>" + text + (scope_text if context else "") + "</article></html>"
    ).encode()
    raw = root / "original.html"
    raw.write_bytes(body)
    sha = hashlib.sha256(body).hexdigest()
    ledger = root / "ledger.sqlite"
    with sqlite3.connect(ledger) as conn:
        conn.executescript("""CREATE TABLE metadata(key TEXT,value TEXT);
        CREATE TABLE municipalities(ags TEXT,record TEXT,status TEXT,checked_at TEXT,error TEXT);
        CREATE TABLE documents(sha256 TEXT,raw_path TEXT,parse_status TEXT);
        CREATE TABLE associations(sha256 TEXT,ags TEXT,url TEXT,authority TEXT);
        CREATE TABLE units(unit_id TEXT,sha256 TEXT,text TEXT,heading TEXT,page_ref TEXT,kind TEXT);
        """)
        conn.execute("INSERT INTO metadata VALUES (?,?)", ("schema", "bavaria-acquisition-v1"))
        for ags, name in [("09161000", "Ingolstadt"), ("09162000", "München")]:
            record = {
                "name": name,
                "official_name": name,
                "region": "Oberbayern",
                "population": 100,
                "population_date": "2024-12-31",
            }
            conn.execute(
                "INSERT INTO municipalities VALUES (?,?,?,?,?)",
                (ags, json.dumps(record), "SYNTHETIC_FIXTURE", None, None),
            )
        conn.execute("INSERT INTO documents VALUES (?,?,?)", (sha, str(raw), "TEXT_EXTRACTED"))
        # A source acquisition association intentionally exists for both municipalities.
        conn.executemany(
            "INSERT INTO associations VALUES (?,?,?,?)",
            [
                (sha, ags, "https://example.org/project", "SYNTHETIC_FIXTURE")
                for ags in ["09161000", "09162000"]
            ],
        )
        conn.execute(
            "INSERT INTO units VALUES (?,?,?,?,?,?)",
            ("unit-alpha", sha, text, "Projekt Alpha", None, "paragraph"),
        )
        if context:
            conn.execute(
                "INSERT INTO units VALUES (?,?,?,?,?,?)",
                ("unit-scope", sha, scope_text, "Geltungsbereich", None, "paragraph"),
            )
    relations = root / "relations.sqlite"
    record = {
        "start_char": 0,
        "end_char": len(text),
        "quote": text,
        "source_type": "html",
        "source_id": "unit-alpha",
        "document_id": sha,
        "source_sha256": sha,
        "source_url": "https://example.org/project",
        "source_frame_ags": ["09161000", "09162000"],
        "claim_scope_ags": scope,
        "scope_basis": "SYNTHETIC_FIXTURE",
        "extraction_model": "synthetic-test",
        "prompt_version": "synthetic-test-v1",
        "status": "SYNTHETIC_FIXTURE",
        "modality": "PLANNED" if predicate == "PLANS" else "SOURCE_REPORTED",
        "reference_time": "2024",
        "uncertainty": "Not independent human review",
        "context_evidence_ids": ["evidence-scope"] if context else [],
    }
    with sqlite3.connect(relations) as conn:
        conn.executescript("""CREATE TABLE metadata(key TEXT,value TEXT);
        CREATE TABLE sources(source_sha256 TEXT,source_url TEXT,raw_path TEXT,
          source_type TEXT,source_frame_ags_json TEXT,original_verified INTEGER);
        CREATE TABLE evidence(evidence_id TEXT,source_sha256 TEXT,unit_id TEXT,
          text TEXT,heading TEXT,page_ref TEXT,locator_json TEXT);
        CREATE TABLE entities(entity_id TEXT,label TEXT,entity_type TEXT,identity_basis TEXT);
        CREATE TABLE relations(relation_id TEXT,subject_id TEXT,predicate TEXT,
          object_id TEXT,evidence_id TEXT,record_json TEXT);
        """)
        conn.execute("INSERT INTO metadata VALUES (?,?)", ("schema", "bavaria-relational-v1"))
        conn.execute(
            "INSERT INTO sources VALUES (?,?,?,?,?,?)",
            (
                sha,
                "https://example.org/project",
                str(raw),
                "html",
                json.dumps(["09161000", "09162000"]),
                1,
            ),
        )
        conn.execute(
            "INSERT INTO evidence VALUES (?,?,?,?,?,?,?)",
            ("evidence-alpha", sha, "unit-alpha", text, "Projekt Alpha", None, "{}"),
        )
        if context:
            conn.execute(
                "INSERT INTO evidence VALUES (?,?,?,?,?,?,?)",
                ("evidence-scope", sha, "unit-scope", scope_text, "Geltungsbereich", None, "{}"),
            )
        conn.executemany(
            "INSERT INTO entities VALUES (?,?,?,?)",
            [
                ("actor", actor, "Organization", "SYNTHETIC_FIXTURE"),
                ("project", "Projekt Alpha", "Measure", "SYNTHETIC_FIXTURE"),
            ],
        )
        conn.execute(
            "INSERT INTO relations VALUES (?,?,?,?,?,?)",
            ("relation-alpha", "actor", predicate, "project", "evidence-alpha", json.dumps(record)),
        )
    manifest = root / "manifest.json"
    make_manifest(ledger, relations, manifest)
    return ledger, relations, manifest, raw


class WorkspaceTests(unittest.TestCase):
    def test_company_suffix_does_not_fragment_a_source_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger, relations, manifest, raw = fixture(
                root, ["09161000"], actor="Beispiel GmbH & Co. KG"
            )
            result = answer(
                Workspace(ledger, relations, manifest),
                "Wer fördert Projekt Alpha?",
                root / "runs.jsonl",
                ["09161000"],
            )
            self.assertEqual(result["canonical_answer"]["status"], "answered")
            self.assertIn("Beispiel GmbH & Co KG", result["canonical_answer"]["answer_text"])
            self.assertIn("Beispiel GmbH & Co. KG", raw.read_text())

    def test_provider_failure_is_not_reported_as_a_successful_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger, relations, manifest, _ = fixture(root, ["09161000"])
            with patch(
                "research.bavaria.workspace.core.OllamaGenerationProvider.generate",
                side_effect=ProviderTimeoutError("Synthetic timeout"),
            ):
                result = answer(
                    Workspace(ledger, relations, manifest),
                    "Wer fördert Projekt Alpha?",
                    root / "runs.jsonl",
                    ["09161000"],
                    provider="ollama",
                    model="synthetic-model",
                    ollama_url="http://127.0.0.1:11435",
                )
            self.assertEqual(result["canonical_answer"]["status"], "generation_failed")
            self.assertEqual(result["answerability"], "generation_failed")

    def test_qualified_name_does_not_select_contained_other_city(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger, relations, manifest, _ = fixture(root, ["09161000"])
            workspace = Workspace(ledger, relations, manifest)
            workspace.municipalities["09574140"] = {
                "name": "Neuhaus an der Pegnitz",
                "official_name": "Neuhaus a.d.Pegnitz, M",
            }
            workspace.municipalities["09472175"] = {
                "name": "Pegnitz",
                "official_name": "Pegnitz, St",
            }
            self.assertEqual(
                workspace.resolve_ags(None, "Wärmeplanung Neuhaus/Pegnitz"), ["09574140"]
            )

    def test_scope_context_bundle_cannot_be_silently_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger, relations, manifest, _ = fixture(root, ["09161000"], context=True)
            workspace = Workspace(ledger, relations, manifest)
            too_small = answer(
                workspace, "Wer fördert Projekt Alpha?", root / "runs.jsonl", ["09161000"], k=1
            )
            self.assertEqual(too_small["canonical_answer"]["status"], "insufficient_evidence")
            complete = answer(
                workspace, "Wer fördert Projekt Alpha?", root / "runs.jsonl", ["09161000"], k=2
            )
            self.assertEqual(complete["canonical_answer"]["status"], "answered")
            self.assertEqual(len(complete["canonical_answer"]["citations"]), 2)
            self.assertIn("[C1][C2]", complete["canonical_answer"]["answer_text"])

    def test_real_contract_chain_preserves_source_identity_and_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger, relations, manifest, _ = fixture(root, ["09161000"])
            workspace = Workspace(ledger, relations, manifest)
            output = answer(
                workspace, "Wer fördert Projekt Alpha?", root / "runs.jsonl", ["09161000"]
            )
            result = output["canonical_answer"]
            self.assertEqual(result["status"], "answered")
            self.assertTrue(result["grounding"]["citation_resolution_success"])
            self.assertIn("Stiftung Beispiel", result["answer_text"])
            citation = result["citations"][0]
            self.assertEqual(citation["source_type"], "segment")
            self.assertEqual(citation["source_id"], "unit-alpha")
            self.assertEqual(citation["source_url"], "https://example.org/project")
            self.assertEqual(citation["municipality_ids"], ("09161000", "09162000"))
            events = [json.loads(line) for line in (root / "runs.jsonl").read_text().splitlines()]
            self.assertEqual([r["status"] for r in events], ["pending", "running", "succeeded"])

    def test_source_frame_cannot_supply_unknown_or_foreign_claim_scope(self):
        for scope in (None, ["09161000"]):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ledger, relations, manifest, _ = fixture(root, scope)
                output = answer(
                    Workspace(ledger, relations, manifest),
                    "Wer fördert Projekt Alpha?",
                    root / "runs.jsonl",
                    ["09162000"],
                )
                self.assertEqual(output["canonical_answer"]["status"], "insufficient_evidence")
                self.assertFalse(output["canonical_answer"]["citations"])

    def test_current_status_is_not_inferred_from_old_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger, relations, manifest, _ = fixture(root, ["09161000"])
            output = answer(
                Workspace(ledger, relations, manifest),
                "Wer fördert Projekt Alpha aktuell?",
                root / "runs.jsonl",
                ["09161000"],
            )
            self.assertEqual(output["canonical_answer"]["status"], "insufficient_evidence")
            self.assertTrue(output["question_plan"]["current_status_requested"])

    def test_plan_is_not_reported_as_completed_implementation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger, relations, manifest, _ = fixture(root, ["09161000"], "PLANS")
            output = answer(
                Workspace(ledger, relations, manifest),
                "Ist Projekt Alpha umgesetzt?",
                root / "runs.jsonl",
                ["09161000"],
            )
            self.assertEqual(output["canonical_answer"]["status"], "answered")
            self.assertIn(
                "keine abgeschlossene Umsetzung", output["canonical_answer"]["answer_text"]
            )

    def test_changed_database_is_rejected_before_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger, relations, manifest, _ = fixture(root, ["09161000"])
            with sqlite3.connect(relations) as conn:
                conn.execute("UPDATE relations SET predicate='OPERATES'")
            with self.assertRaisesRegex(ValueError, "Changed or incorrectly selected"):
                Workspace(ledger, relations, manifest)

    def test_tampered_original_is_rejected_before_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger, relations, manifest, raw = fixture(root, ["09161000"])
            raw.write_bytes(b"changed original")
            with self.assertRaisesRegex(ValueError, "Original source changed"):
                answer(
                    Workspace(ledger, relations, manifest),
                    "Wer fördert Projekt Alpha?",
                    root / "runs.jsonl",
                    ["09161000"],
                )

    def test_broad_discovery_is_read_only_and_does_not_claim_an_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger, relations, manifest, _ = fixture(root, ["09161000"])
            workspace = Workspace(ledger, relations, manifest)
            result = workspace.discover("Förderung Projekt Alpha", ["09161000"], 3)
            self.assertEqual(result["status"], "EVIDENCE_DISCOVERY_NOT_A_COMPLETE_POLICY_ANSWER")
            self.assertEqual(result["results"][0]["unit_id"], "unit-alpha")
            self.assertIsNone(result["results"][0]["claim_scope_ags"])
            with self.assertRaisesRegex(ValueError, "AGS outside"):
                workspace.dossiers(["09199999"])


if __name__ == "__main__":
    unittest.main()
