# Custom Prompts Configuration

This directory contains markdown files used by `claude-gemini-proxy` to shape and sanitize the system prompts before they are sent to upstream models (Google Cloud Code / Gemini API).

## Files:
- `antigravity_identity.md`: Replaces Claude Code / Anthropic identity with the native Antigravity persona.
- `auto_mode.md`: Enforces mandatory use of native `Read`, `Edit`, `Write` tools (preventing the model from creating ad-hoc Python scripts to edit files) and sets autonomous execution rules.
- `environment.md`: Replaces the environment description with actual platform and Gemini model details.

Any changes made to these files will be automatically loaded by the proxy.
