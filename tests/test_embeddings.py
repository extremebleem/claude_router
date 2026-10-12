import unittest
import math
import asyncio
from unittest.mock import AsyncMock, patch

from transformer.embeddings import (
    compute_cosine_similarity,
    local_text_vector,
    EmbeddingsClient,
)


class TestEmbeddings(unittest.TestCase):
    def test_compute_cosine_similarity_identical(self):
        vec = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(compute_cosine_similarity(vec, vec), 1.0, places=5)

    def test_compute_cosine_similarity_orthogonal(self):
        v1 = [1.0, 0.0]
        v2 = [0.0, 1.0]
        self.assertAlmostEqual(compute_cosine_similarity(v1, v2), 0.0, places=5)

    def test_compute_cosine_similarity_zero_vector(self):
        v1 = [0.0, 0.0]
        v2 = [1.0, 2.0]
        self.assertEqual(compute_cosine_similarity(v1, v2), 0.0)
        self.assertEqual(compute_cosine_similarity([], []), 0.0)

    def test_local_text_vector_shape_and_norm(self):
        vec = local_text_vector("hello world", dim=64)
        self.assertEqual(len(vec), 64)
        norm = math.sqrt(sum(x * x for x in vec))
        self.assertAlmostEqual(norm, 1.0, places=4)

    def test_local_text_vector_empty_string(self):
        vec = local_text_vector("", dim=64)
        self.assertEqual(len(vec), 64)
        self.assertTrue(all(x == 0.0 for x in vec))

    def test_local_text_vector_similarity_semantics(self):
        v_base = local_text_vector("python asynchronous programming")
        v_similar = local_text_vector("python async code and programming")
        v_diff = local_text_vector("banana strawberry ice cream dessert")

        sim_close = compute_cosine_similarity(v_base, v_similar)
        sim_far = compute_cosine_similarity(v_base, v_diff)
        self.assertGreater(sim_close, sim_far)

    def test_rank_by_relevance(self):
        client = EmbeddingsClient()
        query = "machine learning and neural networks"
        docs = [
            "completely unrelated cooking recipes for soup",
            "deep learning, neural networks, machine intelligence",
            "hiking trails in the pacific northwest mountains",
        ]
        ranked = client.rank_by_relevance(query, docs)
        self.assertEqual(len(ranked), 3)
        # Top document should be index 1
        top_idx, top_score = ranked[0]
        self.assertEqual(top_idx, 1)
        self.assertGreater(top_score, ranked[1][1])
        self.assertGreater(ranked[1][1], ranked[2][1])

    def test_get_embedding_fallback(self):
        client = EmbeddingsClient()
        vec = asyncio.run(client.get_embedding("test fallback text"))
        self.assertEqual(len(vec), 128)
        norm = math.sqrt(sum(x * x for x in vec))
        self.assertAlmostEqual(norm, 1.0, places=4)


if __name__ == "__main__":
    unittest.main()
