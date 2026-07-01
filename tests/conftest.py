"""Shared fixtures — keep cross-cutting features off by default in tests
unless a test opts in, so they don't affect unrelated test files.
"""

from __future__ import annotations

import pytest

from monitor.config import settings


@pytest.fixture(autouse=True)
def _disable_rate_limit():
    """Rate limiting is on by default in the app; see test_rate_limit.py."""
    saved = settings.rate_limit_enabled
    settings.rate_limit_enabled = False
    yield
    settings.rate_limit_enabled = saved
