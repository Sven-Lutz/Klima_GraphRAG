"""Mathematical regression against the pre-optimization committed discover function."""

import ast
import collections
import copy
import heapq
import math
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.bavaria.workspace.core import Workspace, roots


@pytest.mark.parametrize(
    "query", ["Wärmeplanung", "Solar Förderung", "Klimaschutz 2024", "fehlend", "Solar Solar Wärme"]
)
def test_single_tokenization_preserves_corpus_statistics_ranks_and_exact_scores(
    query, monkeypatch, tmp_path
):
    # Read the immutable historical implementation, not a duplicate research dataset.
    # Other integration fixtures deliberately execute from an isolated directory.
    monkeypatch.chdir(tmp_path)
    source = subprocess.check_output(
        ["git", "show", "b84db20:research/bavaria/workspace/core.py"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    )
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Workspace")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "discover")
    namespace = {"Counter": collections.Counter, "roots": roots, "math": math, "heapq": heapq}
    exec(
        compile(ast.Module(body=[method], type_ignores=[]), "<historical-discover>", "exec"),
        namespace,
    )
    rows = [
        {
            "unit_id": f"{i:04}",
            "sha256": "a" * 64,
            "heading": "Förderung" if i % 3 == 0 else None,
            "source_url": "https://example.org/klima",
            "text": ("Wärmeplanung Solar 2024 " * (i % 4 + 1))
            if i % 2
            else "Klimaschutz Förderung",
            "frame_ags": "09162111",
            "ordinal": i,
            "kind": "paragraph",
            "page_ref": None,
        }
        for i in range(25)
    ]

    def unit_rows(ags, unit_ids=None):
        return iter(
            copy.deepcopy([r for r in rows if unit_ids is None or r["unit_id"] in unit_ids])
        )

    w = SimpleNamespace(
        resolve_ags=lambda *args: ["09162111"],
        municipalities={"09162111": {"name": "Technische Gemeinde"}},
        _unit_rows=unit_rows,
        db="technical-only",
        source_databases=[],
        snapshot_id="technical-snapshot",
    )
    expected = namespace["discover"](w, query, k=8)
    actual = Workspace.discover(w, query, k=8)
    assert actual["results"] == expected["results"]
    assert actual["candidate_units"] == expected["candidate_units"]
    assert actual["query_terms"] == expected["query_terms"]
    assert actual["candidate_workset_bytes"] < 64 * 1024**2
