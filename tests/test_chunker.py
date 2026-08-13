"""Tests für crawler/core/segmentation/chunker.py — Token-Chunking (Stage 1)."""

import pytest

from crawler.core.segmentation import chunker
from crawler.core.segmentation.chunker import chunk_text, count_tokens


class _DeterministicEncoding:
    """Lossless one-code-point-per-token encoding for offline unit tests."""

    @staticmethod
    def encode(text: str) -> list[int]:
        return [ord(character) for character in text]

    @staticmethod
    def decode(tokens: list[int]) -> str:
        return "".join(chr(token) for token in tokens)


ENC = _DeterministicEncoding()


@pytest.fixture(autouse=True)
def _offline_encoding(monkeypatch):
    monkeypatch.setattr(chunker, "_get_encoding", lambda _name: ENC)


# Genug Text für mehrere Chunks bei kleinen Fenstern
LONG_TEXT = (
    "Die Stadt Augsburg fördert den Ausbau von Photovoltaikanlagen auf "
    "kommunalen Dächern. Das Klimaschutzkonzept sieht eine CO2-Reduktion "
    "von 50 Prozent bis 2030 vor. "
) * 40


def test_empty_input_returns_empty_list():
    assert chunk_text("") == []
    assert chunk_text("   \n\t  ") == []


def test_short_text_yields_single_chunk():
    text = "Die Stadt Regensburg baut ein Wärmenetz."
    chunks = chunk_text(text, chunk_size=600, overlap=60)
    assert len(chunks) == 1
    c = chunks[0]
    assert c.chunk_index == 0
    assert c.start_token == 0
    assert c.text == text
    assert c.end_token == count_tokens(text)


def test_chunks_respect_size_limit():
    for c in chunk_text(LONG_TEXT, chunk_size=100, overlap=20):
        assert c.end_token - c.start_token <= 100


def test_overlap_between_consecutive_chunks():
    chunks = chunk_text(LONG_TEXT, chunk_size=100, overlap=20)
    assert len(chunks) >= 3
    tokens = ENC.encode(LONG_TEXT)
    for prev, nxt in zip(chunks, chunks[1:]):
        # Stride = size - overlap
        assert nxt.start_token == prev.start_token + 80
        # Die letzten 20 Tokens des vorherigen == die ersten 20 des nächsten
        assert (
            tokens[prev.end_token - 20 : prev.end_token]
            == tokens[nxt.start_token : nxt.start_token + 20]
        )


def test_full_coverage_without_gaps():
    chunks = chunk_text(LONG_TEXT, chunk_size=100, overlap=20)
    total = len(ENC.encode(LONG_TEXT))
    assert chunks[0].start_token == 0
    assert chunks[-1].end_token == total
    # Kein Loch zwischen Fenstern: jeder Start liegt vor dem vorherigen Ende
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.start_token < prev.end_token


def test_chunk_indices_are_sequential():
    chunks = chunk_text(LONG_TEXT, chunk_size=100, overlap=20)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_overlap_geq_chunk_size_raises():
    with pytest.raises(ValueError):
        chunk_text(LONG_TEXT, chunk_size=50, overlap=50)
    with pytest.raises(ValueError):
        chunk_text(LONG_TEXT, chunk_size=50, overlap=60)


def test_count_tokens():
    assert count_tokens("") == 0
    assert count_tokens(None) == 0
    text = "Klimaschutz in Bayern"
    assert count_tokens(text) == len(ENC.encode(text))
