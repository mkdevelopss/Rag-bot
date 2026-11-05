from threading import Lock

from app.core.config import Settings


class EmbeddingService:
    def __init__(self, settings: Settings):
        self.model_name = settings.embedding_model
        self.query_prefix = settings.embedding_query_prefix
        self.passage_prefix = settings.embedding_passage_prefix
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

    def _encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._get_model().encode(
            texts,
            batch_size=32,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self._encode([self.passage_prefix + text for text in texts])

    def embed_query(self, query: str) -> list[float]:
        return self._encode([self.query_prefix + query])[0]

    def warm_up(self) -> None:
        self._get_model()
