## 1. Development Workflow
AI agents MUST follow this sequence for all changes.

1. **Implement**: Write code/models following project conventions.
2. **Verify**: Use `Justfile` commands for quality gates:
   - `just lint-strict`: Auto-format, fix, check Python for errors.
   - `just type-check`: Run static analysis.
   - `just test`: Run Python unit tests.
3. **Final Gate**: Run `just validate` to verify all steps above in sequence.

## 2. Hard Rules
- Project is greenfield, it should never have 'legacy' code or 'legacy' fallbacks, replace/refactor old paths over adding compatibility fallbacks.
- `tests/` is a structural mirror of `src/`.
- Do not add unnecessary and/or coupled comments and docstrings that make changes hard to maintain and generate staleness. Usually module level docstrings should be 1 line. For function and method docstrings use google style as per ruff config in `pyproject.toml`. Be concise.
- Avoid config knobs that don't actually control anything, unless there is reasonable expectation that a new behavior is going to be added soon (YAGNI); prefer a concise comment instead. Especially avoid knobs with misleading or unexpected behavior, such as those that don't actually enforce anything. Silent/unexpected behavior is worse than no control at all. If a knob has multiple options that aren't clear from context, you must add a concise comment explaining the available options.
- Use Conventional Commits convention for commit messages, with an optional, but highly recommended, scope in parenthesis. Keep it concise.
- Avoid type ignore on untyped import. Add a minimal type stub whenever feasible.
- Avoid linter and type ignores in general without a very good reason that should be explicitly explained to the user.
- Tests shouldn't use repo configuration files or depend on hardcoded project paths, as that makes for brittle tests. Use fixtures (such as conftest modules) and parameterization.
- Avoid Any and don't let Mapping[str, Any] become a documentation-via-runtime-error contract.
- Prefer structural matching with `match` over isinstance calls.
- Use ruff to autofix issues that it can autofix, such as import sorting, trailing newlines, spaces, etc. Do NOT reverse engineer ruff or reimplement it.
- If a file or directory is gitignored, ask the user before committing. There is usually a good reason for it to be ignored, such as that it may be part of a developer specific workflow.
- Comments should be timeless (to exent as that is possible), objective, and concise. Avoid stale-prone comments that reference impl details. Do NOT add unnecessary comments related to impl phases or anything like that.
- Keep commit messages and descriptions very short and concise. Use bulletpoints if you need to, and, as with comments, prioritize timelessness, objectivity, and conciseness.
- Use uv to add and remove dependencies. Unless ABSOLUTELY necessary, do NOT manage pyproject.toml manually. Pin dependencies with `~=` to ensure targetting of the same major version.
- Always prefer chaining exceptions to keep the full chain of events leading to us raising our custom exceptions.
- Prefer strict domain separation, avoid string-based dispatch, and use typing in your favor to avoid seas of complex branching that are brittle and hard to maintain and extend.
- Make illegal states unrepresentable.
