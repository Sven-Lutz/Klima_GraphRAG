"""Isolated synthetic vector fixtures; never real human relevance or gold labels."""

import copy
import hashlib
import json

import numpy as np
import pytest

from crawler.web.semantic_index import DIMENSIONS, SemanticIndex


class TechnicalWorkspace:
    def __init__(self, folder, count=3):
        self.db = folder / "source.sqlite"
        self.db.write_bytes(b"SYNTHETIC_SOURCE_NOT_A_REAL_DATABASE")
        self.manifest_path = folder / "workspace.json"
        self.manifest_path.write_text('{"technical": true}')
        self.source_databases = []
        self.snapshot_id = "synthetic-semantic-snapshot"
        self.municipalities = {
            "09162000": {"name": "SYNTHETIC A"},
            "09163000": {"name": "SYNTHETIC B"},
        }
        self.rows = [
            dict(
                unit_id=f"synthetic-{i:04d}",
                sha256=hashlib.sha256(str(i).encode()).hexdigest(),
                text="SYNTHETIC heat support" if i < 2 else f"SYNTHETIC flood {i}",
                heading=None,
                source_url="https://example.test/technical-fixture",
                frame_ags="09162000" if i % 2 == 0 else "09163000",
                page=None,
                ordinal=i,
                kind="html",
            )
            for i in range(count)
        ]

    def resolve_ags(self, ags, question):
        selected = list(ags or [])
        if set(selected) - self.municipalities.keys():
            raise ValueError("Unknown municipality scope")
        return selected

    def _unit_rows(self, ags, unit_ids=None):
        for row in self.rows:
            if (not ags or row["frame_ags"] in ags) and (
                unit_ids is None or row["unit_id"] in unit_ids
            ):
                yield dict(row)


class TechnicalEncoder:
    def __init__(self):
        self.calls = []
        self.fail_call = None

    def encode(self, texts):
        self.calls.append(list(texts))
        if self.fail_call == len(self.calls):
            raise RuntimeError("SYNTHETIC_ENCODER_INTERRUPTION")
        result = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        for i, text in enumerate(texts):
            result[i, 0 if any(s in text.lower() for s in ["heat", "warm"]) else 1] = 1
        return result


@pytest.fixture
def setup(tmp_path):
    workspace = TechnicalWorkspace(tmp_path)
    model = tmp_path / "model"
    model.mkdir()
    (model / "manifest.json").write_text(json.dumps({"synthetic": True, "files": {}}))
    encoder = TechnicalEncoder()
    index = SemanticIndex(workspace, tmp_path / "semantic", model, encoder=encoder)
    return workspace, model, encoder, index


def test_dedup_real_source_identity_scope_and_deterministic_exact_cosine(setup):
    workspace, model, encoder, index = setup
    assert index.status()["state"] == "NOT_BUILT"
    assert encoder.calls == []
    status = index.build()
    assert status["complete"] and status["units"] == 3 and status["unique_vectors"] == 2
    assert status["encoded_vectors"] == 2 and sum(map(len, encoder.calls)) == 2
    a = index.discover("SYNTHETIC warm", ["09162000"], 2)
    assert [r["unit_id"] for r in a["results"]] == ["synthetic-0000", "synthetic-0002"]
    assert all(r["source_frame_ags"] == ["09162000"] for r in a["results"])
    assert a["results"][0]["source_id"] == "synthetic-0000"
    assert a["results"][0]["claim_scope_ags"] is None
    assert a["results"][0]["text"] == workspace.rows[0]["text"]
    assert a["diagnostics"]["partial_index"] is False
    b = index.discover("SYNTHETIC warm", None, 3)
    assert [r["unit_id"] for r in b["results"]] == [r["unit_id"] for r in workspace.rows]
    assert b["results"][0]["score"] == b["results"][1]["score"] == 1
    assert b["results"][2]["score"] == 0
    assert b["results"] == index.discover("SYNTHETIC warm", None, 3)["results"]
    index.close()
    reopened = SemanticIndex(workspace, index.path, model, encoder=TechnicalEncoder())
    assert reopened.status()["complete"]
    assert reopened.discover("SYNTHETIC warm", None, 3)["results"] == b["results"]
    reopened.close()


