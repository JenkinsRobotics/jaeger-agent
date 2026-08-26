"""Shared fixtures.

The tool registry is process-global, and several suites clear it on
purpose to install their own fixtures (test_liveness, test_length_retry,
test_openai_adapter, test_hermes_xml_adapter). Any test that asserts on
the REAL surface therefore has to establish it rather than assume it —
otherwise it passes or fails depending on collection order, which is the
worst kind of test.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def _register_tool_surface() -> None:
    """Re-run the tool registrations.

    Importing ``jaeger_agent.tools`` again is not enough: the
    ``@register_tool_from_function`` decorators already executed, so a
    cached module re-imports to nothing. Dropping the submodules from
    ``sys.modules`` first makes them run again.
    """
    from jaeger_os.core.tools.tool_registry import clear_registry, get_tools

    if get_tools():
        return
    clear_registry()
    for name in [m for m in list(sys.modules) if m.startswith("jaeger_agent.tools")]:
        del sys.modules[name]
    importlib.import_module("jaeger_agent.tools")


@pytest.fixture()
def live_tools():
    """The real, fully registered tool surface.

    Request this in any test that looks tools up by name. Not autouse:
    suites that deliberately run against an empty or hand-built registry
    must keep getting exactly that.
    """
    _register_tool_surface()
    from jaeger_os.core.tools.tool_registry import get_tools

    return {t.name: t for t in get_tools()}
