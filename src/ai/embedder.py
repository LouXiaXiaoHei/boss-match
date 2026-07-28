"""Local embedding model wrapper using sentence-transformers."""

import logging
import os
import threading
import time

log = logging.getLogger(__name__)

# Default HF mirror for China users (set HF_ENDPOINT env to override)
_DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"


class Embedder:
    """Embedder with lazy model loading and download progress events."""

    DEFAULT_MODEL = "BAAI/bge-small-zh-v1.1"

    def __init__(self, model_name: str = None, cache_dir: str = None):
        self.model_name = model_name or self.DEFAULT_MODEL
        self.cache_dir = os.path.expanduser(cache_dir or "~/.boss-match/models")
        self._model = None
        self._lock = threading.Lock()

    def ensure_model(self, progress_callback=None) -> None:
        """Load model on first call. Pushes download progress via callback."""
        if self._model is not None:
            return

        with self._lock:
            if self._model is not None:
                return

            os.makedirs(self.cache_dir, exist_ok=True)

            # Set HF endpoint for China if not already set
            if not os.environ.get("HF_ENDPOINT") and not os.environ.get("HUGGINGFACE_HUB_URL"):
                os.environ["HF_ENDPOINT"] = _DEFAULT_HF_ENDPOINT

            log.info(f"Loading embedding model: {self.model_name}")

            if progress_callback:
                progress_callback(0.1, "downloading")

            # Monitor download by checking cache dir size in a side thread
            stop_monitor = threading.Event()

            def _monitor():
                while not stop_monitor.is_set():
                    try:
                        size = _dir_size(self.cache_dir)
                        pct = min(0.9, 0.1 + size / (200 * 1024 * 1024) * 0.8)
                        progress_callback(pct, "downloading")
                    except Exception:
                        pass
                    time.sleep(0.5)

            t = threading.Thread(target=_monitor, daemon=True)
            t.start()

            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(
                    self.model_name,
                    cache_folder=self.cache_dir,
                )
            finally:
                stop_monitor.set()

            if progress_callback:
                progress_callback(1.0, "ready")

            log.info(f"Embedding model loaded: {self.model_name}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Batch embed texts. Returns list of normalized vectors."""
        if not texts:
            return []
        self.ensure_model()
        embeddings = self._model.encode(texts, normalize_embeddings=True)
        return [emb.tolist() for emb in embeddings]

    def embed_one(self, text: str) -> list[float]:
        if not text:
            return []
        result = self.embed([text])
        return result[0] if result else []


def _dir_size(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total
