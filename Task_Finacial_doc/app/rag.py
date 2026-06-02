import json
import os
import re
from typing import List, Dict, Any

import faiss
import numpy as np
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

VECTOR_DIR = "vector_store"
INDEX_PATH = os.path.join(VECTOR_DIR, "documents.faiss")
META_PATH = os.path.join(VECTOR_DIR, "metadata.json")

os.makedirs(VECTOR_DIR, exist_ok=True)


def extract_text_from_file(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        reader = PdfReader(file_path)
        texts = []
        for page in reader.pages:
            texts.append(page.extract_text() or "")
        return "\n".join(texts).strip()

    if ext in [".txt", ".md"]:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()

    raise ValueError("Only PDF, TXT, and MD files are supported in this simple version.")


def chunk_text(text: str, max_len: int = 800, overlap: int = 150) -> List[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []

    chunks = []
    step = max_len - overlap
    for start in range(0, len(cleaned), step):
        chunk = cleaned[start:start + max_len].strip()
        if chunk:
            chunks.append(chunk)
    return chunks


class SimpleVectorStore:
    def __init__(self):
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        self.dim = 384
        self.index = faiss.IndexFlatIP(self.dim)
        self.metadata: List[Dict[str, Any]] = []
        self.load()

    def load(self):
        if os.path.exists(INDEX_PATH):
            self.index = faiss.read_index(INDEX_PATH)
        else:
            self.index = faiss.IndexFlatIP(self.dim)

        if os.path.exists(META_PATH):
            with open(META_PATH, "r", encoding="utf-8") as f:
                self.metadata = json.load(f)
        else:
            self.metadata = []

    def save(self):
        faiss.write_index(self.index, INDEX_PATH)
        with open(META_PATH, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, ensure_ascii=False, indent=2)

    def rebuild(self):
        self.index = faiss.IndexFlatIP(self.dim)
        if self.metadata:
            texts = [m["chunk_text"] for m in self.metadata]
            emb = self.model.encode(texts, normalize_embeddings=True)
            emb = np.array(emb, dtype="float32")
            self.index.add(emb)
        self.save()

    def remove_document(self, document_id: int):
        self.metadata = [m for m in self.metadata if m["document_id"] != document_id]
        self.rebuild()

    def index_document(self, document_id: int, chunks: List[str]):

             # remove old chunks of same document first
        self.metadata = [m for m in self.metadata if m["document_id"] != document_id]

        if chunks:
            embeddings = self.model.encode(chunks, normalize_embeddings=True)
            embeddings = np.array(embeddings, dtype="float32")
            self.index = faiss.IndexFlatIP(self.dim)

                      # rebuild from current metadata first
            if self.metadata:
                current_texts = [m["chunk_text"] for m in self.metadata]
                current_emb = self.model.encode(current_texts, normalize_embeddings=True)
                current_emb = np.array(current_emb, dtype="float32")
                self.index.add(current_emb)

            for i, chunk in enumerate(chunks):
                self.metadata.append({
                    "document_id": document_id,
                    "chunk_index": i,
                    "chunk_text": chunk,
                })
            self.index.add(embeddings)

        self.save()

    def search(self, query: str, top_k: int = 5):
        if self.index.ntotal == 0:
            return []

        query_emb = self.model.encode([query], normalize_embeddings=True)
        query_emb = np.array(query_emb, dtype="float32")

        search_k = min(max(top_k * 4, top_k), self.index.ntotal)
        scores, ids = self.index.search(query_emb, search_k)

        query_words = set(query.lower().split())
        results = []

        for score, idx in zip(scores[0], ids[0]):
            if idx < 0 or idx >= len(self.metadata):
                continue

            meta = self.metadata[idx]
            chunk_words = set(meta["chunk_text"].lower().split())
            overlap = len(query_words.intersection(chunk_words))

            rerank_score = float(score) + (0.05 * overlap)

            results.append({
                "document_id": meta["document_id"],
                "chunk_index": meta["chunk_index"],
                "chunk_text": meta["chunk_text"],
                "score": float(score),
                "rerank_score": rerank_score,
            })

        results.sort(key=lambda x: x["rerank_score"], reverse=True)
        return results[:top_k]

    def get_context(self, document_id: int):
        chunks = [m for m in self.metadata if m["document_id"] == document_id]
        chunks = sorted(chunks, key=lambda x: x["chunk_index"])
        return "\n".join([c["chunk_text"] for c in chunks])


rag_store = SimpleVectorStore()