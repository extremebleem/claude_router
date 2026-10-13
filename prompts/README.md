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

The client's own system text (CLAUDE.md etc.) follows the opencode prompt; the Claude Code identity line and billing header are dropped.
