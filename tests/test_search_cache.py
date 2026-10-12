import unittest
import time
from unittest.mock import patch
from transformer.search import SearchLRUCache, perform_web_search, search_cache

class TestSearchLRUCache(unittest.TestCase):
    def setUp(self):
        self.cache = SearchLRUCache(capacity=2, ttl_seconds=1.0)

    def test_cache_hit_and_miss(self):
        results = [{"title": "Test", "url": "http://example.com", "snippet": "Sample"}]
        self.assertIsNone(self.cache.get("hello world"))
        self.cache.set("hello world", results)
        self.assertEqual(self.cache.get("hello world"), results)

    def test_query_normalization(self):
        results = [{"title": "Python", "url": "https://python.org", "snippet": "Python Lang"}]
        self.cache.set("  PyThOn   docs  ", results)
        self.assertEqual(self.cache.get("python docs"), results)
        self.assertEqual(self.cache.get("  PYTHON   DOCS \t\n "), results)

    def test_lru_eviction(self):
        r1 = [{"title": "1", "url": "1", "snippet": "1"}]
        r2 = [{"title": "2", "url": "2", "snippet": "2"}]
        r3 = [{"title": "3", "url": "3", "snippet": "3"}]

        self.cache.set("q1", r1)
        self.cache.set("q2", r2)
        # Access q1 to make q2 oldest
        self.assertEqual(self.cache.get("q1"), r1)
        # Adding q3 should evict q2
        self.cache.set("q3", r3)

        self.assertEqual(self.cache.get("q1"), r1)
        self.assertIsNone(self.cache.get("q2"))
        self.assertEqual(self.cache.get("q3"), r3)

    def test_ttl_expiration(self):
        short_cache = SearchLRUCache(capacity=2, ttl_seconds=0.05)
        res = [{"title": "T", "url": "U", "snippet": "S"}]
        short_cache.set("q", res)
        self.assertEqual(short_cache.get("q"), res)
        time.sleep(0.06)
        self.assertIsNone(short_cache.get("q"))

    @patch("transformer.search.execute_bing_search")
    @patch("transformer.search.execute_ddg_search")
    def test_perform_web_search_integration(self, mock_ddg, mock_bing):
        # Reset module search_cache
        search_cache.clear() if hasattr(search_cache, "clear") else None
        mock_bing.return_value = [{"title": "Bing Res", "url": "http://bing.com", "snippet": "B"}]

        # First call hits mock_bing
        res1 = perform_web_search("test caching query", num_results=1)
        self.assertEqual(res1, mock_bing.return_value)
        self.assertEqual(mock_bing.call_count, 1)

        # Second call should use cache, mock_bing should not be called again
        res2 = perform_web_search("  TEST  CACHING   QUERY  ", num_results=1)
        self.assertEqual(res2, mock_bing.return_value)
        self.assertEqual(mock_bing.call_count, 1)

if __name__ == "__main__":
    unittest.main()
