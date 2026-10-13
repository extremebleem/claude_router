# Custom Prompts Configuration

This directory contains markdown files used by `claude-gemini-proxy` to shape and sanitize the system prompts before they are sent to upstream models (Google Cloud Code / Gemini API).

## Files:
- `antigravity_identity.md`: Replaces Claude Code / Anthropic identity with the native Antigravity persona.
- `auto_mode.md`: Enforces mandatory use of native `Read`, `Edit`, `Write` tools (preventing the model from creating ad-hoc Python scripts to edit files) and sets autonomous execution rules.
- `environment.md`: Replaces the environment description with actual platform and Gemini model details.

Any changes made to these files will be automatically loaded by the proxy.

## prompts/opencode/
System prompts copied from the opencode app (github.com/anomalyco/opencode, formerly `sst/opencode`, `packages/core/src`), used for OpenCode Zen models by `transformer/opencode_prompts.py`:
- `system.txt`: base prompt for every model. `${OPENCODE_TOOL_GUIDANCE}` is filled from the tools in the request.
- `gpt.txt`, `gpt-astra.txt`, `kimi.txt`, `trinity.txt`, `meta.txt`: replace the base prompt when the model id contains `gpt`, `kimi`, `trinity` or `muse`.
- `anthropic.txt`: appended to the base prompt for `claude` models.

Every file above **must** contain `${OPENCODE_TOOL_GUIDANCE}` — the override templates replace `system.txt` outright, so without the placeholder the `shell`/`write`/`edit` guidance is silently dropped for that model. `tests/test_opencode_client.py` asserts this.

## prompts/claude_cli_agent.md
Our own prompt for the Zen provider: the CLI agent harness the model is driven by, plus the tools the request actually carries. `${CLAUDE_CLI_TOOL_GUIDANCE}` is filled from `_CLI_TOOLS` in `transformer/opencode_prompts.py` with one line per tool present in `request.tools` — a tool the catalog does not know (an MCP tool, a renamed one) gets no line and the whole section disappears. Add new tools to `_CLI_TOOLS`, not here.

Zen receives these as **three system messages**, in this order (`build_opencode_system`):
1. opencode's own prompt, verbatim, so the gateway sees the prefix it sees from the opencode client;
2. this file, describing the harness and the tools of that request;
3. the client's own instructions (CLAUDE.md, environment) — dropped when it sends none.

The Antigravity identity never goes to Zen; it belongs to the Cloud Code backend only. Set `OPENCODE_MERGE_SYSTEM_BLOCKS=1` to fold all three into a single system message if a gateway ever refuses more than one leading system turn.
