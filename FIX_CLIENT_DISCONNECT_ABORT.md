# Specification & Agent Prompt: Graceful Client Disconnection & Stream Abort Handling

## 1. Problem Overview & Root Cause Analysis

### Symptoms
When Claude Code cancels an in-flight query (e.g., during subagent orchestration, switching git worktrees, entering plan mode, or user pressing `Esc`), the terminal console is polluted with an unhandled traceback:
```
> Error handling request from 127.0.0.1
Traceback (most recent call last):
  File ".../aiohttp/web_protocol.py", line 575, in _handle_request
    resp = await request_handler(request)
  File "S:\programming\python\claude-gemini-proxy\server.py", line 763, in handle_messages
    await response.prepare(request)
  File ".../aiohttp/web_response.py", line 470, in _start
    await self._write_headers()
  File ".../aiohttp/http_writer.py", line 231, in send_headers
ConnectionResetError: [WinError 10054] An existing connection was forcibly closed by the remote host
```

### Root Causes
1. **Unprotected Stream Handshake (`response.prepare`)**:
   In `server.py` (`handle_messages`, `handle_classifier_stub`, `handle_state_classifier_stub`), `await response.prepare(request)` is executed without catching socket disconnect exceptions. If the client closes the TCP socket before or during headers transmission, `aiohttp.http_writer.send_headers()` raises `ConnectionResetError` (or `BrokenPipeError` on Linux / macOS).
2. **Uncaught Stream Write Loop (`response.write` / `response.drain`)**:
   During active SSE streaming, if the client drops the connection midway, `await response.write(...)` or `await response.drain()` throws `ConnectionResetError` or `asyncio.CancelledError`.
3. **Leaked Upstream Resources**:
   If an uncaught exception is thrown before entering the streaming loop, the upstream connection (`upstream_resp`) may remain open longer than necessary, consuming unnecessary tokens/sockets.
4. **Console Pollution in Shared Process/Terminal**:
   Because `launcher.py` runs the proxy server in a background thread or shared process output, unhandled exceptions bubbling up to `aiohttp.web_protocol` trigger `logger.exception("Error handling request")`, printing directly to standard error (`stderr`).

---

## 2. Solution Architecture

1. **Graceful Client Disconnect Handling**:
   - Wrap all `response.prepare(request)`, `response.write(...)`, `response.drain()`, and `response.write_eof()` in `try ... except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, asyncio.CancelledError)`.
   - On disconnect, immediately close the upstream Google stream (`upstream_resp.close()`) to avoid token waste.
   - Log a quiet `DEBUG` message instead of letting `aiohttp` dump a traceback to `stderr`.
2. **Global aiohttp Protocol Error Suppression**:
   - Configure a custom error handler or server logger filter for `aiohttp.web.Application` to ensure that even low-level socket reset errors during HTTP header negotiation are caught and suppressed from printing tracebacks to the console.
3. **Comprehensive Test Coverage**:
   - Add unit tests verifying that simulated client disconnects (`ConnectionResetError` during `prepare` or streaming) complete cleanly without unhandled exceptions.

---

## 3. Ready-to-Use Agent Prompt

Below is the complete, self-contained prompt to give to an AI agent or developer to implement the fix:

```markdown
### Task: Implement Graceful Client Disconnect & Stream Cancellation Handling in Claude-Gemini Proxy

#### Context:
When Claude Code cancels an ongoing request (e.g. while launching subagents, switching worktrees, entering plan mode, or pressing Escape), it abruptly closes the TCP connection to the proxy. Currently, this causes `ConnectionResetError: [WinError 10054]` in `server.py` at `await response.prepare(request)` or `await response.write()`, which bubbles up to `aiohttp.web_protocol` and dumps a multi-line traceback into the terminal.

#### Objectives:
1. Prevent `ConnectionResetError`, `ConnectionAbortedError`, `BrokenPipeError`, and `asyncio.CancelledError` from dumping stack traces into the user console.
2. Gracefully handle client cancellations: immediately terminate and close the upstream Google Cloud Code / Gemini connection (`upstream_resp.close()`) to prevent resource and token leaks.
3. Apply this graceful handling across all streaming endpoints:
   - `handle_messages()` (main conversation streaming)
   - `handle_classifier_stub()` (permission classifier streaming)
   - `handle_state_classifier_stub()` (state classifier streaming)
4. Silence low-level socket reset warnings in aiohttp's protocol logger.
5. Add unit tests verifying that client disconnections during response preparation or mid-stream are handled cleanly with zero unhandled exceptions.

#### Implementation Details:

1. In `server.py`:
   - Define a tuple of disconnect exceptions:
     ```python
     CLIENT_DISCONNECT_ERRORS = (
         ConnectionResetError,
         ConnectionAbortedError,
         BrokenPipeError,
         asyncio.CancelledError,
     )
     ```
   - In `handle_messages()`:
     Enclose `response.prepare(request)` and the entire SSE streaming loop inside a `try ... except CLIENT_DISCONNECT_ERRORS` block:
     ```python
     if stream:
         response = web.StreamResponse(...)
         try:
             await response.prepare(request)
             event_generator = parse_google_sse_stream(upstream_resp.content, original_model)
             async for event in event_generator:
                 # Check if client disconnected before writing
                 ev_type = event.get("type", "message")
                 payload = f"event: {ev_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                 await response.write(payload.encode("utf-8"))
                 await response.drain()
             await response.write_eof()
         except CLIENT_DISCONNECT_ERRORS:
             log(f"[Req #{req_id}] ℹ Client disconnected / aborted request early.", level="DEBUG")
         finally:
             upstream_resp.close()
         return response
     ```
   - In `handle_classifier_stub()` and `handle_state_classifier_stub()`:
     Wrap `await response.prepare(request)` and event writes in `try ... except CLIENT_DISCONNECT_ERRORS: pass`.
   - In `aiohttp` server setup:
     Ensure the aiohttp protocol error logger suppresses `ConnectionResetError` so that tracebacks never reach `stderr`.

2. In `tests/test_server.py`:
   - Add unit test `test_streaming_client_abrupt_disconnect`:
     Simulate a client that triggers `ConnectionResetError` during `prepare` or stream consumption.
     Assert that the server handler handles it gracefully without raising unhandled exceptions.

3. Verify:
   - Run `python -m unittest discover -s tests` and confirm all tests pass.
   - Ensure clean `git status`.
```
