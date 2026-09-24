"""Persistent per-turn game journal, backed by a Chroma collection on disk.

This is the "what does the agent remember, and what does it recall" demo:
every turn it writes a short note about what it played and why, and before
choosing its next move it recalls the most similar past note (from this
run or any earlier one - the collection is never cleared between runs).
Early on there's nothing similar to recall; after enough turns (or across
repeated runs), recall starts surfacing real past experience.
"""

from __future__ import annotations

import time
import uuid

import chromadb
from langchain_ollama import OllamaEmbeddings

from . import config


class GameJournal:
    def __init__(self) -> None:
        self._embeddings = OllamaEmbeddings(model=config.OLLAMA_EMBED_MODEL, base_url=config.OLLAMA_BASE_URL)
        self._client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        self._collection = self._client.get_or_create_collection("game_journal")

    def remember(self, text: str, metadata: dict) -> str:
        entry_id = f"turn_{uuid.uuid4().hex[:10]}"
        embedding = self._embeddings.embed_query(text)
        self._collection.add(ids=[entry_id], documents=[text], embeddings=[embedding], metadatas=[{**metadata, "stored_at": time.time()}])
        return entry_id

    def recall(self, query: str, k: int = 1) -> list[dict]:
        if self._collection.count() == 0:
            return []
        embedding = self._embeddings.embed_query(query)
        result = self._collection.query(query_embeddings=[embedding], n_results=min(k, self._collection.count()))
        if not result["ids"][0]:
            return []
        return [
            {"text": doc, "distance": dist, **meta}
            for doc, dist, meta in zip(result["documents"][0], result["distances"][0], result["metadatas"][0])
        ]
