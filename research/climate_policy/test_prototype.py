"""Targeted source/claim safety and real-snapshot integration checks."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from research.climate_policy import store
from research.climate_policy.answer import analyze


def unit(text):
    return {
        "unit_id": "fixture",
        "text": text,
        "heading_context": "Klimaschutz Förderung",
        "page_ref": None,
        "source_url": "https://fixture.invalid",
        "acquisition_ags": "09263000",
        "raw_hashes": ["0" * 64],
    }


class ExtractionTests(unittest.TestCase):
    def test_money_roles_and_exact_anchors(self):
        u = unit(
            (
                "Die Investitionskosten betrugen 17.716,26 €. "
                "Die Förderung betrug 7.882,00 €. Maximal 30,0"
                "0 Euro pro m2 werden gefördert."
            )
        )
        rows = store.extract(u)
        facts = {(r["field"], r["value"], r["unit"]) for r in rows}
        self.assertIn(("PROJECT_COST", "17716.26", "EUR"), facts)
        self.assertIn(("REPORTED_GRANT", "7882.00", "EUR"), facts)
        self.assertIn(("FUNDING_CAP", "30.00", "EUR_PER_M2"), facts)
        self.assertTrue(all(u["text"][r["start_char"] : r["end_char"]] == r["quote"] for r in rows))
        self.assertTrue(all(r["municipality_ags"] is None for r in rows))

    def test_projected_not_measured_and_unknowns(self):
        rows = store.extract(
            unit("Künftig werden 15.745 kWh und 6,3 Tonnen CO 2 pro Jahr eingespart.")
        )
        values = [r for r in rows if r["field"] == "ENERGY_OR_EMISSION"]
        self.assertEqual(len(values), 2)
        self.assertTrue(
            all(r["role"] == "PROJECTED" and r["measured_outcome"] is None for r in values)
        )
        self.assertTrue(all(r["reference_date"] is None for r in rows))

    def test_validity_has_literal_evidence(self):
        rows = store.extract(
            unit(
                (
                    "Diese Richtlinie tritt am 01.10.2023 in Kraft"
                    " und ist bis zum 31.12.2026 befristet."
                )
            )
        )
        self.assertEqual(
            next(r["value"] for r in rows if r["field"] == "VALIDITY_END"), "31.12.2026"
        )
        self.assertEqual(
            next(r["validity_start"] for r in rows if r["field"] == "VALIDITY_START"), "01.10.2023"
        )

    def test_no_copy_from_gold_and_no_implicit_database(self):
        with self.assertRaises(FileNotFoundError):
            store.connect("/tmp/absent-klima-snapshot-do-not-create.sqlite")
        self.assertNotIn("candidates.json", Path(store.__file__).read_text())


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "evidence.sqlite"
        cls.receipt = store.build(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_four_municipalities_and_cache(self):
        with store.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM sources").fetchone()[0], 57)
            self.assertEqual(conn.execute("SELECT count(*) FROM evidence").fetchone()[0], 894)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM claims")
        self.assertTrue(store.build(self.db)["cached"])

    def test_actual_grant_answer_has_provenance(self):
        response = analyze(
            self.db,
            "Welche Förderquote und Obergrenzen nennt Straubing für Begrünung und Entsiegelung?",
        )
        self.assertTrue(
            any(c["field"] == "FUNDING_CAP" and c["value"] == "2000.00" for c in response["claims"])
        )
        self.assertFalse(
            any(c["field"] == "PROJECT_COST" and c["value"] == "410000" for c in response["claims"])
        )
        self.assertTrue(all(len(c["source_sha256"]) == 64 for c in response["citations"]))
        self.assertEqual(response["api_cost_eur"], 0)

    def test_real_forecast_not_claimed_measurement(self):
        response = analyze(self.db, "Ist die Energieeinsparung in Buttenheim tatsächlich gemessen?")
        self.assertTrue(any("keine ausdrücklich gemessene" in w for w in response["warnings"]))
        self.assertFalse(
            any(c["role"] == "EXPLICIT_MEASUREMENT_REPORT" for c in response["claims"])
        )

    def test_modified_database_evidence_fails_canonical_source_check(self):
        from research.offline_evaluation.contracts import ValidationError

        other = Path(self.tmp.name) / "modified.sqlite"
        with store.connect(self.db) as source, sqlite3.connect(other) as target:
            source.backup(target)
            raw = target.execute(
                "SELECT source_sha256 FROM evidence JOIN claims USING(unit_id) "
                "WHERE field='FUNDING_CAP' LIMIT 1"
            ).fetchone()[0]
            target.execute(
                "UPDATE evidence SET text='tampered' WHERE unit_id IN "
                "(SELECT unit_id FROM claims WHERE field='FUNDING_CAP')"
            )
        with self.assertRaises(ValidationError):
            analyze(
                other, "Welche Förderobergrenzen nennt Straubing?", "sql", allowed_sources={raw}
            )

    def test_baywa_does_not_mix_other_stations(self):
        r = analyze(self.db, "Welche Ladepunkte und Schnellladesäulen nennt Rosenheim für BayWa?")
        self.assertTrue(
            any(c["field"] == "CHARGING_POINTS" and c["value"] == "16" for c in r["claims"])
        )
        self.assertFalse(any(c["field"] == "BICYCLE_SPACES" for c in r["claims"]))


if __name__ == "__main__":
    unittest.main()
