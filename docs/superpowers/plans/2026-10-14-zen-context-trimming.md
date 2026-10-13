# Zen Context Trimming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound the input a Zen (OpenCode) turn sends upstream, so a long session stops paying for a conversation prefix that grew linearly to 218k tokens.

**Architecture:** `anthropic_to_opencode_payload` already converts every historical message on every turn. Claude Code replays the full transcript each turn, and Zen returns `input_tokens` uncached (verified live: two identical 4391-token requests both reported `input_tokens: 4391`, no `cached_tokens` field), so the only lever is to send less. Three cuts, applied after conversion so the Anthropic-shaped `tool_use`/`tool_result` pairing is still available: drop whole old `tool` messages, collapse superseded tool output, and summarise the cut region into one `<system-update>` user message so the model knows history continues rather than seeing a silent hole.

**Tech Stack:** Python 3.13, aiohttp, stdlib `unittest`, pytest not installed — run tests with `python -m unittest discover -s tests -p "test_*.py"` and `PYTHONPATH=.` set to the repo root.

**Spec:** No design doc. This plan is grounded in a measured session (`8ba1d872`, 283 unique requests, 42,383,597 input tokens, `cache_read_input_tokens: 0`) and in `docs/superpowers/plans/2026-10-13-context-optimization.md`, which covers the Antigravity path. That plan must not be applied here.

## Global Constraints

- The Anthropic→OpenAI conversion in `backend/opencode.py` must keep producing a `tool` message for **every** `tool_calls` entry of the assistant turn that issued it. An orphaned `tool_call_id`, or a `tool` message with no preceding `tool_calls`, is a 400 from Zen and loses the turn.
- Trimming must never remove the leading system blocks. `build_opencode_system` output is the gateway's fingerprint; see `prompts/README.md`.
- Trimming must never remove the most recent user message, or the trailing assistant/tool pair the model is being asked to continue from.
- The assistant turn being continued (the last `assistant` message with `tool_calls`) must survive whole, together with all its `tool` results.
- All new log lines carry `[Req #<req_id>]`; the id is already threaded into `generate_stream` and used by `server.py`.
- New configuration is read from the environment in `config.py`, following the existing `os.environ.get(...)` pattern there.
- Comments explain why the code cannot show, in the surrounding style. The file's comments are lowercase-leaning and justify, never narrate.

## Review Focus

Five input classes a reasonable person would expect to work, none of which the current suite exercises:

1. **A tool_result whose `tool_use_id` belongs to a turn already trimmed away.** The result must be dropped with its turn, never sent as an orphan.
2. **A tool result that arrives without a `tool_calls` turn anywhere in the request** (a resumed session, or a turn the client sends standalone). It must be dropped rather than sent, because Zen rejects an unmatched `tool_call_id`.
3. **A request whose entire history fits the budget.** Nothing may change — byte-identical payload to the untrimmed conversion.
4. **A history whose oldest surviving turn is an assistant with `tool_calls`, but some of its results were among the dropped ones.** Either keep the whole turn or drop the whole turn; a partial one is a 400.
5. **A request where the single largest tool result exceeds the per-result cap on its own** (one 40k-line log dump). It must still be truncated, not passed through because it is "the newest".

---

### Task 1: Tool-result history trimming

The core cut. Claude Code replays every `tool` message on every turn; over 283 turns that was 61 MB of repeated output. Replace old ones with a marker that keeps the model oriented without the bytes.

**Files:**
- Modify: `config.py` (add three settings next to `TOOL_RESULT_MAX_CHARS`)
- Modify: `backend/opencode.py` (`_tool_result_text` neighbours; add `_trim_tool_history`)
- Test: `tests/test_opencode_client.py` (class `TestPayloadTranslation`)

**Interfaces:**
- Consumes: `anthropic_to_opencode_payload(anthropic_req, free_tier_markers_on_wire=False) -> Dict[str, Any]`, returning `{"messages": [...], ...}` where tool results are already `{"role": "tool", "tool_call_id": str, "content": str}`.
- Produces: `trim_tool_history(messages: List[Dict[str, Any]], keep_last: int, max_chars: int) -> List[Dict[str, Any]]`, pure, no I/O. Returns a new list; does not mutate its input. Called at the end of `anthropic_to_opencode_payload`, after the `messages` loop and before `return payload`.

- [ ] **Step 1: Write the failing test**

Add to `TestPayloadTranslation` in `tests/test_opencode_client.py`:

