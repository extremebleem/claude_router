# Context Optimization and Gemma Embeddings v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement context optimization in claude-gemini-proxy via Smart Tool Output Truncation, bounded LRU WebSearch caching, dynamic Thinking Budget scaling, and Gemma Embeddings v2 integration.

**Architecture:** 
- Add safe head/tail tool result truncation in `transformer/content.py` to bound massive CLI/file outputs.
- Upgrade `transformer/search.py` with an LRU cache with TTL.
- Implement token-aware `thinkingBudget` calculation in `transformer/content.py`.
- Implement `transformer/embeddings.py` for Gemma/Gemini embedding generation and cosine similarity reranking with local offline fallback.

**Tech Stack:** Python 3.10+, aiohttp, math/hashlib standard library, unittest.

**Spec:** `docs/superpowers/specs/2026-10-13-context-optimization-design.md`

## Global Constraints
- Pure Python 3 standard library for core functions (no extra heavy runtime dependencies like torch/transformers).
- Backward compatibility with existing 39 unit tests in `tests/`.
- All truncation markers must clearly report omitted line and char counts.
- Search LRU cache must be thread-safe with bounded size (max 256) and TTL (15 min).

## Review Focus
1. Truncation must never corrupt small or medium outputs (under threshold).
2. Truncation must correctly handle empty strings, non-string outputs, and Windows CRLF vs Unix LF.
3. LRU Search cache eviction must cleanly discard items older than TTL or when capacity is exceeded.
4. Thinking budget scaling must never reduce budget below a functional minimum (1024 tokens) nor exceed model max.
5. Embeddings similarity must gracefully return 0.0 or fallback vectors if network or API is unavailable.

---

### Task 1: Smart Tool Output Truncation

**Files:**
- Modify: `transformer/content.py`
- Test: `tests/test_truncation.py`

**Interfaces:**
- Produces: `truncate_tool_output(output_text: str, max_lines: int = 600, max_chars: int = 120000, head_lines: int = 250, tail_lines: int = 250) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_truncation.py`:
```python
import unittest
from transformer.content import truncate_tool_output

class TestToolTruncation(unittest.TestCase):
    def test_short_output_untouched(self):
        text = "Hello world\nLine 2\nLine 3"
        self.assertEqual(truncate_tool_output(text), text)

    def test_truncation_by_lines(self):
        lines = [f"line {i}" for i in range(1000)]
        text = "\n".join(lines)
        truncated = truncate_tool_output(text, max_lines=100, head_lines=20, tail_lines=20)
        self.assertIn("Omitted", truncated)
        self.assertTrue(truncated.startswith("line 0\nline 1"))
        self.assertTrue(truncated.endswith("line 998\nline 999"))

    def test_truncation_by_chars(self):
        long_line = "a" * 50000
        text = f"{long_line}\n{long_line}\n{long_line}"
        truncated = truncate_tool_output(text, max_chars=40000, max_lines=1000)
        self.assertIn("Omitted", truncated)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_truncation.py`
Expected: FAIL with `ImportError: cannot import name 'truncate_tool_output' from 'transformer.content'`

- [ ] **Step 3: Implement `truncate_tool_output` in `transformer/content.py`**

Add `truncate_tool_output` and connect it into `tool_result` handling:
```python
def truncate_tool_output(
    output_text: str,
    max_lines: int = 600,
    max_chars: int = 120000,
    head_lines: int = 250,
    tail_lines: int = 250
) -> str:
    if not isinstance(output_text, str):
        output_text = str(output_text)

    char_len = len(output_text)
    lines = output_text.splitlines()
    num_lines = len(lines)

    if num_lines <= max_lines and char_len <= max_chars:
        return output_text

    if num_lines > max_lines:
        head = lines[:head_lines]
        tail = lines[-tail_lines:] if tail_lines > 0 else []
        omitted_lines = num_lines - (len(head) + len(tail))
        omitted_chars = char_len - sum(len(l) + 1 for l in head) - sum(len(l) + 1 for l in tail)
        marker = f"\n\n[... Omitted {omitted_lines} lines (~{max(0, omitted_chars)} chars) by proxy to optimize context window ...]\n\n"
        return "\n".join(head) + marker + "\n".join(tail)

    # Exceeds max_chars but not max_lines
    half_chars = max_chars // 2
    head_part = output_text[:half_chars]
    tail_part = output_text[-half_chars:]
    omitted = char_len - (len(head_part) + len(tail_part))
    marker = f"\n\n[... Omitted ~{omitted} characters by proxy to optimize context window ...]\n\n"
    return head_part + marker + tail_part
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests/test_truncation.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add transformer/content.py tests/test_truncation.py
git commit -m "feat(context): add smart tool output truncation"
```

---

### Task 2: LRU Search Cache with TTL

**Files:**
- Modify: `transformer/search.py`
- Test: `tests/test_search_cache.py`

**Interfaces:**
- Produces: `SearchLRUCache` class with `get(query: str) -> Optional[List[Dict[str, str]]]` and `set(query: str, results: List[Dict[str, str]])`

- [ ] **Step 1: Write the failing test**

