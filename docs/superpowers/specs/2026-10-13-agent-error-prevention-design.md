# Agent Error Prevention and Prompt Tuning Specification

## 1. Problem Analysis from Real User Sessions
Analysis of 3,367 logged turns from real user sessions (`slopium` and others) identified 4 distinct classes of recurring agent errors:

1. **`Edit` Tool "String to replace not found" (19 occurrences)**:
   - **Root Cause**: The model guesses whitespace, indentation (tabs vs spaces), or tries to edit code without verifying exact formatting with `Read`. When an edit fails, it repeats the exact same failing edit multiple times, burning tokens in retry loops.
2. **`Bash` Command Failures & Windows Syntax Errors (16 occurrences)**:
   - **Root Cause**: The model issues POSIX-only commands (e.g. `python -c "..."` with huge multiline heredocs that fail Windows argument length limits with `ENAMETOOLONG: name too long, uv_spawn`, or Unix shellisms that fail in cmd/PowerShell environments).
3. **"File does not exist" on relative paths (6 occurrences)**:
   - **Root Cause**: The model assumes current working directory or relative path without verifying with `Glob` or using absolute paths.
4. **Endless Re-Reading of Unchanged Files (614 occurrences)**:
   - **Root Cause**: The model reads `PaintTraverse.cpp` 287 times because it forgets it already read it or lacks a reminder that files in memory are unchanged unless modified.

## 2. Requirements & Changes

### 2.1 Update `CORE_CODING_GUIDELINES` in `transformer/prompts.py`
Add explicit directives targeted at the top failure modes:
- **Precision in `Edit`**: Always match indentation (spaces vs tabs) exactly from `Read`. Keep `old_string` focused (3-10 lines) and include unique anchor context.
- **Windows Command Safety**: Warn against giant inline multiline python scripts via Bash (`ENAMETOOLONG`). For large file generations, use native `Write` tool rather than `python -c "..."`.
- **Absolute Paths**: Reinforce mandatory absolute paths (`C:\...`) for all filesystem tools.
- **Deduplication Awareness**: Do not re-read files unless modified in the current session.

### 2.2 Update `auto_mode.md` in `prompts/auto_mode.md`
- Forbid `python -c "..."` multiline code generation to prevent Windows command line length overflows (`uv_spawn ENAMETOOLONG`).

### 2.3 Update Model Directives in `MODEL_SPECIFIC_TUNING`
- Refine `gemini-flash` and `gemini-pro` directives to prevent hallucinated edits and repetitive loops.

## 3. Success Metrics
- 100% passing test suite across all 72+ unit tests.
- Clear, unambiguous guidance injected to prevent the 4 observed error categories.
