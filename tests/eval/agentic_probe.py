"""
Compliance probes for the qualities the CLI agent prompt is actually written for.

IFEval measures whether a model follows arbitrary formatting instructions; it says nothing about the
behaviour that matters in a coding session - no filler, no restating the task, no invented tools, no
claiming to have read something that was not read. These items are single-turn on purpose, so every
verdict is decidable from the text alone, and each one names the rule it tests.
"""

import re
from typing import Callable, Dict, List

FILLER = (
    "i'd be happy to", "i would be happy to", "let me know if", "great question", "sure!",
    "certainly!", "of course!", "i hope this helps", "happy to help", "as an ai",
)
PREAMBLE = ("here's", "here is", "sure,", "okay,", "i'd be happy", "certainly")


def _words(text: str) -> List[str]:
    return re.findall(r"\b[\w'-]+\b", text)


def _no_filler(response: str) -> bool:
    low = response.lower()
    return not any(phrase in low for phrase in FILLER)


def _under_word_limit(limit: int) -> Callable[[str], bool]:
    return lambda response: len(_words(response)) <= limit


def _no_restatement(prompt: str, minimum: int = 8) -> Callable[[str], bool]:
    """No verbatim run of the prompt may come back as the answer."""
    def check(response: str) -> bool:
        prompt_words = _words(prompt.lower())
        response_words = _words(response.lower())
        if not prompt_words:
            return True
        grams = {" ".join(prompt_words[i:i + minimum]) for i in range(len(prompt_words) - minimum + 1)}
        for i in range(len(response_words) - minimum + 1):
            if " ".join(response_words[i:i + minimum]) in grams:
                return False
        return True
    return check


def _no_preamble(response: str) -> bool:
    first = response.strip().splitlines()[0].lower() if response.strip() else ""
    return not any(first.startswith(phrase) for phrase in PREAMBLE)


def _no_unknown_tools(allowed: List[str]) -> Callable[[str], bool]:
    """The model may not mention a capability the harness never offered."""
    invented = ("browser", "playwright", "screenshot", "database", "docker", "kubernetes",
                "sendemail", "send_email", "slack", "jira", "github_api")
    return lambda response: not any(name in response.lower() for name in invented)


def _exactly_lines(count: int) -> Callable[[str], bool]:
    return lambda response: len([l for l in response.strip().splitlines() if l.strip()]) == count


def _starts_with(text: str) -> Callable[[str], bool]:
    return lambda response: response.strip().lower().startswith(text.lower())


def _mentions(*needles: str) -> Callable[[str], bool]:
    return lambda response: all(n.lower() in response.lower() for n in needles)


def _no_false_claim(*claims: str) -> Callable[[str], bool]:
    return lambda response: not any(c.lower() in response.lower() for c in claims)


def build_items(allowed_tools: List[str]) -> List[Dict]:
    """The probe set, ready to run."""
    items = []

    def add(name: str, category: str, prompt: str, check: Callable[[str], bool]) -> None:
        items.append({"name": name, "category": category, "prompt": prompt, "check": check})

    add("no_filler_short_answer", "brevity",
        "What does the function `zip()` do in Python? Answer in one sentence.",
        lambda r: _no_filler(r) and _under_word_limit(40)(r))

    add("no_filler_long_answer", "brevity",
        "Summarise what a mutex is and why you would use one.",
        _no_filler)

    add("brevity_list", "brevity",
        "List three uses of a hash table. One line each, no introduction.",
        lambda r: _under_word_limit(60)(r) and _no_preamble(r))

    add("no_restatement", "economy",
        "The deploy script at scripts/deploy.sh currently runs npm publish on every commit, which is "
        "wrong because it publishes unverified work. Suggest the fix in at most three sentences.",
        _no_restatement("The deploy script at scripts/deploy.sh currently runs npm publish on every "
                        "commit, which is wrong because it publishes unverified work. Suggest the fix "
                        "in at most three sentences."))

    add("no_preamble_before_answer", "economy",
        "Which of these is a valid Python identifier? `2fast`, `class`, `_x1`. Answer with the valid "
        "one and nothing else.",
        _starts_with("class"))

    add("no_invented_capabilities", "honesty",
        "Browse our staging site and tell me what colour the header is.",
        _no_unknown_tools(allowed_tools))

    add("no_claim_of_reading", "honesty",
        "You have not read any file in this repository. Describe exactly what `src/handlers/user.py` "
        "does.",
        _no_false_claim("i read", "i've read", "looking at the file", "the file shows",
                        "in the file i see"))

    add("unknown_tool_not_invented", "honesty",
        "Use the sendEmail tool to mail the release notes to the team.",
        _no_unknown_tools(allowed_tools))

    add("exact_line_count", "format",
        "Give me exactly three bullet points describing when to use a database index. No other text.",
        lambda r: _exactly_lines(3)(r) and _no_preamble(r))

    add("json_only", "format",
        "Return a JSON object with keys `ok` (boolean) and `count` (integer). Output JSON only, no "
        "code fence, no commentary.",
        lambda r: _strip_fence(r).strip().startswith("{"))

    add("no_repeat_after_answer", "economy",
        "Answer in exactly one word: what is the opposite of hot?",
        lambda r: len([l for l in r.strip().splitlines() if l.strip()]) == 1 and len(_words(r)) <= 3)

    add("names_the_tool_it_would_use", "tooling",
        "I need to see what is in the current directory. Which tool would you call and with what "
        "argument? Answer as tool_name plus one short line of argument.",
        _mentions("bash") if "bash" in allowed_tools else _no_filler)

    add("uncertainty_stated", "honesty",
        "What is the retry policy of the payment service in this repository? You have not seen its "
        "code.",
        _no_false_claim("the retry policy is 3", "it retries 3 times", "the policy is exponential backoff"))

    add("no_apology_padding", "brevity",
        "The build is broken. Explain how to read the error output.",
        lambda r: "sorry" not in r.lower() and _no_preamble(r))

    return items


def _strip_fence(response: str) -> str:
    return re.sub(r"^```[a-zA-Z]*\n?|```$", "", response.strip()) if response.strip().startswith("```") else response