"""Synthetic SQLite only. No real-world claims in fixtures."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from crawler.core.policy_snapshot_provenance import ProvenanceError, open_snapshot, read_source
from crawler.scripts.reviewed_policy_answer import prepare_packet, adjudicate_packet

AGS = "09462000"  # Test identifier, all source claims entirely fictional.
OTHER = "09362000"
POLICY = "fictional_policy_grant"
TEXT = "Fiktives Gemeindeblatt: Ein fiktiver Zuschuss wird angeboten."
URL = "https://example.invalid/fictional-program"


class QAIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "pilot.sqlite"
        con = sqlite3.connect(self.db)
        con.executescript("""
          CREATE TABLE documents_raw(document_id TEXT PRIMARY KEY, url_canonical TEXT);
          CREATE TABLE municipality_documents(municipality_id TEXT, document_id TEXT);
          CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY, document_id TEXT, text TEXT, chunk_hash TEXT);
        """)
        con.execute("INSERT INTO documents_raw VALUES(?,?)", ("doc1", URL))
        con.execute("INSERT INTO municipality_documents VALUES(?,?)", (AGS, "doc1"))
        con.execute("INSERT INTO chunks VALUES(?,?,?,?)", ("chunk1", "doc1", TEXT,
                    sha256(TEXT.encode()).hexdigest()))
        con.commit()
        con.close()

    def retrieval(self, **change):
        item = {"evidence_id":"ev1", "source_type":"chunk", "source_id":"chunk1",
                "chunk_id":"chunk1", "evidence_text":TEXT,
                "document":{"document_id":"doc1", "source_url":URL,
                            "municipality_ids":[AGS]}}
        item.update(change)
        return {"items":[item]}

    def packet(self, **change):
        pkt = prepare_packet(self.retrieval(**change), self.db,
                             question_id="question_001", municipality_id=AGS,
                             policy_id=POLICY, as_of="2026-11-03")
        return pkt

    def mark_reviewed(self, packet, claim="offers", **changes):
        review = packet["items"][0]["review"]
        review.update({"verified":True, "reviewer":"Synthetic reviewer (TEST ONLY)",
                       "reviewed_at":"2026-11-03", "issuer_ags":AGS,
                       "authority":"municipality", "claim":claim,
                       "quote":"Ein fiktiver Zuschuss wird angeboten.",
                       "published_at":"2026-10-01"})
        review.update(changes)
        return packet

    def test_unknown_before_review(self):
        p = self.packet()
        self.assertEqual(len(p["items"]),1)
        self.assertEqual(adjudicate_packet(p,self.db)["decision"]["label"], "unknown")

    def test_valid_reviewed_yes(self):
        p = self.mark_reviewed(self.packet())
        answer = adjudicate_packet(p, self.db)
        self.assertEqual(answer["decision"]["label"],"yes")
        self.assertEqual(answer["decision"]["citations"][0]["url"],URL)

    def test_valid_reviewed_no_requires_explicit_quote(self):
        # The synthetic document doesn't actually say the policy is absent.
        # Reviewer semantics must still be checked by a human; provenance alone
        # cannot detect an incorrect reviewer claim even if the quote is real.
        p = self.mark_reviewed(self.packet(),claim="explicitly_not_offered")
        self.assertEqual(adjudicate_packet(p,self.db)["decision"]["label"],"no")

    def test_wrong_city_fails(self):
        with self.assertRaisesRegex(ProvenanceError, "NOT linked"):
            prepare_packet(self.retrieval(),self.db,question_id="q",municipality_id=OTHER,
                           policy_id=POLICY,as_of="2026-11-03")

    def test_retrieval_municipality_tag_missing_fails(self):
        item=self.retrieval()["items"][0]
        item["document"]["municipality_ids"]=[OTHER]
        with self.assertRaisesRegex(ProvenanceError, "municipality tags mismatch"):
            prepare_packet({"items":[item]}, self.db, question_id="q",
                           municipality_id=AGS,policy_id=POLICY,as_of="2026-11-03")

    def test_spoofed_url_fails(self):
        with self.assertRaisesRegex(ProvenanceError,"URL mismatch"):
            self.packet(document={"document_id":"doc1", "source_url":"https://attacker.invalid", "municipality_ids":[AGS]})

    def test_spoofed_chunk_text_fails(self):
        with self.assertRaisesRegex(ProvenanceError, "text mismatch"):
            self.packet(evidence_text="The municipality definitely funds everything")

    def test_changed_database_after_review_fails(self):
        packet = self.packet()
        con=sqlite3.connect(self.db)
        updated="Dokument wurde nach dem Review geändert."
        con.execute("UPDATE chunks SET text=?, chunk_hash=? WHERE chunk_id='chunk1'",(updated,sha256(updated.encode()).hexdigest()))
        con.commit(); con.close()
        with self.assertRaisesRegex(ProvenanceError,"source does not match snapshot"):
            adjudicate_packet(packet,self.db)

    def test_corrupt_chunk_hash_rejected(self):
        con=sqlite3.connect(self.db)
        con.execute("UPDATE chunks SET chunk_hash='WRONG' WHERE chunk_id='chunk1'")
        con.commit(); con.close()
        with self.assertRaisesRegex(ProvenanceError,"hash mismatch"):
            self.packet()

    def test_quote_not_in_source_fails(self):
        p=self.mark_reviewed(self.packet(),quote="No matching invented quote")
        with self.assertRaisesRegex(ValueError,"quote is not contained"):
            adjudicate_packet(p,self.db)

    def test_verification_requires_reviewer(self):
        p=self.mark_reviewed(self.packet(),reviewer=None)
        with self.assertRaisesRegex(ProvenanceError,"lacks reviewer"):
            adjudicate_packet(p,self.db)

    def test_verification_false_ignored(self):
        p=self.packet()
        p["items"][0]["review"]["claim"]="offers"
        self.assertEqual(adjudicate_packet(p,self.db)["decision"]["label"],"unknown")

    def test_nonboolean_verified_rejected(self):
        p=self.packet()
        p["items"][0]["review"]["verified"]="true"
        with self.assertRaisesRegex(ProvenanceError,"must be a boolean"):
            adjudicate_packet(p,self.db)

    def test_wrong_issuer_unknown(self):
        p=self.mark_reviewed(self.packet(),issuer_ags=OTHER)
        self.assertEqual(adjudicate_packet(p,self.db)["decision"]["label"],"unknown")

    def test_planned_unknown(self):
        p=self.mark_reviewed(self.packet(),claim="planned")
        self.assertEqual(adjudicate_packet(p,self.db)["decision"]["label"],"unknown")

    def test_conflict(self):
        con=sqlite3.connect(self.db)
        con.execute("INSERT INTO chunks VALUES(?,?,?,?)", ("chunk2", "doc1",TEXT,sha256(TEXT.encode()).hexdigest()))
        con.commit();con.close()
        retrieval = self.retrieval()
        second = json.loads(json.dumps(retrieval["items"][0]))
        second.update({"evidence_id":"ev2", "chunk_id":"chunk2", "source_id":"chunk2"})
        retrieval["items"].append(second)
        p=prepare_packet(retrieval,self.db,question_id="question_001",municipality_id=AGS,policy_id=POLICY,as_of="2026-11-03")
        self.mark_reviewed(p)
        p["items"][1]["review"].update(p["items"][0]["review"])
        p["items"][1]["review"]["claim"]="explicitly_not_offered"
        self.assertEqual(adjudicate_packet(p,self.db)["decision"]["label"],"conflict")

    def test_duplicate_chunk_rejected(self):
        p=self.packet()
        p["items"].append(json.loads(json.dumps(p["items"][0])))
        p["items"][1]["evidence_id"]="another_id"
        with self.assertRaisesRegex(ProvenanceError,"Duplicate chunk"):
            adjudicate_packet(p,self.db)

    def test_malformed_db_fail_closed(self):
        con=sqlite3.connect(self.db)
        con.execute("DROP TABLE municipality_documents")
        con.commit(); con.close()
        with self.assertRaisesRegex(ProvenanceError,"missing provenance tables"):
            self.packet()

    def test_nonchunk_graph_assertion_skipped(self):
        p=prepare_packet({"items":[{"evidence_id":"graph1", "source_type":"assertion",
                                     "evidence_text":"Fictional edge"}]},self.db,
                         question_id="q",municipality_id=AGS,policy_id=POLICY,as_of="2026-11-03")
        self.assertEqual(p["items"],[])
        self.assertEqual(len(p["skipped_retrieval"]),1)

    def test_duplicate_evidence_ids_fails(self):
        item=self.retrieval()["items"][0]
        with self.assertRaisesRegex(ProvenanceError,"Duplicate retrieval evidence_id"):
            prepare_packet({"items":[item,item]},self.db,question_id="q",
                           municipality_id=AGS,policy_id=POLICY,as_of="2026-11-03")

    def test_invalid_review_date_fails(self):
        p=self.mark_reviewed(self.packet(),reviewed_at="today")
        with self.assertRaisesRegex(ProvenanceError,"Invalid review date"):
            adjudicate_packet(p,self.db)


if __name__ == "__main__":
    unittest.main()