def test_checkpoint_resume_never_serves_partial_or_reencodes_completed_vectors(setup):
    workspace, _, encoder, index = setup
    workspace.rows += [
        {**workspace.rows[2], "unit_id": f"extra-{i}", "text": f"SYNTHETIC unique flood{i}"}
        for i in range(130)
    ]
    assert index.build(max_batches=0)["encoded_vectors"] == 0
    with pytest.raises(ValueError, match="incomplete"):
        index.discover("SYNTHETIC heat")
    encoder.fail_call = 2
    with pytest.raises(RuntimeError, match="INTERRUPTION"):
        index.build()
    assert index.status()["encoded_vectors"] == 64
    first_texts = encoder.calls[0]
    encoder.fail_call = None
    before = len(encoder.calls)
    status = index.build()
    assert status["complete"] and status["encoded_vectors"] == 132
    assert not any(text in first_texts for call in encoder.calls[before:] for text in call)
    assert len({text for call in encoder.calls[before:] for text in call}) == 68
    index.close()


def test_snapshot_model_vector_corruption_and_changed_source_fail_closed(setup):
    workspace, model, _, index = setup
    index.build()
    wrong = copy.copy(workspace)
    wrong.snapshot_id = "another-snapshot"
    with pytest.raises(ValueError, match="another snapshot"):
        SemanticIndex(wrong, index.path, model)
    (model / "manifest.json").write_text('{"different": true}')
    with pytest.raises(ValueError, match="manifest changed"):
        index.discover("SYNTHETIC heat")
    with pytest.raises(ValueError, match="another snapshot/model"):
        SemanticIndex(workspace, index.path, model)
    (model / "manifest.json").write_text(json.dumps({"synthetic": True, "files": {}}))
    index.close()
    with index.vector_path.open("r+b") as stream:
        stream.write(b"CORRUPTED")
    with pytest.raises(ValueError, match="integrity/coverage"):
        SemanticIndex(workspace, index.path, model)
    workspace.db.write_bytes(b"CHANGED_SYNTHETIC_SOURCE")
    with pytest.raises(ValueError, match="snapshot changed"):
        index.build()


