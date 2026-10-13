"""
Runs prompt variants against OpenCode Zen and scores them.

    python tests/eval/run_eval.py --suite ifeval --sample 120 --variants production,terse
    python tests/eval/run_eval.py --suite agentic

Every request goes through the proxy's own translation (anthropic_to_opencode_payload), with the
second system block swapped for the variant under test, so the free-tier gate, the system-block
layout and the tool surface are exactly what production sends - only the wording changes.

Needs no credential: the anonymous free tier is what the proxy uses when no account is configured.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# This directory's modules are imported flat: `tests` is a package elsewhere in site-packages and
# would shadow the repository's own tests directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from auth.opencode_catalog import static_snapshot  # noqa: E402
from backend.opencode import (  # noqa: E402
    OpenCodeClient,
    anthropic_to_opencode_payload,
    fold_sse_to_message,
)
from transformer.opencode_prompts import render_claude_cli_prompt  # noqa: E402

import agentic_probe  # noqa: E402
import ifeval_scorer  # noqa: E402
import variants as variants_module  # noqa: E402

MODEL = "space-bunny-free"
RESULTS_DIR = Path(__file__).parent / "results"
PAUSE_SECONDS = 1.5  # the free tier is a shared resource; do not hammer it


class Runner:
    """One anonymous Zen client, reused for every request of a run."""

    def __init__(self, model: str = MODEL):
        class _Empty:
            def get_available_accounts(self, *a, **kw):
                return []

            def cooldown_remaining(self, *a, **kw):
                return 0.0

        self.client = OpenCodeClient(_Empty())
        self.client._catalog = static_snapshot()
        self.model = model
        self.done = 0

    async def ask(self, prompt: str, variant: str, max_tokens: int = 600) -> str:
        request = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "tools": variants_module.TOOLS,
            "max_tokens": max_tokens,
        }
        payload = anthropic_to_opencode_payload(request, free_tier_markers_on_wire=True)
        rendered = render_claude_cli_prompt(variant, variants_module.TOOL_NAMES)
        blocks = [m for m in payload["messages"] if m["role"] == "system"]
        blocks[1]["content"] = rendered
        self.done += 1
        status, body = await asyncio.wait_for(
            fold_sse_to_message(self.client.generate_stream(request)), timeout=120)
        await asyncio.sleep(PAUSE_SECONDS)
        if status != 200:
            raise RuntimeError(f"{status}: {json.dumps(body.get('error') or body)[:200]}")
        return "".join(block.get("text", "") for block in body.get("content", [])
                       if block.get("type") == "text")


async def run_ifeval(runner: Runner, variant_name: str, variant: str, sample: int) -> Dict[str, Any]:
    rows = ifeval_scorer.load_prompts()
    if sample and sample < len(rows):
        # One prompt per instruction type before the rest, so a small sample still covers the board.
        seen: Dict[str, int] = {}
        picked, rest = [], []
        for row in rows:
            key = row["instruction_id_list"][0]
            if seen.get(key, 0) < 3:
                seen[key] = seen.get(key, 0) + 1
                picked.append(row)
            else:
                rest.append(row)
        rows = picked + rest[: max(0, sample - len(picked))]
    results = []
    for row in rows:
        try:
            response = await runner.ask(row["prompt"], variant)
        except Exception as e:
            print(f"  ! {e}", flush=True)
            continue
        verdicts, ids = ifeval_scorer.score(row, response)
        results.append({"key": row.get("key"), "prompt": row["prompt"], "response": response,
                        "ids": ids, "verdicts": verdicts, "followed": all(verdicts)})
        print(f"  [{runner.done}] {'ok ' if all(verdicts) else 'MISS'} "
              f"{','.join(i for i, v in zip(ids, verdicts) if not v) or '-'}", flush=True)
    summary = ifeval_scorer.summarise(results)
    summary["variant"] = variant_name
    return {"summary": summary, "results": results}


async def run_agentic(runner: Runner, variant_name: str, variant: str) -> Dict[str, Any]:
    items = agentic_probe.build_items(variants_module.TOOL_NAMES)
    results = []
    for item in items:
        try:
            response = await runner.ask(item["prompt"], variant)
            ok = bool(item["check"](response))
        except Exception as e:
            print(f"  ! {item['name']}: {e}", flush=True)
            continue
        results.append({"name": item["name"], "category": item["category"],
                        "response": response, "passed": ok})
        print(f"  [{runner.done}] {'ok  ' if ok else 'MISS'} {item['name']}", flush=True)
    summary = {"variant": variant_name, "probes": len(results),
               "passed": sum(1 for r in results if r["passed"]),
               "by_category": {c: [1 if r["passed"] else 0 for r in results if r["category"] == c]
                               for c in sorted({r["category"] for r in results})}}
    summary["score"] = summary["passed"] / len(results) if results else 0.0
    return {"summary": summary, "results": results}


def report(runs: List[Dict[str, Any]]) -> None:
    print("\n" + "=" * 78)
    for run in runs:
        summary = run["summary"]
        print(f"{summary['variant']:<16}", end="")
        if "prompt_level" in summary:
            print(f"prompt-level {summary['prompt_level']:.1%}  "
                  f"instruction-level {summary['instruction_level']:.1%}  "
                  f"(n={summary['prompts']})")
        else:
            print(f"probes {summary['passed']}/{summary['probes']} = {summary['score']:.0%}")
    base = runs[0]["summary"]
    if len(runs) > 1 and "prompt_level" in base:
        print(f"\ndeltas vs {base['variant']}:")
        for run in runs[1:]:
            print(f"  {run['summary']['variant']:<16} "
                  f"prompt {(run['summary']['prompt_level'] - base['prompt_level']):+.1%}  "
                  f"instruction {(run['summary']['instruction_level'] - base['instruction_level']):+.1%}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("ifeval", "agentic", "both"), default="agentic")
    parser.add_argument("--variants", default="production,terse")
    parser.add_argument("--sample", type=int, default=0, help="IFEval prompts to run (0 = all)")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()

    available = variants_module.variants()
    names = [n.strip() for n in args.variants.split(",") if n.strip()]
    unknown = [n for n in names if n not in available]
    if unknown:
        raise SystemExit(f"unknown variant(s): {', '.join(unknown)}. have: {', '.join(available)}")

    runner = Runner(args.model)
    suites = (["ifeval", "agentic"] if args.suite == "both" else [args.suite])
    runs = []
    for name in names:
        print(f"\n=== {name} ===", flush=True)
        for suite in suites:
            if suite == "ifeval":
                runs.append(await run_ifeval(runner, name, available[name], args.sample))
            else:
                runs.append(await run_agentic(runner, name, available[name]))
    report(runs)

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"eval_{stamp}.json"
    path.write_text(json.dumps(runs, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    asyncio.run(main())