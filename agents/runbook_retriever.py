"""
RunbookRetriever: Semantic search over runbook Markdown files using ChromaDB.
Embeddings are generated locally with sentence-transformers (all-MiniLM-L6-v2).
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass

import chromadb
from chromadb.utils import embedding_functions

from observability.metrics import kb_query_duration_seconds


@dataclass
class RunbookResult:
    title: str
    content: str
    relevance_score: float   # 0.0 (irrelevant) → 1.0 (perfect match)
    file_path: str


class RunbookRetriever:
    """
    Indexes runbook Markdown files in a local ChromaDB store and performs
    cosine-similarity semantic search using all-MiniLM-L6-v2 embeddings.
    """

    COLLECTION = "runbooks"
    EMBED_MODEL = "all-MiniLM-L6-v2"

    def __init__(self, runbooks_dir: str, db_path: str = "./data/chroma_db") -> None:
        self.runbooks_dir = runbooks_dir
        os.makedirs(db_path, exist_ok=True)

        self._chroma = chromadb.PersistentClient(path=db_path)
        self._embed = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=self.EMBED_MODEL
        )
        self._col = self._chroma.get_or_create_collection(
            name=self.COLLECTION,
            embedding_function=self._embed,
            metadata={"hnsw:space": "cosine"},
        )

        if self._col.count() == 0:
            self._index()

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def _index(self) -> None:
        pattern = os.path.join(self.runbooks_dir, "*.md")
        paths = sorted(glob.glob(pattern))
        if not paths:
            raise FileNotFoundError(f"No .md runbooks found in {self.runbooks_dir!r}")

        docs, metas, ids = [], [], []
        for path in paths:
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            name = os.path.basename(path)
            title = name.replace(".md", "").replace("_", " ").title()
            docs.append(content)
            metas.append({"title": title, "file_path": path})
            ids.append(name)

        self._col.add(documents=docs, metadatas=metas, ids=ids)

    def reindex(self) -> None:
        """Drop and rebuild the collection (use after updating runbooks)."""
        self._chroma.delete_collection(self.COLLECTION)
        self._col = self._chroma.get_or_create_collection(
            name=self.COLLECTION,
            embedding_function=self._embed,
            metadata={"hnsw:space": "cosine"},
        )
        self._index()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, n_results: int = 3) -> list[RunbookResult]:
        """Return the top-n most relevant runbooks for *query*."""
        n = min(n_results, self._col.count())
        if n == 0:
            return []

        with kb_query_duration_seconds.labels(n_results=str(n)).time():
            results = self._col.query(query_texts=[query], n_results=n)

        output: list[RunbookResult] = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            # ChromaDB cosine distance ∈ [0, 2]; convert to relevance ∈ [0, 1]
            relevance = max(0.0, 1.0 - dist)
            output.append(
                RunbookResult(
                    title=meta["title"],
                    content=doc,
                    relevance_score=relevance,
                    file_path=meta["file_path"],
                )
            )

        return output
