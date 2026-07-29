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

    DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"

    def __init__(self, model_name: str = None, cache_dir: str = None):
        self.model_name = model_name or self.DEFAULT_MODEL
        self.cache_dir = os.path.expanduser(cache_dir or "~/.boss-match/models")
        self._model = None
        self._lock = threading.Lock()

    def _local_model_path(self) -> str | None:
        """Check if model files exist in local manual-download directory."""
        # Manual download layout: cache_dir/manual/<model_slug>/
        slug = self.model_name.replace("/", "_")
        local_dir = os.path.join(self.cache_dir, "manual", slug)
        if os.path.isfile(os.path.join(local_dir, "config.json")):
            return local_dir
        return None

    def ensure_model(self, progress_callback=None) -> None:
        """Load model on first call. Pushes download progress via callback."""
        if self._model is not None:
            return

        with self._lock:
            if self._model is not None:
                return

            os.makedirs(self.cache_dir, exist_ok=True)

            # Prefer local model files if available
            local_path = self._local_model_path()
            if local_path:
                log.info(f"Loading embedding model from local path: {local_path}")
                if progress_callback:
                    progress_callback(0.5, "downloading", 0.0)
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(local_path)
                if progress_callback:
                    progress_callback(1.0, "ready", 0.0)
                log.info(f"Embedding model loaded from local: {local_path}")
                return

            # Fallback: download from HF mirror
            if not os.environ.get("HF_ENDPOINT") and not os.environ.get("HUGGINGFACE_HUB_URL"):
                os.environ["HF_ENDPOINT"] = _DEFAULT_HF_ENDPOINT

            log.info(f"Loading embedding model: {self.model_name}")

            if progress_callback:
                progress_callback(0.1, "downloading", 0.0)

            # Monitor download by checking cache dir size in a side thread
            stop_monitor = threading.Event()
            _prev_size = [0]
            _prev_time = [time.time()]

            def _monitor():
                while not stop_monitor.is_set():
                    try:
                        size = _dir_size(self.cache_dir)
                        now = time.time()
                        dt = now - _prev_time[0]
                        speed = (size - _prev_size[0]) / dt / (1024 * 1024) if dt > 0 else 0.0
                        _prev_size[0] = size
                        _prev_time[0] = now
                        pct = min(0.9, 0.1 + size / (200 * 1024 * 1024) * 0.8)
                        progress_callback(pct, "downloading", max(0.0, speed))
                    except Exception:
                        pass
                    time.sleep(1.0)

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
                progress_callback(1.0, "ready", 0.0)

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