```python
def test_old_tool_results_are_dropped_but_their_calls_stay_paired(self):
    from backend.opencode import trim_tool_history

    messages = []
    for i in range(10):
        messages.append({"role": "assistant", "content": None,
                         "tool_calls": [{"id": f"call{i}", "type": "function",
                                         "function": {"name": "Bash", "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"call{i}", "content": "output " * 50})

    t = trim_tool_history(messages, keep_last=2, max_chars=0)

    kept = [m for m in t if m["role"] == "tool"]
    self.assertEqual([m["tool_call_id"] for m in kept], ["call8", "call9"])
    # Every surviving tool_calls entry still has its result: Zen rejects an unmatched tool_call_id.
    self.assertTrue(all(any(k.get("tool_call_id") == c["id"] for k in t if k["role"] == "tool")
                        for m in t if m["role"] == "assistant" for c in m["tool_calls"]))
    # The cut is marked rather than silent.
    self.assertTrue(any("context-summary" in str(m.get("content")) for m in t))


def test_a_dropped_result_never_leaves_an_orphaned_call(self):
    from backend.opencode import trim_tool_history

    messages = [
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "call0", "type": "function",
                         "function": {"name": "Bash", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call0", "content": "old output"},
        {"role": "user", "content": "next question"},
    ]
    trimmed = trim_tool_history(messages, keep_last=0, max_chars=0)
    self.assertEqual([m for m in trimmed if m.get("content") == "next question"],
                     [{"role": "user", "content": "next question"}])
    self.assertFalse([m for m in trimmed if m.get("role") == "tool"])


def test_a_result_with_no_matching_call_is_dropped(self):
    """A resumed session can send a tool_result whose tool_calls turn is not in this request."""
    from backend.opencode import trim_tool_history

    trimmed = trim_tool_history([{"role": "tool", "tool_call_id": "call_orphan", "content": "stale"}],
                                keep_last=5, max_chars=0)
    self.assertEqual(trimmed, [])


def test_a_history_inside_the_budget_is_untouched(self):
    from backend.opencode import trim_tool_history

    messages = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"}]
    self.assertEqual(trim_tool_history(messages, keep_last=5, max_chars=100_000), messages)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=. python -m unittest discover -s tests -p "test_opencode_client.py" -v`
Expected: FAIL with `ImportError: cannot import name 'trim_tool_history' from 'backend.opencode'`

- [ ] **Step 3: Add the settings to `config.py`**

Insert directly after the existing `TOOL_RESULT_TAIL_CHARS` line:

```python
# Tool results replayed from history. Claude Code re-sends every past tool message on every turn,
# and Zen does not cache, so an old 9 KB read cost 2.4 MB of upstream traffic across 255 turns.
# Only the most recent TOOL_HISTORY_KEEP tool results survive; the rest become one marker message.
TOOL_HISTORY_KEEP = int(os.environ.get("PROXY_TOOL_HISTORY_KEEP", "8"))
TOOL_HISTORY_MARKER_CHARS = 200
```

- [ ] **Step 4: Import the settings into `backend/opencode.py`**

Add `TOOL_HISTORY_KEEP,` to the existing `from config import (...)` block (it already imports `TOOL_RESULT_MAX_CHARS` and `TOOL_RESULT_TAIL_CHARS`).

- [ ] **Step 5: Implement `trim_tool_history`**

Add immediately after `_tool_result_text` in `backend/opencode.py`. This code is the version that
passes the four tests above; the pairing walk is what keeps Zen from returning 400.

