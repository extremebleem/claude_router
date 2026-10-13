"""
System prompts for the OpenCode Zen backend, mirroring what the opencode app sends.

Sources in github.com/anomalyco/opencode (formerly sst/opencode), packages/core/src:
- session/runner/prompt/system.txt + session/system-prompt.ts: base prompt and tool guidance
- plugin/optimize.ts + plugin/system-prompt/*.txt: per-model overrides (gpt, kimi, trinity, muse) and the claude append
The text files live in prompts/opencode/ and can be edited without code changes.

Every override template carries the ${OPENCODE_TOOL_GUIDANCE} placeholder; without it the
shell/write/edit guidance would be silently dropped for that model.

Zen receives three system blocks, each its own message (build_opencode_system):
  1. opencode's prompt, verbatim, so the gateway sees the prefix it sees from the opencode client;
  2. prompts/claude_cli_agent.md - the CLI agent harness and the tools this request carries, with
     ${CLAUDE_CLI_TOOL_GUIDANCE} filled from _CLI_TOOLS;
  3. the client's own instructions (CLAUDE.md, environment).
Nothing here carries the Antigravity identity: that is for the Cloud Code backend only.
"""

import re
from typing import Any, Dict, List, Optional, Union

from transformer.prompts import load_prompt_file

TOOL_GUIDANCE_PLACEHOLDER = "${OPENCODE_TOOL_GUIDANCE}"
CLAUDE_CLI_TOOL_GUIDANCE_PLACEHOLDER = "${CLAUDE_CLI_TOOL_GUIDANCE}"
# Matches the placeholder alone or followed by its line break, so the whole line goes.
_GUIDANCE_PLACEHOLDER_RE = re.compile(re.escape(TOOL_GUIDANCE_PLACEHOLDER) + r"\n?")
_CLI_GUIDANCE_PLACEHOLDER_RE = re.compile(re.escape(CLAUDE_CLI_TOOL_GUIDANCE_PLACEHOLDER) + r"\n?")

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


# The CLI agent's tools, keyed by the name Claude Code puts in request.tools. One line each, taken
# from the harness' own tool help; the second system block lists exactly the ones a request carries.
# Insertion order is the render order, so the block stays byte-stable for a given tool set and the
# gateway keeps its prefix cache. A tool missing here (an MCP server, a renamed one) is simply not
# described in prose - its schema already reaches the model through payload["tools"].
_CLI_TOOLS: Dict[str, str] = {
    # Filesystem
    "Read": "Reads a file from the local filesystem; the output is line-numbered, so a line it shows can be quoted straight into an Edit.",
    "Write": "Writes a file to the local filesystem, creating it or replacing its content entirely.",
    "Edit": "Replaces one exact string in an existing text file; `old_string` must match character for character (indentation included) and occur exactly once unless `replace_all` is set.",
    "NotebookEdit": "Replaces, inserts or deletes a single cell of a Jupyter notebook (.ipynb).",
    "Glob": "Fast file pattern matching by glob (`**/*.js`, `src/**/*.ts`); returns matching paths sorted by modification time.",
    "Grep": "Content search built on ripgrep; prefer it over grep/rg through Bash, its results integrate with file links.",
    # Execution
    "Bash": "Executes a bash command and returns its output; the only way to run a command, a build, a test or git.",
    # Web
    "WebSearch": "Searches the web and returns result blocks with titles and URLs.",
    "WebFetch": "Fetches a URL, converts the page to markdown and answers a prompt against it with a small fast model.",
    # Agents and tasks
    "Agent": "Launches a subagent for a complex multi-step task; every agent type brings its own capabilities and tools.",
    "Skill": "Invokes a skill, i.e. a packaged instruction set for one particular kind of task.",
    "SendMessage": "Sends a message to another agent or session by the name ListAgents prints.",
    "ListAgents": "Lists the agents and sessions that SendMessage can address.",
    "TaskCreate": "Creates a task in the session task list, to track multi-step work.",
    "TaskUpdate": "Updates a task in the session task list (status, description, owner).",
    "TaskGet": "Returns one task from the session task list by id.",
    "TaskList": "Lists the tasks of the session task list.",
    "TaskStop": "Stops a running background task by id.",
    "Workflow": "Runs a workflow script that orchestrates several subagents deterministically in the background.",
    "ScheduleWakeup": "Schedules when to resume work, used by /loop sessions without an interval.",
    "CronCreate": "Schedules a prompt to be enqueued at a future time, recurring or one-shot.",
    "CronDelete": "Cancels a job previously scheduled with CronCreate.",
    "CronList": "Lists the jobs scheduled with CronCreate.",
    # Session and interaction
    "AskUserQuestion": "Asks the user to choose between options, for a decision that is genuinely theirs to make.",
    "EnterPlanMode": "Switches into plan mode to research an approach and get sign-off before implementing it.",
    "ExitPlanMode": "Leaves plan mode by presenting the finished plan for approval.",
    "EnterWorktree": "Creates an isolated git worktree and moves the session into it; only on an explicit request.",
    "ExitWorktree": "Leaves a worktree session created by EnterWorktree and returns to the original directory.",
    "DesignSync": "Reads and updates the user's design-system projects through their claude.ai login, for the /design-sync skill.",
    "ReportFindings": "Reports code-review findings as a typed list the host UI renders.",
}


CLI_TOOL_SECTION_HEADING = "# Tools available in this session"


def render_claude_cli_prompt(template: str, tool_names: List[str]) -> str:
    """Fills the CLI agent prompt with the tools this request actually carries.

    A tool the catalog does not know gets no prose line: the heading and the placeholder block are
    dropped together rather than leaving an empty section, the same way the opencode guidance
    placeholder behaves. The names are the ones the client sent and the schemas carry, because
    nothing on the wire renames them.
    """
    present = {n for n in tool_names if n in _CLI_TOOLS}
    listing = "\n".join(f"- **{name}**: {_CLI_TOOLS[name]}" for name in _CLI_TOOLS if name in present)
    filled = f"{CLI_TOOL_SECTION_HEADING}\n{listing}\n" if listing else ""
    return _CLI_GUIDANCE_PLACEHOLDER_RE.sub(lambda _: filled, template)


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
    if "bunny" in m:
        return "space-bunny.txt", "append"
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
) -> List[str]:
    """The system blocks Zen receives, in order:

    1. opencode's own prompt for the model - verbatim, so the gateway sees the same prefix it would
       see from the opencode client itself.
    2. the CLI agent prompt: what harness the model is driven by and which tools it has, rendered
       from the tools of this request. It is a separate block on purpose: block 1 must stay
       byte-identical to upstream, and the tool list changes with the client's tool set while
       block 1 does not.
    3. the client's own instructions (CLAUDE.md, environment), dropped when it sends none.
    """
    blocks = [_opencode_prompt(model, tool_names), _claude_cli_prompt(tool_names)]
    client = _client_system_text(raw_system)
    if client:
        blocks.append(client)
    return [b for b in blocks if b]


def _opencode_prompt(model: str, tool_names: List[str]) -> str:
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
    return base


def _claude_cli_prompt(tool_names: List[str]) -> str:
    return render_claude_cli_prompt(load_prompt_file("claude_cli_agent.md"), tool_names)
