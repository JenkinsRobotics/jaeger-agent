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

from jaeger_agent import selfcheck


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
