"""The self-check runs, and passes, on this install.

Two different jobs here. The first test is the sanity gate: if the tool
surface, toolsets, skills, dialects, adapters or prompt registry are
broken, CI fails with the specific check that broke rather than a
thousand cryptic downstream errors.

The rest verify the checker ITSELF behaves — a health check that cannot
fail is worse than no health check, because it reports green while the
thing it watches burns.
"""

from __future__ import annotations

import importlib
import sys

import pytest

from jaeger_agent import selfcheck


@pytest.fixture(autouse=True)
def _live_tool_surface():
    """Re-register the tool surface before each check.

    The registry is process-global and several sibling suites clear it on
    purpose to install their own fixtures (test_liveness, test_length_retry,
    test_openai_adapter, ...). Whether this file runs before or after them
    decided whether it saw 96 tools or zero — the self-check was correct
    both times, and the TEST was the flaky thing.

    Re-importing is not enough: the registration decorators already ran, so
    a cached module re-imports to nothing. Dropping the submodules from
    sys.modules first makes them execute again.
    """
    from jaeger_os.core.tools.tool_registry import clear_registry, get_tools

    if not get_tools():
        clear_registry()
        for name in [m for m in list(sys.modules) if m.startswith("jaeger_agent.tools")]:
            del sys.modules[name]
        importlib.import_module("jaeger_agent.tools")
    yield


def test_selfcheck_passes() -> None:
    report = selfcheck.run()
    assert report.ok, "self-check failed:\n" + "\n".join(
        f"  {c.name}: {c.detail}" for c in report.failures
    )


def test_selfcheck_covers_the_whole_surface() -> None:
    """Guard against a check silently disappearing from CHECKS."""
    report = selfcheck.run()
    assert len(report.checks) == len(selfcheck.CHECKS)
    assert len(selfcheck.CHECKS) >= 15


def test_a_broken_check_is_reported_not_raised() -> None:
    """A check that raises must become a FAILED check. If it propagated,
    one bad probe would take down the whole report and hide the others."""

    def _boom() -> selfcheck.Check:
        raise RuntimeError("deliberate")

    original = selfcheck.CHECKS
    selfcheck.CHECKS = (*original, _boom)
    try:
        report = selfcheck.run()
        assert not report.ok
        assert any("deliberate" in c.detail for c in report.failures)
    finally:
        selfcheck.CHECKS = original


def test_exit_code_follows_health() -> None:
    """``python3 -m jaeger_agent.selfcheck`` is meant for CI, so the exit
    code has to mean something."""
    assert selfcheck.main([]) == 0


def test_json_output_is_machine_readable(capsys) -> None:
    import json

    selfcheck.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["checks"] and all("name" in c for c in payload["checks"])