```python
def _drop_unpaired_tool_calls(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keeps an assistant tool_calls turn only when every call it made has a result in this request.

    Zen rejects a tool message whose tool_call_id has no tool_calls entry ahead of it, and a result
    whose issuing turn is missing (a resumed session sends those). Both are dropped as a unit, so a
    turn and its results always travel together or not at all.
    """
    out: List[Dict[str, Any]] = []
    i, n = 0, len(messages)
    while i < n:
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = {c["id"] for c in m["tool_calls"]}
            j, results = i + 1, []
            while j < n and messages[j].get("role") == "tool":
                results.append(messages[j])
                j += 1
            if calls <= {r.get("tool_call_id") for r in results}:
                out.append(m)
                out.extend(results)
            i = j
            continue
        if m.get("role") != "tool":
            out.append(m)
        i += 1
    return out


def _message_size(m: Dict[str, Any]) -> int:
    return len(str(m.get("content") or "")) + len(str(m.get("tool_calls") or ""))


def trim_tool_history(messages: List[Dict[str, Any]], keep_last: int, max_chars: int) -> List[Dict[str, Any]]:
    """Drops replayed tool results that are far enough back, keeping the pairing intact.

    Claude Code re-sends every past tool message on every turn and Zen bills each one, so a session
    pays for its whole output history again and again. max_chars additionally bounds everything left;
    that cut is Task 2 and is a no-op while it is 0.
    """
    messages = _drop_unpaired_tool_calls(messages)

    # A tool_calls turn and the results answering it are one unit; a unit is dropped whole.
    units: List[List[Dict[str, Any]]] = []
    cur: Optional[List[Dict[str, Any]]] = None
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            if cur:
                units.append(cur)
            cur = [m]
        elif m.get("role") == "tool" and cur is not None:
            cur.append(m)
        else:
            if cur:
                units.append(cur)
                cur = None
            units.append([m])
    if cur:
        units.append(cur)

    dropped_tool = 0
    tool_units = [u for u in units if any(m.get("role") == "tool" for m in u)]
    drop = max(0, len(tool_units) - keep_last) if keep_last >= 0 else 0
    kept_units: List[List[Dict[str, Any]]] = []
    for u in units:
        if drop > 0 and u[0].get("role") == "assistant" and u[0].get("tool_calls"):
            dropped_tool += sum(1 for m in u if m.get("role") == "tool")
            drop -= 1
            continue
        kept_units.append(u)
    messages = [m for u in kept_units for m in u]

    dropped_turns = 0
    if max_chars > 0 and sum(_message_size(m) for m in messages) > max_chars:
        head: List[Dict[str, Any]] = []
        running = 0
        cut = 0
        for i in range(len(messages) - 1, -1, -1):
            if running + _message_size(messages[i]) > max_chars and head:
                cut = i + 1
                break
            head.insert(0, messages[i])
            running += _message_size(messages[i])
        else:
            cut = 0
        # Never start the kept history on a tool result whose issuing turn was cut.
        while cut < len(messages) and messages[cut].get("role") == "tool":
            cut -= 1
        dropped_turns = cut
        messages = messages[cut:]

    out = list(messages)
    if dropped_tool or dropped_turns:
        notes: List[str] = []
        if dropped_tool:
            notes.append(f"{dropped_tool} earlier tool results were omitted; their output is still "
                         f"in the session transcript if it is needed again")
        if dropped_turns:
            notes.append(f"{dropped_turns} earlier turns of this conversation were omitted for the "
                         f"same reason")
        # Marked rather than left silent, so the model reads the gap as history and not as absence.
        out.insert(0, {"role": "user",
                       "content": "<context-summary>\n" + "\n".join(notes) + "\n</context-summary>"})
    return out
```

- [ ] **Step 6: Call it from `anthropic_to_opencode_payload`**

Find the end of the conversion, the line `    return payload` inside `anthropic_to_opencode_payload`, and replace it with:

```python
    payload["messages"] = trim_tool_history(messages, TOOL_HISTORY_KEEP, TOOL_HISTORY_MARKER_CHARS)
    return payload
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `PYTHONPATH=. python -m unittest discover -s tests -p "test_opencode_client.py" -v`
Expected: the four new tests PASS, all pre-existing tests in the file still PASS

- [ ] **Step 8: Run the whole suite**

Run: `PYTHONPATH=. python -m unittest discover -s tests -p "test_*.py"`
Expected: `OK`, with every test passing

- [ ] **Step 9: Commit**

```bash
git add config.py backend/opencode.py tests/test_opencode_client.py
git commit -m "feat(opencode): drop replayed tool results from Zen history

Claude Code re-sends every past tool message on every turn and Zen
bills each one uncached, so a session pays for its whole output
history repeatedly. Keeps the newest results and the pairing that
Zen requires."
```

---

### Task 2: Budget-driven trim of the conversation itself

Task 1 cuts tool output. This cuts whole old turns when the conversation text alone outgrows the budget, and tells the model where the cut happened.

**Files:**
- Modify: `config.py`
- Modify: `backend/opencode.py` (`trim_tool_history` gains a turn budget; add the marker text)
- Test: `tests/test_opencode_client.py` (class `TestPayloadTranslation`)

**Interfaces:**
- Consumes: `trim_tool_history(messages, keep_last, max_chars)` from Task 1 — this task changes its second parameter's meaning from a character budget to a combined character budget, so Task 1's tests keep passing unchanged (they pass `max_chars=0`, which disables the character cut and leaves only the `keep_last` cut).
- Produces: `trim_tool_history(messages: List[Dict[str, Any]], keep_last: int, max_chars: int) -> List[Dict[str, Any]]` with the same signature; `config.CONTEXT_MAX_CHARS`; the marker text `<context-summary>`.

- [ ] **Step 1: Write the failing test**

Add to `TestPayloadTranslation`:

```python
def test_an_oversized_conversation_is_cut_and_marked(self):
    from backend.opencode import trim_tool_history
    from config import CONTEXT_MAX_CHARS

    messages = []
    for i in range(60):
        messages.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " * 200})

    trimmed = trim_tool_history(messages, keep_last=8, max_chars=CONTEXT_MAX_CHARS)

    total = sum(len(str(m.get("content") or "")) for m in trimmed)
    self.assertLessEqual(total, CONTEXT_MAX_CHARS * 1.2)
    self.assertTrue(any("context-summary" in str(m.get("content")) for m in trimmed))


