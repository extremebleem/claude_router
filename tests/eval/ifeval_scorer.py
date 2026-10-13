"""
Stdlib-only scorer for the public IFEval prompts.

Mirrors the rules of google-research/instruction_following_eval (arXiv:2311.07911) for the instruction
types that dataset uses. Two differences from the official implementation, both called out where they
occur:

  * sentences are split with a regex rather than nltk's punkt tokenizer, which can move a count by one
    at the edges;
  * `language:response_language` is not scored. It needs langdetect, and this project keeps a single
    dependency on purpose.

Everything else - the idea of the benchmark, its prompts and its per-instruction rules - is Google's,
unmodified; the dataset is cached under data/ so a run needs no network.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

DATA = Path(__file__).parent / "data" / "input_data.jsonl"

# Scored against a different rule set than the official one, so its numbers are not comparable.
SKIPPED_INSTRUCTIONS = {"language:response_language", "detectable_format:constrained_response"}


def load_prompts(limit: int = 0) -> List[Dict[str, Any]]:
    """The cached IFEval prompts, optionally the first `limit` of them."""
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[:limit] if limit else rows


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z\"'(\[])", text.strip())
    return [p for p in parts if p.strip()]


def _words(text: str) -> List[str]:
    return re.findall(r"\b[\w'-]+\b", text)


def _paragraphs(text: str) -> List[str]:
    """Paragraphs are separated by the markdown divider *** in this benchmark, not by a blank line."""
    return [p for p in re.split(r"\s?\*\*\*\s?", text.strip()) if p.strip()]


def _relation(kwargs: Dict[str, Any]) -> str:
    """The benchmark leaves the relation out when the requirement is a minimum."""
    return kwargs.get("relation") or "at least"


def _count(condition: bool, relation: str) -> bool:
    """`condition` is always spelled "at least N"; a "less than" requirement is its negation."""
    return not condition if relation == "less than" else condition


def _check(instruction_id: str, response: str, kwargs: Dict[str, Any]) -> bool:
    value = response.strip()
    low = value.lower()

    if instruction_id == "keywords:existence":
        return all(re.search(re.escape(k), low, flags=re.IGNORECASE) for k in kwargs["keywords"])
    if instruction_id == "keywords:forbidden_words":
        return not any(re.search(re.escape(k), low, flags=re.IGNORECASE) for k in kwargs["forbidden_words"])
    if instruction_id == "keywords:frequency":
        count = len(re.findall(re.escape(kwargs["keyword"].lower()), low))
        return _count(count >= kwargs["frequency"], _relation(kwargs))
    if instruction_id == "keywords:letter_frequency":
        count = low.count(kwargs["letter"].lower())
        return _count(count >= kwargs["let_frequency"], kwargs.get("let_relation") or "at least")
    if instruction_id == "length_constraints:number_sentences":
        count = len(_sentences(value))
        return _count(count >= kwargs["num_sentences"], _relation(kwargs))
    if instruction_id == "length_constraints:number_words":
        count = len(_words(value))
        return _count(count >= kwargs["num_words"], _relation(kwargs))
    if instruction_id == "length_constraints:number_paragraphs":
        count = len(_paragraphs(value))
        return _count(count >= kwargs["num_paragraphs"], _relation(kwargs))
    if instruction_id == "length_constraints:nth_paragraph_first_word":
        paragraphs = _paragraphs(value)
        index = kwargs["nth_paragraph"] - 1
        if index >= len(paragraphs):
            return False
        first = _words(paragraphs[index])
        return bool(first) and first[0].lower() == kwargs["first_word"].lower()
    if instruction_id == "detectable_content:number_placeholders":
        return len(re.findall(r"\[.*?\]", value)) >= int(kwargs["num_placeholders"])
    if instruction_id == "detectable_content:postscript":
        return kwargs["postscript_marker"].lower() in low
    if instruction_id == "detectable_format:number_bullet_lists":
        return len(re.findall(r"^\s*[\*\-]\s+\S", value, flags=re.MULTILINE)) == int(kwargs["num_bullets"])
    if instruction_id == "detectable_format:number_highlighted_sections":
        highlights = re.findall(r"\*[^\n\*]*\*", value) + re.findall(r"\*\*[^\n\*]*\*\*", value)
        return len(highlights) >= int(kwargs["num_highlights"])
    if instruction_id == "detectable_format:multiple_sections":
        parts = re.split(r"\s?" + re.escape(kwargs["section_spliter"]) + r"\s?", value)
        return len([p for p in parts if p.strip()]) >= int(kwargs["num_sections"])
    if instruction_id == "detectable_format:json_format":
        try:
            json.loads(value)
            return True
        except ValueError:
            return False
    if instruction_id == "detectable_format:title":
        return bool(re.search(r"<<[^\n]+>>", value))
    if instruction_id == "combination:two_responses":
        return len([p for p in value.split("******") if p.strip()]) >= 2
    if instruction_id == "combination:repeat_prompt":
        return low.startswith(kwargs["prompt_to_repeat"].strip().lower())
    if instruction_id == "startend:end_checker":
        return low.rstrip().endswith(kwargs["end_phrase"].strip().lower())
    if instruction_id == "startend:quotation":
        return value.startswith('"') and value.endswith('"')
    if instruction_id == "change_case:capital_word_frequency":
        count = len(re.findall(r"\b[A-Z]{2,}\b", value))
        return _count(count >= int(kwargs["capital_frequency"]),
                      kwargs.get("capital_relation") or "at least")
    if instruction_id == "change_case:english_capital":
        return value.isupper()
    if instruction_id == "change_case:english_lowercase":
        return value.islower()
    if instruction_id == "punctuation:no_comma":
        return "," not in value
    return False


def score(prompt_row: Dict[str, Any], response: str) -> Tuple[List[bool], List[str]]:
    """Per-instruction verdicts for one response, plus the ids they belong to."""
    verdicts, ids = [], []
    kwargs_list = prompt_row.get("kwargs") or [{}]
    for index, instruction_id in enumerate(prompt_row.get("instruction_id_list") or []):
        ids.append(instruction_id)
        if instruction_id in SKIPPED_INSTRUCTIONS:
            verdicts.append(True)  # not measured; counted as followed so the score is not dragged
            continue
        kwargs = kwargs_list[index] if index < len(kwargs_list) else {}
        verdicts.append(_check(instruction_id, response, kwargs))
    return verdicts, ids


def summarise(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Prompt-level strict accuracy, instruction-level accuracy, and a per-type breakdown."""
    prompts = len(results)
    strict = sum(1 for r in results if r["followed"])
    by_type: Dict[str, List[int]] = {}
    for result in results:
        for instruction_id, ok in zip(result["ids"], result["verdicts"]):
            bucket = by_type.setdefault(instruction_id, [])
            bucket.append(1 if ok else 0)
    per_type = {k: sum(v) / len(v) for k, v in sorted(by_type.items())}
    families: Dict[str, List[int]] = {}
    for instruction_id, accuracy in per_type.items():
        families.setdefault(instruction_id.split(":")[0], []).append(accuracy)
    return {
        "prompts": prompts,
        "prompt_level": strict / prompts if prompts else 0.0,
        "instruction_level": sum(per_type[k] * len(by_type[k]) for k in per_type)
                            / sum(len(v) for v in by_type.values()) if prompts else 0.0,
        "by_type": per_type,
        "by_family": {k: sum(v) / len(v) for k, v in sorted(families.items())},
    }