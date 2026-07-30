"""ChromaDB vector store wrapper for RAG retrieval."""

import logging
import os
from dataclasses import dataclass, field

import chromadb

from src.ai.chunker import Chunk

log = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    chunk_id: str
    text: str
    source: str
    section: str
    score: float
    metadata: dict = field(default_factory=dict)


class VectorStore:
    """Manages resume, supplement, and jobs collections in ChromaDB."""

    def __init__(self, persist_dir: str = None):
        self.persist_dir = os.path.expanduser(persist_dir or "~/.boss-match/chromadb")
        os.makedirs(self.persist_dir, exist_ok=True)
        self._client = chromadb.PersistentClient(path=self.persist_dir)
        self._resume_col = self._client.get_or_create_collection(
            "resume", metadata={"hnsw:space": "cosine"})
        self._supplement_col = self._client.get_or_create_collection(
            "supplement", metadata={"hnsw:space": "cosine"})
        self._jobs_col = self._client.get_or_create_collection(
            "jobs", metadata={"hnsw:space": "cosine"})

    def _col(self, source: str):
        if source == "resume":
            return self._resume_col
        if source == "supplement":
            return self._supplement_col
        return self._jobs_col

    def upsert_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]):
        """Upsert chunks into the appropriate collection based on source."""
        if not chunks:
            return
        # Group by source
        by_source: dict[str, list[tuple[Chunk, list[float]]]] = {}
        for chunk, emb in zip(chunks, embeddings):
            by_source.setdefault(chunk.source, []).append((chunk, emb))

        for source, items in by_source.items():
            col = self._col(source)
            ids = [c.id for c, _ in items]
            docs = [c.text for c, _ in items]
            embs = [emb for _, emb in items]
            metas = []
            for c, _ in items:
                meta = {"source": c.source, "section": c.section}
                if c.job_id:
                    meta["job_id"] = c.job_id
                if c.resume_id:
                    meta["resume_id"] = c.resume_id
                meta.update(c.metadata)
                metas.append(meta)
            col.upsert(ids=ids, documents=docs, embeddings=embs, metadatas=metas)

    def query(self, query_embedding: list[float], source: str = None,
              top_k: int = 5, resume_id: str = None) -> list[RetrievalResult]:
        """Query a single collection."""
        if source:
            cols = [self._col(source)]
        else:
            cols = [self._resume_col, self._supplement_col, self._jobs_col]
        return self._query_multi(query_embedding, cols, top_k, resume_id=resume_id)

    def query_multi(self, query_embedding: list[float],
                    sources: list[str] = None,
                    top_k: int = 5, resume_id: str = None) -> list[RetrievalResult]:
        """Query multiple collections, merge and rank by score."""
        if sources:
            cols = [self._col(s) for s in sources]
        else:
            cols = [self._resume_col, self._supplement_col]
        return self._query_multi(query_embedding, cols, top_k, resume_id=resume_id)

    def _query_multi(self, query_embedding: list[float],
                     cols: list, top_k: int = 5,
                     resume_id: str = None) -> list[RetrievalResult]:
        all_results = []
        for col in cols:
            if col.count() == 0:
                continue
            try:
                kwargs = {
                    "query_embeddings": [query_embedding],
                    "n_results": min(top_k, col.count()),
                    "include": ["documents", "metadatas", "distances"],
                }
                if resume_id:
                    kwargs["where"] = {"resume_id": resume_id}
                r = col.query(**kwargs)
                for i in range(len(r["ids"][0])):
                    meta = r["metadatas"][0][i] or {}
                    # ChromaDB cosine distance: 0 = identical, 2 = opposite
                    # Convert to similarity score: 1 - distance/2
                    distance = r["distances"][0][i]
                    score = max(0.0, 1.0 - distance / 2.0)
                    all_results.append(RetrievalResult(
                        chunk_id=r["ids"][0][i],
                        text=r["documents"][0][i],
                        source=meta.get("source", ""),
                        section=meta.get("section", ""),
                        score=score,
                        metadata=meta,
                    ))
            except Exception as e:
                log.warning(f"Query failed for collection: {e}")

        all_results.sort(key=lambda x: x.score, reverse=True)
        return all_results[:top_k]

    def get_job_chunks(self, job_id: str) -> list[Chunk]:
        """Get all chunks for a specific job."""
        if self._jobs_col.count() == 0:
            return []
        try:
            r = self._jobs_col.get(
                where={"job_id": job_id},
                include=["documents", "metadatas"],
            )
            chunks = []
            for i in range(len(r["ids"])):
                meta = r["metadatas"][i] or {}
                chunks.append(Chunk(
                    id=r["ids"][i],
                    text=r["documents"][i],
                    source="job",
                    job_id=job_id,
                    section=meta.get("section", ""),
                ))
            return chunks
        except Exception as e:
            log.warning(f"Get job chunks failed: {e}")
            return []

    def clear_resume(self, resume_id: str):
        """Delete all resume chunks for a specific resume_id."""
        try:
            self._resume_col.delete(where={"resume_id": str(resume_id)})
        except Exception as e:
            log.warning(f"Clear resume chunks failed: {e}")

    def clear_jobs(self):
        """Clear jobs collection before each match."""
        self._client.delete_collection("jobs")
        self._jobs_col = self._client.get_or_create_collection(
            "jobs", metadata={"hnsw:space": "cosine"})

    def clear_supplement(self):
        self._client.delete_collection("supplement")
        self._supplement_col = self._client.get_or_create_collection(
            "supplement", metadata={"hnsw:space": "cosine"})

    def clear_all(self):
        for name in ("resume", "supplement", "jobs"):
            try:
                self._client.delete_collection(name)
            except Exception:
                pass
        self._resume_col = self._client.get_or_create_collection(
            "resume", metadata={"hnsw:space": "cosine"})
        self._supplement_col = self._client.get_or_create_collection(
            "supplement", metadata={"hnsw:space": "cosine"})
        self._jobs_col = self._client.get_or_create_collection(
            "jobs", metadata={"hnsw:space": "cosine"})