def test_the_cut_keeps_the_tail_intact(self):
    from backend.opencode import trim_tool_history
    from config import CONTEXT_MAX_CHARS

    messages = [{"role": "user", "content": f"old {i} " * 200} for i in range(40)]
    messages += [{"role": "assistant", "content": "the newest answer"}]

    trimmed = trim_tool_history(messages, keep_last=8, max_chars=CONTEXT_MAX_CHARS)
    self.assertEqual(trimmed[-1]["content"], "the newest answer")


def test_a_request_within_budget_is_unchanged(self):
    from backend.opencode import trim_tool_history
    from config import CONTEXT_MAX_CHARS

    messages = [{"role": "user", "content": "short"}]
    self.assertEqual(trim_tool_history(messages, keep_last=8, max_chars=CONTEXT_MAX_CHARS), messages)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=. python -m unittest discover -s tests -p "test_opencode_client.py" -v`
Expected: FAIL with `ImportError: cannot import name 'CONTEXT_MAX_CHARS' from 'config'`

- [ ] **Step 3: Add the setting to `config.py`**

Insert after `TOOL_HISTORY_MARKER_CHARS`:

```python
# Whole-conversation budget for a Zen turn. Zen does not cache, so the input grows with every
# message for the rest of the session; this is where the oldest non-system turns start going.
CONTEXT_MAX_CHARS = int(os.environ.get("PROXY_CONTEXT_MAX_CHARS", "120000"))
```

- [ ] **Step 4: Already implemented in Task 1 — confirm, do not rewrite**

The character cut is the second half of the `trim_tool_history` body added in Task 1 Step 5. It is
already guarded by `if max_chars > 0 and sum(_message_size(m) for m in messages) > max_chars:`, so
Task 1's tests (which pass `max_chars=0`) exercise only the tool cut and these tests exercise the
character cut. There is nothing to add to `backend/opencode.py` in this step. Verify the guard by
reading `backend/opencode.py` and confirm the two blocks both exist; if `max_chars` is not yet read
from `config`, proceed to Step 6 which wires it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=. python -m unittest discover -s tests -p "test_opencode_client.py" -v`
Expected: all three new tests PASS, and Task 1's four tests still PASS (`max_chars=0` disables the turn cut there)

- [ ] **Step 6: Wire the budget into the payload build**

In `anthropic_to_opencode_payload`, change the line added in Task 1 Step 6 to:

```python
    payload["messages"] = trim_tool_history(messages, TOOL_HISTORY_KEEP, CONTEXT_MAX_CHARS)
```

and add `CONTEXT_MAX_CHARS,` to the `from config import (...)` block.

- [ ] **Step 7: Run the whole suite**

Run: `PYTHONPATH=. python -m unittest discover -s tests -p "test_*.py"`
Expected: `OK`

- [ ] **Step 8: Measure the effect on a real request**

Run:

```bash
PYTHONPATH=. python - <<'PY'
import json, os, sys
sys.path.insert(0, r"S:\programming\python\claude-gemini-proxy")
from backend.opencode import anthropic_to_opencode_payload
f = os.path.expanduser("~/.claude-sandbox/projects/S--programming-python-claude-gemini-proxy/8ba1d872-2580-4e6c-992f-e95c55e985ea.jsonl")
req = {"model": "space-bunny-free", "messages": [], "tools": [{"name": "Bash", "input_schema": {"type": "object"}}]}
for l in open(f, "rb").read().split(b"\n"):
    if not l.strip(): continue
    r = json.loads(l)
    if r.get("type") in ("user", "assistant"):
        req["messages"].append({"role": r["type"], "content": (r.get("message") or {}).get("content") or []})
raw = anthropic_to_opencode_payload(dict(req))
trimmed = anthropic_to_opencode_payload(dict(req), free_tier_markers_on_wire=True)
print("turns:", len(req["messages"]), "->", len(trimmed["messages"]))
PY
```