def test_resource_gate_and_exclusive_writer_are_real_and_preserve_partial(setup, monkeypatch):
    import fcntl
    import shutil

    workspace, model, encoder, index = setup
    usage = shutil.disk_usage(index.path.parent)
    monkeypatch.setattr(
        "crawler.web.semantic_index.shutil.disk_usage", lambda _: usage._replace(free=1)
    )
    with pytest.raises(ValueError, match="reserve plus bounded"):
        index.build()
    assert not index.database.exists() and not encoder.calls
    monkeypatch.undo()
    index.path.mkdir(exist_ok=True)
    with (index.path / "writer.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="active writer"):
            index.build()
    assert not index.database.exists()
    assert index.build()["complete"]
    index.close()


def test_bad_vectors_empty_scopes_and_parameters_are_explicit(setup):
    workspace, _, encoder, index = setup
    original = encoder.encode
    encoder.encode = lambda texts: np.zeros((len(texts), DIMENSIONS))
    with pytest.raises(ValueError, match="zero semantic vector"):
        index.build()
    assert not index.status()["complete"] and index.status()["encoded_vectors"] == 0
    encoder.encode = original
    index.build()
    with pytest.raises(ValueError, match="Unknown municipality"):
        index.discover("SYNTHETIC heat", ["00000000"])
    for question, k in [("", 8), ("SYNTHETIC heat", True), ("SYNTHETIC heat", 21)]:
        with pytest.raises(ValueError):
            index.discover(question, None, k)
    workspace.municipalities["09164000"] = {"name": "SYNTHETIC EMPTY"}
    assert index.discover("SYNTHETIC heat", ["09164000"])["results"] == []
    with index.conn() as c:
        with pytest.raises(Exception, match="readonly"):
            c.execute("DELETE FROM units")
    assert workspace.rows[0]["text"].encode() not in index.database.read_bytes()
    index.close()


def test_empty_corpus_completes_without_encoder_and_runtime_mutation_is_rejected(setup):
    workspace, model, encoder, index = setup
    workspace.rows = []
    assert index.build()["complete"]
    assert not encoder.calls
    assert index.discover("SYNTHETIC heat")["results"] == []
    index.close()
    reopened = SemanticIndex(workspace, index.path, model, encoder=encoder)
    assert reopened.status()["units"] == 0
    assert reopened.discover("SYNTHETIC heat")["results"] == []
    with reopened.conn(write=True) as c:
        c.execute("INSERT INTO metadata VALUES ('SYNTHETIC_MUTATION','true')")
    with pytest.raises(ValueError, match="Completed semantic index changed"):
        reopened.discover("SYNTHETIC heat")
    reopened.close()


def test_new_snapshot_explicitly_reuses_only_verified_identical_input_vectors(setup, tmp_path):
    workspace, model, _, original = setup
    original.build()
    vector_bytes = original.vector_path.read_bytes()
    old_database = original.database.read_bytes()
    newer = copy.copy(workspace)
    newer.snapshot_id = "synthetic-expanded-snapshot"
    newer.rows = [dict(row) for row in workspace.rows] + [
        {**workspace.rows[0], "unit_id": "new-same-text"},
        {**workspace.rows[0], "unit_id": "new-input", "text": "SYNTHETIC warm policy newtext"},
    ]
    encoder = TechnicalEncoder()
    index = SemanticIndex(newer, tmp_path / "expanded", model, encoder=encoder)
    seeded = index.seed_from(original.path)
    assert seeded["units"] == 5 and seeded["unique_vectors"] == 3
    assert seeded["seeded_vectors"] == seeded["encoded_vectors"] == 2
    assert not seeded["complete"] and not encoder.calls
    assert seeded["binding"]["snapshot"] == newer.snapshot_id
    assert seeded["seed_parent"]["binding"]["snapshot"] == workspace.snapshot_id
    assert index.seed_from(original.path)["seeded_vectors"] == 2  # Idempotent resume.
    assert index.build()["complete"] and sum(map(len, encoder.calls)) == 1
    assert original.vector_path.read_bytes() == vector_bytes
    assert original.database.read_bytes() == old_database
    assert index.discover("SYNTHETIC warm", None, 8)["snapshot_id"] == newer.snapshot_id
    with pytest.raises(ValueError, match="different output"):
        original.seed_from(original.path)
    with pytest.raises(ValueError, match="immutable"):
        index.seed_from(original.path)
    original.close()
    index.close()


def test_incomplete_or_incompatible_or_corrupt_seed_never_creates_target(setup, tmp_path):
    workspace, model, _, original = setup
    original.build(max_batches=0)
    target = SemanticIndex(workspace, tmp_path / "target", model, encoder=TechnicalEncoder())
    with pytest.raises(ValueError, match="parent is incomplete"):
        target.seed_from(original.path)
    assert not target.path.exists()
    original.build()
    incompatible_model = tmp_path / "different-model"
    incompatible_model.mkdir()
    (incompatible_model / "manifest.json").write_text('{"different": true}')
    incompatible = SemanticIndex(workspace, tmp_path / "incompatible", incompatible_model)
    with pytest.raises(ValueError, match="binding mismatch"):
        incompatible.seed_from(original.path)
    assert not incompatible.path.exists()
    original.close()
    with original.vector_path.open("r+b") as stream:
        stream.write(b"CORRUPTED_SEED")
    with pytest.raises(ValueError, match="integrity failure"):
        target.seed_from(original.path)
    assert not target.path.exists()
