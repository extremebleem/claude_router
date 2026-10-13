# Agent Error Prevention and Prompt Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the top 4 recurring errors in agent sessions (Edit string mismatches, Windows ENAMETOOLONG bash errors, relative path failures, and redundant re-reads) via targeted system prompt guidelines and model directives.

**Architecture:** 
- Update `CORE_CODING_GUIDELINES` and model directives in `transformer/prompts.py`.
- Update `prompts/auto_mode.md` to forbid giant inline python scripts that cause `ENAMETOOLONG`.
- Update tests in `tests/test_transformer.py` to verify the presence and consistency of new guidelines.

**Tech Stack:** Python 3.10+, unittest.

**Spec:** `docs/superpowers/specs/2026-10-13-agent-error-prevention-design.md`

## Global Constraints
- Minimal, high-density prompt text (avoid bloated instructions that dilute model attention).
- Backward compatibility with all existing 72 tests.

## Review Focus
1. New guidelines must directly address the 4 observed errors from friend logs.
2. Prompts must remain concise and token-efficient.
3. System instruction builder must correctly inject updated directives for Flash, Pro, and Muse models.

---

### Task 1: Update Coding Guidelines and Model Directives in `transformer/prompts.py`

**Files:**
- Modify: `transformer/prompts.py:34-62`
- Test: `tests/test_transformer.py`

- [ ] **Step 1: Write test checking for new prompt directives**
Add test in `tests/test_transformer.py` verifying that `CORE_CODING_GUIDELINES` contains precision editing, Windows bash command limits, and absolute path rules.

- [ ] **Step 2: Update `CORE_CODING_GUIDELINES` and `MODEL_SPECIFIC_TUNING` in `transformer/prompts.py`**
Include:
- Exact tab/space indentation matching for `Edit`.
- Forbidding giant multiline `python -c` scripts that trigger Windows `ENAMETOOLONG`.
- Re-read avoidance rule for unchanged files.

- [ ] **Step 3: Run tests to verify they pass**
Run: `python -m unittest discover -s tests -p "test_transformer.py"`

- [ ] **Step 4: Commit**
```bash
git add transformer/prompts.py tests/test_transformer.py
git commit -m "tune(prompts): add precision editing and Windows command safety directives"
```

---

### Task 2: Update `prompts/auto_mode.md`

**Files:**
- Modify: `prompts/auto_mode.md`
- Test: `tests/test_transformer.py`

- [ ] **Step 1: Update `prompts/auto_mode.md`**
Explicitly forbid passing large code payloads through shell commands (`python -c "..."`) which fail with `ENAMETOOLONG`. Direct model to use native `Write` instead.

- [ ] **Step 2: Run full test suite**
Run: `python -m unittest discover -s tests`

- [ ] **Step 3: Commit**
```bash
git add prompts/auto_mode.md
git commit -m "tune(prompts): forbid shell multiline scripts causing ENAMETOOLONG in auto mode"
```
