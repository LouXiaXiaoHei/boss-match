"""RAG retriever — bridges VectorStore and Embedder for query-time retrieval."""

import logging

from src.ai.embedder import Embedder
from src.ai.vector_store import VectorStore, RetrievalResult

log = logging.getLogger(__name__)


class Retriever:
    """RAG retriever: embed query, fetch relevant chunks from VectorStore."""

    def __init__(self, vector_store: VectorStore, embedder: Embedder):
        self.vector_store = vector_store
        self.embedder = embedder

    def retrieve_for_job(self, query_text: str = None,
                         query_embedding: list[float] = None,
                         top_k: int = 5,
                         resume_id: str = None) -> list[RetrievalResult]:
        """Retrieve resume + supplement chunks relevant to a job query.

        Either query_text or query_embedding must be provided.
        """
        if query_embedding is None:
            if not query_text:
                return []
            query_embedding = self.embedder.embed_one(query_text)
            if not query_embedding:
                return []

        return self.vector_store.query_multi(
            query_embedding,
            sources=["resume", "supplement"],
            top_k=top_k,
            resume_id=resume_id,
        )

    def retrieve_supplements(self, query_embedding: list[float] = None,
                             top_k: int = 3) -> list[RetrievalResult]:
        """Retrieve key supplement chunks for summary phase.

        If no query_embedding given, returns first N chunks (most recent).
        """
        if query_embedding is not None:
            results = self.vector_store.query(
                query_embedding, source="supplement", top_k=top_k,
            )
            return results

        # No query: return first N from supplement collection
        if self.vector_store._supplement_col.count() == 0:
            return []
        try:
            r = self.vector_store._supplement_col.get(
                limit=top_k,
                include=["documents", "metadatas"],
            )
            results = []
            for i in range(len(r["ids"])):
                meta = r["metadatas"][i] or {}
                results.append(RetrievalResult(
                    chunk_id=r["ids"][i],
                    text=r["documents"][i],
                    source="supplement",
                    section=meta.get("section", ""),
                    score=1.0,
                    metadata=meta,
                ))
            return results
        except Exception as e:
            log.warning(f"Retrieve supplements failed: {e}")
            return []
