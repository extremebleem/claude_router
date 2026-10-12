# Token Leakage Prevention and Accurate Cache Telemetry Specification

## 1. Context & Motivation
Detailed analysis of long user sessions (`18d2b632-307a-4b56-a12b-3a6bbb527e68.jsonl`) revealed three primary failure modes that drain tokens and trigger 429 rate limit errors:
1. **Uncontrolled File Re-Reading (`Read` Tool Duplication)**:
   - A single file (`PaintTraverse.cpp`) was read 287 times over 750 turns. Each turn accumulated duplicate dumps of the file, causing input tokens to grow linearly to 866,554 tokens (totaling 364 million tokens processed).
2. **Missing SSE Cache Telemetry in `message_delta` (`transformer/streamer.py`)**:
   - Google Cloud Code sends `usageMetadata` (including `cachedContentTokenCount`) in the final stream chunk.
   - Claude Code SSE protocol expects cache tokens and total input tokens in `message_delta.usage`. Because `message_start` emits before `usageMetadata` arrives, `cache_read_input_tokens` was always reported as 0, blinding Claude Code CLI to active cache hits and confusing internal autocompact heuristics.
3. **Overly Lenient Auto-Compact Threshold in `launcher.py`**:
   - `CLAUDE_CODE_MAX_CONTEXT_TOKENS` was set to `1,000,000`. Claude Code refused to trigger background autocompact until reaching 1M tokens, by which point Google Cloud Code TPM quotas were already saturated. Setting this to `200,000` aligns with Google Cloud Code's optimal prompt-caching window and triggers proactive compaction before rate-limit thresholds.

## 2. Technical Architecture

### 2.1 Superseded `Read` Tool Result Deduplication (`transformer/content.py`)
- In `convert_anthropic_to_google`:
  - Track all `Read` tool invocations: map file path to list of `tool_use_id`s.
  - For any `Read` tool_result whose `tool_use_id` is NOT the latest invocation for that file path:
    - Replace the bulky file contents with a concise tombstone:
      `"[File content omitted: superseded by a later read of {file_path}]"`
  - This eliminates hundreds of duplicate file dumps from the conversation history while preserving tool invocation sequence integrity.

### 2.2 Accurate Cache Read Usage Reporting (`transformer/streamer.py`)
- In `parse_google_sse_stream`:
  - When the final `message_delta` is yielded, populate `usage`:
    ```python
    "usage": {
        "output_tokens": output_tokens,
        "input_tokens": max(0, input_tokens - cache_read_tokens),
        "cache_read_input_tokens": cache_read_tokens,
        "cache_creation_input_tokens": 0
    }
    ```
  - In `aggregate_sse_stream_to_json`: correctly aggregate `cache_read_input_tokens`.

### 2.3 Proactive Context Window Threshold (`launcher.py`)
- Update `launcher.py`:
  - Change default `CLAUDE_CODE_MAX_CONTEXT_TOKENS` from `1000000` to `200000` (configurable via env).
  - This prompts Claude Code to run `/compact` naturally when exceeding 200k tokens instead of running up to 866k tokens and exhausting quota.

## 3. Validation & Testing
- Unit tests verifying:
  - Repeated reads of the same file have older outputs replaced with the superseded tombstone, while the most recent read retains full contents.
  - Final SSE `message_delta` contains `cache_read_input_tokens` matching Google's `cachedContentTokenCount`.
  - Non-streaming aggregator reflects accurate token counts and cache reads.
- 100% test suite passing across all 66+ tests.
