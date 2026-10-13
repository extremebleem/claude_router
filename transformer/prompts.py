"""
Model-Specific System Prompt Engineering for Gemini and Cloud Code models.
Applies optimal system instructions tailored to each model architecture and family,
while safely integrating user-defined project instructions (CLAUDE.md, git attribution).
"""

import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Union

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_prompt_file(filename: str, fallback: str = "") -> str:
    """Reads prompt template from prompts/ directory if present."""
    path = PROMPTS_DIR / filename
    if path.exists():
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return fallback

# Core Antigravity identity required by Google Cloud Code infrastructure
ANTIGRAVITY_BASE_PROMPT = (
    "You are Antigravity, a powerful agentic AI coding assistant designed by "
    "the Google Deepmind team working on Advanced Agentic Coding. You are pair "
    "programming with a USER to solve their coding task. The task may require "
    "creating a new codebase, modifying or debugging an existing codebase, "
    "or simply answering a question.**Absolute paths only****Proactiveness**"
)

# Core Guidelines for Coding Agents
CORE_CODING_GUIDELINES = (
    "Operating Guidelines:\n"
    "- Always use absolute paths (e.g. Windows style 'C:\\...' or POSIX) when interacting with filesystem tools.\n"
    "- For Edit tool calls: verify exact lines from Read first; match indentation (spaces vs tabs) with 100% precision; select a unique 3-10 line anchor for old_string to prevent 'String to replace not found'.\n"
    "- Do not re-read unchanged files: if a file was already read in the conversation and has not been edited, reuse its content.\n"
    "- For Bash tool calls: do not pass huge inline code snippets via 'python -c \"...\"' on Windows (causes ENAMETOOLONG); use native Write or Edit tool instead.\n"
    "- Preserve existing code structure, comments, and style unless explicitly asked to refactor.\n"
    "- Be concise, direct, and actionable. Do not output conversational filler or restate the user's request.\n"
    "- When calling tools, ensure all parameters strictly match the declared schema.\n"
    "- Proactively use search tools (e.g. WebSearch, Glob, Grep) when information or context is missing or external research is needed.\n"
    "- Never output speculative text when you can inspect files or run tests using available tools.\n"
    "- Use markdown code blocks with explicit language identifiers for all code and diffs."
)

MODEL_SPECIFIC_TUNING: Dict[str, str] = {
    # Gemini 3.8 / 3.6 / 3.5 / 3.0 Flash: Rapid, proactive, agentic execution
    "gemini-flash": (
        "Model Directive [Gemini Flash Agentic]:\n"
        "- Prioritize high-velocity, multi-step autonomous tool execution.\n"
        "- Call tools immediately without preparatory conversational text before the call.\n"
        "- Verify file states directly with inspection tools rather than assuming file contents.\n"
        "- Conclude tool actions with brief, high-density summaries of changes."
    ),

    # Gemini 3.1 Pro / 2.5 Pro: Deep architectural reasoning, correctness, and refactoring
    "gemini-pro": (
        "Model Directive [Gemini Pro High-Reasoning]:\n"
        "- Apply rigorous architectural analysis and edge-case evaluation.\n"
        "- Prefer surgical, minimal diffs to preserve codebase stability and prevent regression.\n"
        "- Check for backward compatibility and maintain complete type safety.\n"
        "- Thoroughly trace dependencies across modules before modifying public APIs."
    ),

    # Thinking Models: Deliberate reasoning inside thought tokens
    "thinking": (
        "Model Directive [Thinking & Deliberation]:\n"
        "- Formulate a complete execution plan in your internal thought before emitting responses.\n"
        "- Verify parameter validity and tool dependencies within your thoughts.\n"
        "- Keep the visible response strictly focused on results and deliverables; do not duplicate internal reasoning into visible text.\n"
        "- Never emit raw tool calls or fake function call syntax inside thought blocks."
    ),

    # Claude Models hosted on Google Cloud Code
    "claude": (
        "Model Directive [Claude Cloud Code]:\n"
        "- Maintain disciplined software engineering standards.\n"
        "- Inspect tool execution results carefully and self-correct errors immediately.\n"
        "- Deliver clean, production-ready code with comprehensive error handling."
    ),

    # Ultra-Lightweight Models: Fast turnaround, simplicity
    "lite": (
        "Model Directive [Flash Lite]:\n"
        "- Keep operations simple, direct, and focused.\n"
        "- Answer immediately with minimum latency and clear, unambiguous output."
    ),

    # Open-weights Models
    "gpt-oss": (
        "Model Directive [GPT-OSS]:\n"
        "- Follow standard declarative formatting.\n"
        "- Ensure strictly compliant JSON arguments for all tool calls."
    ),

    # Muse Spark family (Meta Superintelligence Labs multimodal coding agent)
    "muse-spark": (
        "Model Directive [Muse Spark Coding Agent]:\n"
        "- Operate as Muse Spark, a state-of-the-art multimodal software engineering agent.\n"
        "- Prioritize precise, high-velocity code modifications and verified repo-level changes.\n"
        "- Adhere strictly to tool schemas and emit clean, syntactically correct code blocks.\n"
        "- Execute autonomous problem solving with minimal conversational overhead."
    ),
}

