"""CLI entry point for the Phase 3a runtime simulation.

Usage: ``uv run python -m sim.run <scenario>`` or ``uv run python -m sim.run --all``.

Each scenario is a module in ``sim/scenarios/`` with a ``main()`` function.
The module docstring MUST contain ``STORY_REF: <story-id>`` for the
validator's discovery rule (see acheron.ux_review.discovery.artifact_path_for).
"""

from __future__ import annotations

import argparse
import importlib
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import cast


def discover_scenarios() -> list[str]:
    """Return the sorted list of scenario names from sim/scenarios/*.py."""
    scenarios_dir = Path(__file__).parent / "scenarios"
    return sorted(p.stem for p in scenarios_dir.glob("*.py") if p.stem != "__init__")


def run_scenario(name: str) -> int:
    """Run a single scenario by name. Returns the scenario's exit code."""
    mod = importlib.import_module(f"sim.scenarios.{name}")
    return cast("Callable[[], int]", mod.main)()


def main() -> int:
    """CLI entry point: run one scenario or --all."""
    parser = argparse.ArgumentParser(description="Run Phase 3a scenarios")
    parser.add_argument("scenario", nargs="?", help="Scenario name to run")
    parser.add_argument("--all", action="store_true", help="Run all scenarios")
    args = parser.parse_args()

    if not args.scenario and not args.all:
        parser.error("specify a scenario name or --all")

    scenarios = discover_scenarios() if args.all else [args.scenario]
    failures: list[tuple[str, str]] = []
    for name in scenarios:
        print(f"\n=== Running scenario: {name} ===", flush=True)
        try:
            rc = run_scenario(name)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
        except Exception as exc:  # noqa: BLE001 - harness boundary
            rc = 1
            failures.append((name, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"))
        else:
            if rc != 0:
                failures.append((name, f"exit={rc}"))
        if rc == 0:
            print(f"  {name}: OK", flush=True)

    if failures:
        print(f"\n{len(failures)} scenario(s) failed:", flush=True)
        for name, reason in failures:
            print(f"  - {name}: {reason}", flush=True)
        return 1
    print(f"\nAll {len(scenarios)} scenario(s) passed.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
