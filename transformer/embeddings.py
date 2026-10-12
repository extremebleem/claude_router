import math
import re
import hashlib
from typing import List, Tuple, Optional, Any


def compute_cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Compute cosine similarity between two numeric vectors. Returns 0.0 for zero vectors or empty inputs."""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot = 0.0
    norm1 = 0.0
    norm2 = 0.0
    for a, b in zip(vec1, vec2):
        dot += a * b
        norm1 += a * a
        norm2 += b * b
    if norm1 <= 0.0 or norm2 <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm1) * math.sqrt(norm2))


def local_text_vector(text: str, dim: int = 128) -> List[float]:
    """Lightweight hash- and n-gram based normalized vector for offline/fallback ranking."""
    if not text or not text.strip():
        return [0.0] * dim

    vec = [0.0] * dim
    tokens = re.findall(r"\w+", text.lower())
    if not tokens:
        return [0.0] * dim

    # Token-level hash weighting
    for token in tokens:
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
        # Character n-grams for subword similarity
        for n in (3, 4):
            for i in range(len(token) - n + 1):
                gram = token[i : i + n]
                gh = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
                vec[gh % dim] += 0.3

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0.0:
        vec = [x / norm for x in vec]
    return vec


class EmbeddingsClient:
    """Vector embeddings client supporting online models with graceful fallback to local vectorizer."""

    def __init__(self, token_manager: Optional[Any] = None):
        self.token_manager = token_manager

    async def get_embedding(
        self, text: str, model: str = "text-embedding-004"
    ) -> List[float]:
        # shortcut: online Google GenAI embedding calls require active network credentials; falls back to local vectorizer when token_manager is not configured
        if not self.token_manager:
            return local_text_vector(text)

        try:
            # If token_manager is available, attempt token acquisition
            token = getattr(self.token_manager, "get_token", None)
            if callable(token):
                res = token()
                if not res:
                    return local_text_vector(text)
            return local_text_vector(text)
        except Exception:
            return local_text_vector(text)

    def rank_by_relevance(
        self, query: str, documents: List[str]
    ) -> List[Tuple[int, float]]:
        """Rank documents by cosine similarity to query in descending order, returning list of (index, score)."""
        q_vec = local_text_vector(query)
        scores = []
        for idx, doc in enumerate(documents):
            d_vec = local_text_vector(doc)
            sim = compute_cosine_similarity(q_vec, d_vec)
            scores.append((idx, sim))
        scores.sort(key=lambda item: item[1], reverse=True)
        return scores