# Identities or terms that trigger false 429/blocked responses in Cloud Code
IDENTITY_SCRUBS = [
    (r"\bNous Research\b", "the assistant team"),
    (r"\bHermes Agent\b", "the assistant"),
    (r"\bHermes\b", "the assistant"),
    (r"\bClaude Code\b", "Antigravity"),
    (r"Anthropic's official CLI for Claude", "Google Antigravity Coding Assistant"),
    (r"\bAnthropic\b", "Google DeepMind"),
]


def sanitize_user_system_text(text: str) -> str:
    """Sanitizes client system prompt text, scrubbing problematic identities and Anthropic billing headers."""
    if not isinstance(text, str):
        return ""
    cleaned = text
    # Strip Anthropic billing header that triggers Google Cloud Code 429 RESOURCE_EXHAUSTED
    cleaned = re.sub(r"x-anthropic-billing-header[^\n]*\n?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"cc_version=[^\n;]*;?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"cc_entrypoint=[^\n;]*;?", "", cleaned, flags=re.IGNORECASE)
    for pattern, replacement in IDENTITY_SCRUBS:
        cleaned = re.sub(pattern, replacement, cleaned)
    return cleaned.strip()


def extract_user_system_prompt(raw_system: Optional[Union[str, List[Any]]]) -> str:
    """
    Extracts, sanitizes, and modernizes client system text:
    - Strips all technical billing headers (x-anthropic-billing-header, cc_version)
    - Strips Claude Code / Anthropic identity declarations
    - Updates # Environment section with real Antigravity details
    - Injects auto mode native tools policy (mandatory Read/Edit/Write tools, no python scripts)
    """
    if not raw_system:
        return ""

    chunks: List[str] = []
    if isinstance(raw_system, str):
        chunks.append(raw_system)
    elif isinstance(raw_system, list):
        for block in raw_system:
            if isinstance(block, str):
                text = block
            elif isinstance(block, dict) and "text" in block:
                text = block.get("text", "")
            else:
                continue

            text_lower = text.lower()
            # 1. Skip technical billing info
            if "x-anthropic-billing-header" in text_lower or "cc_version=" in text_lower or "cc_entrypoint=" in text_lower:
                continue

            # 2. Skip Claude Code / Anthropic identity declarations
            if "you are claude code" in text_lower or "anthropic's official cli" in text_lower:
                continue

            # 3. Skip Muse Spark identity block if coming from muse-spark-code (handled by base prompt)
            if "you are muse spark" in text_lower and "visual studio code through the muse spark code extension" in text_lower:
                continue

            clean_block = text.strip()
            if clean_block:
                chunks.append(clean_block)

    combined = "\n\n".join(chunks)

    # 3. Replace Environment section if present with updated Antigravity environment
    env_content = load_prompt_file("environment.md")
    if env_content:
        # Match "# Environment", "# Environment Context", or "<environment>"
        env_pattern = r"(?:# Environment[^\n]*|<environment>)\s*\n.*?(?=\n# |\n</environment>|\Z)"
        if re.search(env_pattern, combined, flags=re.DOTALL):
            combined = re.sub(env_pattern, env_content + "\n", combined, flags=re.DOTALL)

    # 4. In auto mode / background session: inject autonomous policy only when session is in auto/background mode
    auto_mode_content = load_prompt_file("auto_mode.md")
    if auto_mode_content:
        if "# Background Session" in combined:
            combined = combined.replace("# Background Session", f"# Background Session\n\n{auto_mode_content}\n")
        elif "permissionmode: auto" in combined.lower() or "auto mode" in combined.lower():
            combined = f"{combined}\n\n{auto_mode_content}"

    return sanitize_user_system_text(combined)


