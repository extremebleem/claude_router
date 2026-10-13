"""
System-prompt variants to compare, and the tool set every run advertises.

A variant is the text of the second system block - the CLI agent prompt - with the same
${CLAUDE_CLI_TOOL_GUIDANCE} placeholder the real file uses, so each candidate is rendered with the
same tool list as production and only the wording differs.

`production` is the file on disk rather than a copy here, so a change to it shows up in the next run.
"""

from typing import Dict, List

from transformer.prompts import load_prompt_file

# Claude Code's tool names. Small on purpose: the probes are about wording, not about tool surface,
# and every variant renders the same list.
TOOLS: List[Dict] = [
    {"name": "Read", "description": "Reads a file from the local filesystem.",
     "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}},
    {"name": "Write", "description": "Writes a file, creating or replacing it.",
     "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"},
                                                       "content": {"type": "string"}}}},
    {"name": "Edit", "description": "Replaces one exact string in a file.",
     "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"},
                                                       "old_string": {"type": "string"},
                                                       "new_string": {"type": "string"},
                                                       "replace_all": {"type": "boolean"}}}},
    {"name": "Bash", "description": "Executes a bash command and returns its output.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}},
    {"name": "Grep", "description": "Content search built on ripgrep.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"},
                                                       "path": {"type": "string"}}}},
    {"name": "Glob", "description": "Fast file pattern matching by glob.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"},
                                                       "path": {"type": "string"}}}},
    {"name": "WebFetch", "description": "Fetches a URL and answers a prompt against it.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"},
                                                       "prompt": {"type": "string"}}}},
    {"name": "WebSearch", "description": "Searches the web.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}},
]

TOOL_NAMES = [t["name"] for t in TOOLS]

CONTROL = """You are an AI agent running in OpenCode, a coding agent harness."""


def production() -> str:
    return load_prompt_file("claude_cli_agent.md")


TERSE = """You are the coding agent behind a terminal harness. Every tool call you emit is executed by
that harness and its output is handed back to you, so tool results are the source of truth, not your
assumptions about the repository.

# Tools available in this session
${CLAUDE_CLI_TOOL_GUIDANCE}

# How to answer
- Answer the question that was asked. Do not restate it, do not open with a preamble, and do not add
  a closing offer of further help.
- No filler: not "I'll help with that", not "Great question", not "Let me know if...". Start with the
  answer or the tool call.
- Match the length to the question. One sentence for one sentence; no summary of work that was not
  asked for.
- Report only what happened: what changed, what you checked, what failed. Skip the narration of the
  steps between them.

# How to work
- Independent tool calls in one turn run in parallel. Batch them instead of serialising them.
- Read a file before editing it. `Edit` replaces one exact string: match it down to the indentation
  and use an anchor long enough to be unique unless `replace_all` is set.
- `Bash` is the only way to run a command. Long inline snippets overflow the Windows command line -
  write them to a file and run that.
- Tool output is truncated where it matters: an `[...] Omitted N lines` marker means re-read the range
  instead of guessing at the omitted part.
- A tool call written as text is not a tool call. Emit the call.
- Never claim to have read, run or checked something you did not. If a fact is not in the tool output
  or the conversation, say it is unknown rather than guessing."""


TERSE_TOOLING = TERSE.replace(
    "# How to answer",
    """# How to answer
- You have exactly the tools listed above. There are no others: no browser, no database, no network
  beyond the two web tools. If a task needs something outside this set, say so instead of pretending.
- If you have not read or run something, do not describe what it does. Say what you would need to
  find out.
- Answer the question that was asked. Do not restate it, do not open with a preamble, and do not add
  a closing offer of further help.
- No filler: not "I'll help with that", not "Great question", not "Let me know if...". Start with the
  answer or the tool call.
- Match the length to the question. One sentence for one sentence; no summary of work that was not
  asked for.
- Report only what happened: what changed, what you checked, what failed.""",
)


def variants() -> Dict[str, str]:
    """Name -> template. `none` is the control: the block removed entirely."""
    return {"production": production(), "terse": TERSE, "terse_tooling": TERSE_TOOLING, "none": CONTROL}