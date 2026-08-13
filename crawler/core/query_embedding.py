"""Lazy local query-embedding adapter with explicit canonical lineage."""

from __future__ import annotations

import numpy as np

from crawler.core.retrieval import EmbeddingIdentity


class SentenceTransformerQueryEmbedder:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        model_revision: str,
        default_model_revision: str,
        vector_dimension: int,
        preprocessing_id: str,
    ):
        self.identity = EmbeddingIdentity(
            provider=provider,
            model=model,
            model_revision=model_revision,
            vector_dimension=vector_dimension,
            preprocessing_id=preprocessing_id,
        )
        self._default_model_revision = default_model_revision
        self._model = None

    def embed_query(self, text: str) -> np.ndarray:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            revision = (
                {}
                if self.identity.model_revision == self._default_model_revision
                else {"revision": self.identity.model_revision}
            )
            self._model = SentenceTransformer(self.identity.model, **revision)
            self._model.max_seq_length = 256
        vector = self._model.encode([text], normalize_embeddings=True, convert_to_numpy=True)[0]
        return np.asarray(vector, dtype=np.float32)
