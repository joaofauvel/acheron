# Themes Taxonomy

Authoritative list of the theme prefixes used by the code-review skills, organized into 6 bundles. Each story is tagged with exactly one prefix; bundle assignment is an execution detail (see bundle prompts).

## Bundle A — Correctness

| Prefix | Theme | Threshold | In scope |
|---|---|---|---|
| `CORR` | General correctness | low | Logic bugs, off-by-one, incorrect branching, wrong default behavior |
| `ML` | ML correctness | low | Train/test leakage, target leakage, incorrect metric, biased sampling, OOF integrity |
| `MATH` | Numerical correctness | low | Numerical stability, precision loss, divide-by-zero, NaN propagation, incorrect aggregations |

## Bundle B — Architecture

| Prefix | Theme | Threshold | In scope |
|---|---|---|---|
| `ARCH` | Architecture | medium | Boundary violations, dependency direction, port placement, hexagonal layering, package cohesion |
| `CFG` | Configuration | medium | YAML-vs-core dataclass split per AGENTS.md, env var sprawl, knob-vs-comment per project hard rules |

## Bundle C — Code quality

| Prefix | Theme | Threshold | In scope |
|---|---|---|---|
| `MAINT` | Maintainability | high | File size, function length, naming, dead code, brittleness, fragility hotspots |
| `EXC` | Exception discipline | medium | Bare `except`, swallowed errors, exceptions used as control flow, missing exception types |
| `TYPE` | Type safety | medium | `Any` usage, missing annotations, `# type: ignore` audit, Protocol vs ABC drift |

## Bundle D — Verification

| Prefix | Theme | Threshold | In scope |
|---|---|---|---|
| `TEST` | Test coverage and quality | medium | Unit/integration coverage, fakes vs mocks, fixture hygiene, AGENTS.md test independence rules |
| `REPRO` | Reproducibility | medium | Seed handling, deterministic ordering, frozen training-window invariants, version pinning |
| `DATA` | Data quality | medium | dbt source freshness, schema tests, contract tests, null/dupe handling |

## Bundle E — Operations

| Prefix | Theme | Threshold | In scope |
|---|---|---|---|
| `PERF` | Performance | medium | Hot paths, complexity, BigQuery cost, memory, vectorization opportunities |
| `OBS` | Observability | medium | Logging consistency, structured logs, metrics, error reporting, training-run tracking |
| `SEC` | Security | low | Anchored to `.agentic-rules/python/python-security-patterns-rules_v1.md` — input validation, secrets, dependency hygiene, SQL/template injection in dbt macros |

## Bundle F — Surface

| Prefix | Theme | Threshold | In scope |
|---|---|---|---|
| `DX` | Developer experience | high | `just` commands, error messages, build feedback, local setup |
| `PKG` | Packaging | high | `pyproject.toml`, dependency hygiene, version pins, build scripts |
| `DOC` | Documentation | high | Docstrings (per AGENTS.md hard rules: 1-line module docstrings, Google style for functions), READMEs, comment discipline |

## Threshold meanings

- **low**: catch every issue, even minor. Use for SEC, CORR, ML, MATH.
- **medium**: catch significant issues + recurring small issues bundled into patterns. Skip stylistic preferences.
- **high**: surface only patterns or significant gaps. Avoid nit-flooding (ruff/mypy/basedpyright already cover the small stuff).

## Bundling small issues

When N>1 small issues share a remediation, file ONE story listing all sites. Eight `except Exception:` blocks → one `EXC` story enumerating all eight files, not eight stories.
