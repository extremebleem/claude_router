# Context Optimization & Gemma Embeddings v2 Specification

## 1. Overview
This specification defines the context optimization subsystem for `claude-gemini-proxy`.
As conversations grow long and tools output massive payloads, the proxy must maintain high responsiveness, avoid hitting token limits prematurely, and reduce Time-to-First-Token (TTFT) without degrading model task comprehension.

## 2. Requirements & Subsystems

### 2.1 Smart Tool Output Truncation
- **Problem**: CLI commands (`cat`, `git log`, `grep`, `dir /s`, bash test dumps) can output 5,000–50,000+ lines into a single `tool_result`. This monopolizes the token window and causes severe latency.
- **Solution**:
  - Add `truncate_tool_output(output_text, max_lines=600, max_chars=120000, head_lines=250, tail_lines=250)` in `transformer/content.py`.
  - If output exceeds thresholds, preserve `head_lines` and `tail_lines`, inserting a clear marker:
    `\n\n[... Omitted {omitted_lines} lines ({omitted_chars} chars) by proxy to optimize context window ...]\n\n`.
  - Non-text or JSON objects are serialized and evaluated safely.

### 2.2 In-Memory LRU Search Cache
- **Problem**: Repetitive queries or follow-up iterations by agents scrape Bing/DuckDuckGo repeatedly, causing extra network latency and potential IP rate limits.
- **Solution**:
  - Implement a thread-safe / asyncio-safe bounded `SearchLRUCache` in `transformer/search.py`.
  - Capacity: 256 items. TTL: 900 seconds (15 minutes).
  - Normalization of query string (lowercased, stripped, whitespace collapsed).

### 2.3 Dynamic Thinking Budget Scaling
- **Problem**: When conversation context exceeds 200k–300k tokens, deep thinking budgets (e.g. 16k–32k tokens) dramatically increase TTFT and total latency.
- **Solution**:
  - Dynamically scale `thinkingBudget` in `transformer/content.py` based on estimated input tokens:
    - If total estimated prompt tokens < 100,000: standard budget (up to 16,000–32,000).
    - If between 100,000 and 300,000: scale budget to max 8,000.
    - If > 300,000 tokens: scale budget to max 4,096 (or 2,048 for Flash) to keep response streaming fast.

### 2.4 Gemma / Gemini Embeddings v2 Integration
- **Problem**: For long-term context pruning or smart context compaction, semantic vector similarity is needed to select relevant historic tool turns or user instructions.
- **Solution**:
  - Create `transformer/embeddings.py` providing `EmbeddingsClient` supporting `embeddinggemma` / `text-embedding-004` through Google Cloud Code internal API endpoints.
  - Compute cosine similarity for ranking text blocks.
  - Provide a fallback lightweight token-frequency / TF-IDF vectorizer if offline or without active network tokens.

## 3. Success Metrics
- 100% test pass rate across existing 39 tests + new unit tests for truncation, LRU cache, thinking scaling, and embeddings.
- Zero loss of crucial head/tail information in tool responses.
- Measurable reduction in payload sizes for oversized tool responses.
