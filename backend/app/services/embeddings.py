from threading import Lock

from app.core.config import Settings


class EmbeddingService:
    def __init__(self, settings: Settings):
        self.model_name = settings.embedding_model
        self._model = None
        self._lock = Lock()

    def _get_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    try:
                        from sentence_transformers import SentenceTransformer

                        self._model = SentenceTransformer(self.model_name)
                    except Exception as exc:
                        raise RuntimeError(
                            f"Could not load embedding model '{self.model_name}': {exc}"
                        ) from exc
        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._get_model().encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_query(self, query: str) -> list[float]:
        return self.embed_texts([query])[0]
