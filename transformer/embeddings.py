import math
import re
import hashlib
import aiohttp
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

    # Dual-hash projection across dimension to minimize collisions
    for idx, token in enumerate(tokens):
        # 1. Primary word hash
        h1 = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
        sign = 1.0 if ((h1 >> 16) & 1) == 0 else -1.0
        vec[h1 % dim] += 1.0 * sign

        # 2. Character n-grams for subword similarity
        for n in (3, 4):
            for i in range(len(token) - n + 1):
                gram = token[i : i + n]
                gh = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
                gsign = 1.0 if ((gh >> 8) & 1) == 0 else -1.0
                vec[gh % dim] += 0.3 * gsign

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0.0:
        vec = [x / norm for x in vec]
    return vec


class EmbeddingsClient:
    """Vector embeddings client supporting online models with graceful fallback to local vectorizer."""

    def __init__(self, token_manager: Optional[Any] = None, session: Optional[aiohttp.ClientSession] = None):
        self.token_manager = token_manager
        self.session = session

    async def get_embedding(
        self, text: str, model: str = "text-embedding-004"
    ) -> List[float]:
        """Fetches vector embedding via Google Cloud Code / Gemini API or falls back to local vectorizer."""
        if not self.token_manager:
            return local_text_vector(text)

        try:
            # 1. Retrieve OAuth access token
            token_fn = getattr(self.token_manager, "get_token", None)
            account_token_fn = getattr(self.token_manager, "get_token_for_account", None)
            access_token = None

            if callable(token_fn):
                res = token_fn()
                if isinstance(res, tuple):
                    access_token = res[0]
                elif isinstance(res, str):
                    access_token = res
            elif callable(account_token_fn):
                res = account_token_fn()
                if isinstance(res, tuple):
                    access_token = res[0]
                elif isinstance(res, str):
                    access_token = res

            if not access_token:
                return local_text_vector(text)

            # 2. Dispatch online embedding request
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
            payload = {
                "model": f"models/{model}",
                "content": {"parts": [{"text": text[:2048]}]}
            }
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }

            async def _do_post(sess: aiohttp.ClientSession):
                async with sess.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=3.0)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        values = data.get("embedding", {}).get("values")
                        if isinstance(values, list) and values:
                            return values
                    return None

            if self.session and not self.session.closed:
                online_vec = await _do_post(self.session)
                if online_vec:
                    return online_vec
            else:
                async with aiohttp.ClientSession() as temp_sess:
                    online_vec = await _do_post(temp_sess)
                    if online_vec:
                        return online_vec

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

