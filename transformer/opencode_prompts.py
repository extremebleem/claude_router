"""
System prompts for the OpenCode Zen backend, mirroring what the opencode app sends.

Sources in github.com/anomalyco/opencode (formerly sst/opencode), packages/core/src:
- session/runner/prompt/system.txt + session/system-prompt.ts: base prompt and tool guidance
- plugin/optimize.ts + plugin/system-prompt/*.txt: per-model overrides (gpt, kimi, trinity, muse) and the claude append
The text files live in prompts/opencode/ and can be edited without code changes.

Every override template carries the ${OPENCODE_TOOL_GUIDANCE} placeholder; without it the
shell/write/edit guidance would be silently dropped for that model.
"""

import re
from typing import Any, List, Optional, Union

from transformer.prompts import load_prompt_file

TOOL_GUIDANCE_PLACEHOLDER = "${OPENCODE_TOOL_GUIDANCE}"
# Matches the placeholder alone or followed by its line break, so the whole line goes.
_GUIDANCE_PLACEHOLDER_RE = re.compile(re.escape(TOOL_GUIDANCE_PLACEHOLDER) + r"\n?")

# Claude Code appends its own context-budget bookkeeping to the system prompt and interleaves
# system turns between every model turn. It is not an instruction for the model, and passing it
# through wastes context and confuses the model. Set to False to send it verbatim.
DROP_TOKEN_ACCOUNTING = True
# The accounting word is optional and so are "left"/"remaining": Claude Code has shipped both
# '<total_tokens>14976683 tokens left</total_tokens>' and the bare '<total_tokens>14976683</total_tokens>',
# and a mismatch here would turn every bookkeeping turn back into a noise message.
# The tag body may not contain sentence punctuation, so a genuine instruction that merely quotes a
# token count survives.
_TOKEN_BODY = r"\s*[\d.,_]+\s*(?:tokens?\s*)?(?:left|remaining)?[^<.!?\n]*"
_TOKEN_ACCOUNTING_RE = re.compile(
    rf"(?:<total_tokens>{_TOKEN_BODY}</total_tokens>\s*)+",
    re.IGNORECASE,
)
_TOKEN_ACCOUNTING_LINE_RE = re.compile(
    rf"^[ \t]*<total_tokens>{_TOKEN_BODY}</total_tokens>[ \t]*\n?",
    re.IGNORECASE | re.MULTILINE,
)


def is_token_accounting(text: str) -> bool:
    """True for pure Claude Code token-budget bookkeeping such as '<total_tokens>100 left</total_tokens>'."""
    stripped = (text or "").strip()
    return bool(stripped) and _TOKEN_ACCOUNTING_RE.fullmatch(stripped) is not None


def strip_token_accounting(text: str) -> str:
    """Drops the budget bookkeeping from a block that also carries real content."""
    return _TOKEN_ACCOUNTING_LINE_RE.sub("", text or "").strip()

# Client tool name (Claude Code) -> guidance key; opencode names are shell/write/edit
_TOOL_KEYS = {"bash": "shell", "shell": "shell", "write": "write", "edit": "edit"}

_GUIDANCE = {
    "shell": [
        "- Prefer dedicated tools over shell commands; fall back to the shell when a tool cannot do what you need.",
        "- Do not chain shell commands with separators like `echo \"====\";` or `printf '---'`; the output becomes noisy in a way that makes the user's side of the conversation worse.",
    ],
    "write": [
        "- Use the write tool to create files or completely replace their content. Prefer using the edit tool for targeted changes.",
    ],
    # Claude Code's Edit takes old_string/new_string/replace_all where opencode's takes oldString/newString/replaceAll
    "edit": [
        "- Use the edit tool for targeted changes to existing text files. It replaces the exact text in `old_string` with `new_string`, and the values must differ. By default, `old_string` must occur exactly once. If it occurs multiple times, include more surrounding context to make it unique or set `replace_all` to true to replace every occurrence.",
    ],
}


def render_prompt(template: str, tool_names: List[str]) -> str:
    """Port of SessionSystemPrompt.render: fills the tool guidance for the tools the request carries."""
    keys = {_TOOL_KEYS[n.lower()] for n in tool_names if n.lower() in _TOOL_KEYS}
    guidance = "\n".join(line for key in ("shell", "write", "edit") if key in keys for line in _GUIDANCE[key])
    # Own line when there is guidance, dropped entirely otherwise so no blank line is left behind.
    # The trailing newline is part of the match so the placeholder's own line disappears with it,
    # whether or not it happens to be the last line of the file.
    filled = f"{guidance}\n" if guidance else ""
    return _GUIDANCE_PLACEHOLDER_RE.sub(lambda _: filled, template)


def model_prompt(model: str) -> tuple:
    """Port of plugin/optimize.ts: returns (file, mode) for the model, or None to keep the base prompt."""
    m = (model or "").lower().split("/")[-1]
    if "gpt" in m:
        return ("gpt-astra.txt" if "astra" in m else "gpt.txt"), "override"
    if "claude" in m:
        return "anthropic.txt", "append"
    if "kimi" in m:
        return "kimi.txt", "override"
    if "trinity" in m:
        return "trinity.txt", "override"
    if "muse" in m:
        return "meta.txt", "override"
    return None


def _client_system_text(raw_system: Optional[Union[str, List[Any]]]) -> str:
    """Client (Claude Code) system text without its identity line and billing header."""
    if not raw_system:
        return ""
    blocks = [raw_system] if isinstance(raw_system, str) else raw_system
    chunks = []
    for block in blocks:
        text = block if isinstance(block, str) else (block.get("text", "") if isinstance(block, dict) else "")
        lower = text.lower()
        if "x-anthropic-billing-header" in lower or "cc_version=" in lower:
            continue
        if "you are claude code" in lower or "anthropic's official cli" in lower:
            continue
        if DROP_TOKEN_ACCOUNTING:
            if is_token_accounting(text):
                continue
            text = strip_token_accounting(text)
        if text.strip():
            chunks.append(text.strip())
    return re.sub(r"\bClaude Code\b", "OpenCode", "\n\n".join(chunks))


def build_opencode_system(
    model: str,
    raw_system: Optional[Union[str, List[Any]]],
    tool_names: List[str],
) -> str:
    """opencode's own system prompt for the model, followed by the client's instructions (CLAUDE.md etc.)."""
    base = load_prompt_file("opencode/system.txt")
    chosen = model_prompt(model)
    if chosen:
        name, mode = chosen
        template = load_prompt_file(f"opencode/{name}")
        if template:
            template = template.replace("{{MODEL_NAME}}", (model or "").split("/")[-1])
            rendered = render_prompt(template, tool_names)
            base = f"{render_prompt(base, tool_names)}\n\n{rendered}" if mode == "append" else rendered
        else:
            base = render_prompt(base, tool_names)
    else:
        base = render_prompt(base, tool_names)
    return "\n\n".join(p for p in (base, _client_system_text(raw_system)) if p)