Create `tests/test_search_cache.py`:
```python
import unittest
import time
from transformer.search import SearchLRUCache

class TestSearchLRUCache(unittest.TestCase):
    def test_cache_hit_and_eviction(self):
        cache = SearchLRUCache(capacity=2, ttl_seconds=10.0)
        cache.set("query 1", [{"title": "R1"}])
        cache.set("query 2", [{"title": "R2"}])
        self.assertIsNotNone(cache.get("query 1"))
        
        # Adding 3rd item evicts least recently used
        cache.set("query 3", [{"title": "R3"}])
        self.assertIsNone(cache.get("query 2"))
        self.assertIsNotNone(cache.get("query 1"))
        self.assertIsNotNone(cache.get("query 3"))

    def test_cache_ttl_expiry(self):
        cache = SearchLRUCache(capacity=5, ttl_seconds=0.05)
        cache.set("quick", [{"title": "Fast"}])
        self.assertIsNotNone(cache.get("quick"))
        time.sleep(0.06)
        self.assertIsNone(cache.get("quick"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_search_cache.py`
Expected: FAIL with `ImportError: cannot import name 'SearchLRUCache' from 'transformer.search'`

- [ ] **Step 3: Implement `SearchLRUCache` in `transformer/search.py`**

Implement `SearchLRUCache` using `collections.OrderedDict` and thread-safe lock:
```python
from collections import OrderedDict
import threading

class SearchLRUCache:
    def __init__(self, capacity: int = 256, ttl_seconds: float = 900.0):
        self.capacity = capacity
        self.ttl = ttl_seconds
        self._cache: OrderedDict[str, Tuple[float, List[Dict[str, str]]]] = OrderedDict()
        self._lock = threading.Lock()

    def _normalize(self, q: str) -> str:
        return " ".join(q.lower().strip().split())

    def get(self, query: str) -> Optional[List[Dict[str, str]]]:
        key = self._normalize(query)
        with self._lock:
            if key not in self._cache:
                return None
            ts, val = self._cache[key]
            if time.time() - ts > self.ttl:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return val

    def set(self, query: str, results: List[Dict[str, str]]) -> None:
        key = self._normalize(query)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (time.time(), results)
            if len(self._cache) > self.capacity:
                self._cache.popitem(last=False)
```
Connect `search_cache` into `perform_web_search`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests/test_search_cache.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add transformer/search.py tests/test_search_cache.py
git commit -m "feat(search): implement thread-safe bounded LRU search cache"
```

---

### Task 3: Dynamic Thinking Budget Scaling

**Files:**
- Modify: `transformer/content.py`
- Test: `tests/test_thinking_scaling.py`

**Interfaces:**
- Produces: `calculate_thinking_budget(model: str, total_prompt_tokens: int, user_budget: Optional[int] = None) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_thinking_scaling.py`:
```python
import unittest
from transformer.content import calculate_thinking_budget

class TestThinkingScaling(unittest.TestCase):
    def test_standard_budget_under_threshold(self):
        # Small prompt tokens should preserve standard/requested budget
        budget = calculate_thinking_budget("gemini-3.8-flash-tiered", total_prompt_tokens=10000, user_budget=4096)
        self.assertEqual(budget, 4096)

    def test_scaled_budget_on_large_context(self):
        # For large context > 300k tokens, flash thinking budget scales down
        budget = calculate_thinking_budget("gemini-3.8-flash-tiered", total_prompt_tokens=350000, user_budget=16000)
        self.assertLessEqual(budget, 2048)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_thinking_scaling.py`
Expected: FAIL with `ImportError: cannot import name 'calculate_thinking_budget' from 'transformer.content'`

- [ ] **Step 3: Implement `calculate_thinking_budget` in `transformer/content.py`**

Implement scaling logic and integrate into `convert_anthropic_to_google`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests/test_thinking_scaling.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add transformer/content.py tests/test_thinking_scaling.py
git commit -m "feat(thinking): add dynamic thinking budget scaling based on token context"
```

---

### Task 4: Gemma / Gemini Embeddings v2 Module

**Files:**
- Create: `transformer/embeddings.py`
- Test: `tests/test_embeddings.py`

**Interfaces:**
- Produces: 
  - `compute_cosine_similarity(vec1: List[float], vec2: List[float]) -> float`
  - `class EmbeddingsClient`:
    - `async def get_embedding(text: str, model: str = "embeddinggemma") -> List[float]`
    - `def rank_by_relevance(query: str, documents: List[str]) -> List[Tuple[int, float]]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_embeddings.py`:
```python
import unittest
from transformer.embeddings import compute_cosine_similarity, local_text_vector

class TestEmbeddings(unittest.TestCase):
    def test_cosine_similarity(self):
        v1 = [1.0, 0.0, 0.0]
        v2 = [1.0, 0.0, 0.0]
        v3 = [0.0, 1.0, 0.0]
        self.assertAlmostEqual(compute_cosine_similarity(v1, v2), 1.0)
        self.assertAlmostEqual(compute_cosine_similarity(v1, v3), 0.0)

    def test_local_vectorizer_fallback(self):
        v1 = local_text_vector("cat and dog")
        v2 = local_text_vector("cat and puppy")
        v3 = local_text_vector("completely unrelated spaceship orbit")
        sim_close = compute_cosine_similarity(v1, v2)
        sim_far = compute_cosine_similarity(v1, v3)
        self.assertGreater(sim_close, sim_far)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_embeddings.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'transformer.embeddings'`

- [ ] **Step 3: Implement `transformer/embeddings.py`**

Implement cosine similarity, hash-based bag-of-words/character n-gram local vectorizer fallback, and async Cloud Code endpoint client.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests/test_embeddings.py`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m unittest discover -s tests`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add transformer/embeddings.py tests/test_embeddings.py
git commit -m "feat(embeddings): implement Gemma embeddings v2 and semantic ranking"
```
