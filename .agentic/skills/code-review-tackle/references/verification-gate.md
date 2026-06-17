# Verification Gate

Per AGENTS.md "Final Gate", every tackled story must pass these commands before reaching `status: verified`. Failure at any step blocks the tackle run.

## Commands (in order)

```bash
poetry run ruff check --fix .
poetry run ruff format .
just lint-strict
just type-check
just test
poetry run -- resolver run dbt parse
```

`just validate` per AGENTS.md runs lint-strict + type-check + test in sequence; the dbt parse step is added separately because it covers the warehouse-side compile. Run `just validate` instead of the three middle commands.

## Failure handling

- If `ruff check --fix` or `ruff format` modifies files: re-stage them and continue. These are autofixers; their changes are part of the tackle work.
- If `just lint-strict`, `just type-check`, `just test`, or `dbt parse` fails: abort the tackle run. Surface the error to the user. Do NOT bypass with `--no-verify` or similar; per AGENTS.md hard rules, lint/type ignores require explicit user approval with reason.

## Why this gate

- AGENTS.md hard rule: "Final Gate: Run `just validate` to verify all steps above in sequence."
- The dbt parse step is mandatory whenever dbt models change (per AGENTS.md dbt conventions).

## Output

The verification gate produces no artifact other than its pass/fail status. Pass → tackle flow proceeds to the correctness subagent pass. Fail → tackle run aborts; story stays `status: in-progress`.
