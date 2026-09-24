"""RAG over the rules_kb/ markdown files, using a local Ollama embedding model."""

from __future__ import annotations

import chromadb
from langchain_ollama import OllamaEmbeddings

from . import config


def _chunk(text: str) -> list[str]:
    # Rules docs are short and already organized as one idea per paragraph -
    # a heavy text splitter would be overkill.
    return [p.strip() for p in text.split("\n\n") if p.strip()]


class RulesRetriever:
    def __init__(self) -> None:
        self._embeddings = OllamaEmbeddings(model=config.OLLAMA_EMBED_MODEL, base_url=config.OLLAMA_BASE_URL)
        self._client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        self._collection = self._client.get_or_create_collection("rules_kb")
        self._ingest_if_empty()

    def _ingest_if_empty(self) -> None:
        if self._collection.count() > 0:
            return
        ids, docs, metas = [], [], []
        for md_file in sorted(config.RULES_KB_DIR.glob("*.md")):
            chunks = _chunk(md_file.read_text(encoding="utf-8"))
            for i, chunk in enumerate(chunks):
                ids.append(f"{md_file.stem}_{i}")
                docs.append(chunk)
                metas.append({"source": md_file.name})
        if not docs:
            return
        embeddings = self._embeddings.embed_documents(docs)
        self._collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)

    def query(self, question: str, k: int = 3) -> list[str]:
        embedding = self._embeddings.embed_query(question)
        result = self._collection.query(query_embeddings=[embedding], n_results=k)
        return result["documents"][0] if result["documents"] else []