def build_system_instruction(
    model_name: str,
    user_system: Optional[Union[str, List[Any]]] = None
) -> List[Dict[str, str]]:
    """
    Builds the model-specific system instruction parts for Google Cloud Code / Gemini API.
    Guarantees the required Antigravity base identity while appending optimized guidelines
    and safely integrating the client's custom system prompt (CLAUDE.md, instructions).
    """
    m = model_name.lower()
    antigravity_identity = load_prompt_file("antigravity_identity.md", fallback=ANTIGRAVITY_BASE_PROMPT)

    parts: List[Dict[str, str]] = [
        {"text": antigravity_identity},
        {"text": CORE_CODING_GUIDELINES}
    ]

    # 1. Thinking models (e.g. gemini-2.5-flash-thinking, claude-opus-4-6-thinking)
    if "thinking" in m:
        parts.append({"text": MODEL_SPECIFIC_TUNING["thinking"]})
        if "claude" in m:
            parts.append({"text": MODEL_SPECIFIC_TUNING["claude"]})
            parts.append({
                "text": "Interleaved thinking is enabled. You may think between tool calls and after receiving tool results before deciding the next action or final answer."
            })
        elif "muse" in m or "spark" in m:
            parts.append({"text": MODEL_SPECIFIC_TUNING["muse-spark"]})
        elif "pro" in m:
            parts.append({"text": MODEL_SPECIFIC_TUNING["gemini-pro"]})
        else:
            parts.append({"text": MODEL_SPECIFIC_TUNING["gemini-flash"]})

    # 2. Muse Spark family
    elif "muse" in m or "spark" in m:
        parts.append({"text": MODEL_SPECIFIC_TUNING["muse-spark"]})

    # 3. Claude models
    elif "claude" in m:
        parts.append({"text": MODEL_SPECIFIC_TUNING["claude"]})

    # 4. Lite models
    elif "lite" in m or "extra-low" in m:
        parts.append({"text": MODEL_SPECIFIC_TUNING["lite"]})

    # 5. Pro models (Gemini 3.1 Pro, Gemini 2.5 Pro)
    elif "pro" in m:
        parts.append({"text": MODEL_SPECIFIC_TUNING["gemini-pro"]})

    # 6. Gemini Flash family (Gemini 3.8 Flash, 3.6 Flash, 3.5 Flash, 3 Flash)
    elif any(k in m for k in ["3.8", "3.6", "3.5", "3.1", "flash", "agent"]):
        parts.append({"text": MODEL_SPECIFIC_TUNING["gemini-flash"]})

    # 7. GPT-OSS family
    elif "gpt" in m:
        parts.append({"text": MODEL_SPECIFIC_TUNING["gpt-oss"]})

    # 8. Default fallback based on model capability
    else:
        if "pro" in m or "opus" in m or "high" in m:
            parts.append({"text": MODEL_SPECIFIC_TUNING["gemini-pro"]})
        else:
            parts.append({"text": MODEL_SPECIFIC_TUNING["gemini-flash"]})

    # Safely append client user system prompt if provided
    client_prompt = extract_user_system_prompt(user_system)
    if client_prompt:
        parts.append({
            "text": f"User and Environment Directives:\n{client_prompt}"
        })

    return parts
