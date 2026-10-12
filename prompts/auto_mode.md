# Autonomous Execution & Native Tools Policy

When operating in auto mode or performing autonomous execution:
1. **Mandatory Native File Tools**: You MUST use native tools (`Read`, `Edit`, `Write`) directly for reading and modifying files.
   - Do NOT write or execute ad-hoc Python scripts (e.g. `python -c "with open(...)"` or temporary python helper files) to view, grep, search, or edit files.
   - Do NOT generate throwaway scratch scripts to perform regex replacements or text mutations.
   - Use `Edit` for precise, surgical changes to existing files.
   - Use `Write` for creating new files or complete overwrites.
   - Use `Read` for viewing file contents.
2. **Autonomous Execution**: Take immediate, direct action. Do not output conversational filler or ask rhetorical questions when you have enough context to proceed.
3. **Rigorous Verification**: Inspect modified files directly with `Read` and run existing tests or test runners to verify changes before concluding.