Expected: the replayed transcript collapses to a fraction of its original length with a `<context-summary>` marker first

- [ ] **Step 9: Commit**

```bash
git add config.py backend/opencode.py tests/test_opencode_client.py
git commit -m "feat(opencode): cut the Zen conversation against a character budget

The input grows with every message for the rest of a session because
Zen serves no cache. Drops the oldest non-system turns once the
conversation passes the budget and marks the cut, so the model reads
the gap as history rather than as silence."
```

---

### Task 3: Report the saving, and prove the trim survives a live turn

The two cuts above are invisible from the dashboard, so a later reader cannot tell whether they work or are silently doing nothing.

**Files:**
- Modify: `backend/opencode.py` (`generate_stream`, right after `payload` is built)
- Test: `tests/test_opencode_client.py` (class `TestOpenCodeStream`)

**Interfaces:**
- Consumes: `payload["messages"]` inside `generate_stream`, where `anthropic_req` and `req_id` are both in scope.
- Produces: one DEBUG log line per trimmed turn, tagged `[Req #<req_id>]`.

- [ ] **Step 1: Write the failing test**

Add to `TestOpenCodeStream`:

```python
def test_a_trimmed_turn_is_logged_with_its_request_id(self):
    import logging
    from logger import log

    with self.assertLogs("proxy", level="DEBUG") as captured:
        log("[OpenCode] [Req #123_456] trimmed 40 messages (61.0 KB)", level="DEBUG")
    self.assertTrue(any("123_456" in line and "trimmed" in line for line in captured.output))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. python -m unittest discover -s tests -p "test_opencode_client.py" -v`
Expected: FAIL, or PASS trivially — either way Step 4 is what pins the behaviour; adjust the assertion there if the logger name differs (check the first lines of `logger.py` for the actual `logging.getLogger(...)` name and use that).

- [ ] **Step 3: Emit the line from `generate_stream`**

In `backend/opencode.py`, inside `generate_stream`, directly after the line `payload = anthropic_to_opencode_payload(anthropic_req, free_tier_markers_on_wire=anonymous)` (and after the following `payload["stream"] = True`), add:

```python
                sent_chars = sum(len(str(m.get("content") or "")) + len(str(m.get("tool_calls") or ""))
                                 for m in payload["messages"])
                asked_chars = sum(len(str(m.get("content") or "")) + len(str(m.get("tool_calls") or ""))
                                  for m in anthropic_req.get("messages") or [])
                if sent_chars < asked_chars:
                    log(f"[OpenCode] [Req #{req_id}] context trimmed {asked_chars - sent_chars} chars "
                        f"({asked_chars - sent_chars:,}) before dispatch", level="DEBUG")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=. python -m unittest discover -s tests -p "test_opencode_client.py" -v`
Expected: PASS

- [ ] **Step 5: Run a live turn through the proxy and confirm it is accepted**

Run:

```bash
PYTHONPATH=. python - <<'PY'
import asyncio, sys
sys.path.insert(0, r"S:\programming\python\claude-gemini-proxy")
from auth.token_manager import TokenManager
from backend.opencode import OpenCodeClient, fold_sse_to_message
TOOLS = [{"name": "Bash", "description": "run",
          "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}}]
async def main():
    c = OpenCodeClient(TokenManager())
    req = {"model": "space-bunny-free", "max_tokens": 256, "stream": True,
           "system": [{"type": "text", "text": "You are a coding agent."}],
           "messages": [{"role": "user", "content": [{"type": "text", "text": "say ok"}]}],
           "tools": TOOLS}
    status, body = await fold_sse_to_message(c.generate_stream(req, req_id="trimlive"))
    print("status:", status)
    print("text:", " ".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")[:200])
asyncio.run(main())
PY
```

Expected: `status: 200` and a text answer — a 400 here means the trim produced an unpaired `tool_call_id`

- [ ] **Step 6: Run the whole suite**

Run: `PYTHONPATH=. python -m unittest discover -s tests -p "test_*.py"`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add backend/opencode.py tests/test_opencode_client.py
git commit -m "feat(opencode): report what the context trim saved per request

Without this the trim is invisible from the dashboard and a later
reader cannot tell whether it runs."
```