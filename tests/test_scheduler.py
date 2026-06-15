"""Tests for scheduler setup and job execution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monitor.checks.base import CheckOutcome, CheckState
from monitor.scheduler import _build_check, _run_service_job, setup_scheduler
from monitor.service_config import ServiceConfig, load_services

# ---------------------------------------------------------------------------
# service_config
# ---------------------------------------------------------------------------


def test_load_services_parses_yaml(tmp_path):
    yaml_content = """
services:
  - name: web
    type: http
    target: https://example.com
    interval: 60
  - name: db
    type: tcp
    target: localhost
    port: 5432
    interval: 30
"""
    f = tmp_path / "services.yaml"
    f.write_text(yaml_content)

    services = load_services(f)

    assert len(services) == 2
    assert services[0].name == "web"
    assert services[0].type == "http"
    assert services[0].interval == 60
    assert services[1].port == 5432


def test_load_services_applies_threshold_defaults(tmp_path):
    yaml_content = """
services:
  - name: sys
    type: system
    target: /
    interval: 120
"""
    f = tmp_path / "services.yaml"
    f.write_text(yaml_content)

    services = load_services(f)

    assert services[0].thresholds.cpu == 90.0
    assert services[0].thresholds.memory == 90.0


def test_load_services_raises_when_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_services(tmp_path / "missing.yaml")


# ---------------------------------------------------------------------------
# setup_scheduler
# ---------------------------------------------------------------------------


def test_setup_scheduler_registers_one_job_per_service():
    services = [
        ServiceConfig(name="svc1", type="http", target="http://a.com", interval=30),
        ServiceConfig(name="svc2", type="tcp", target="localhost", port=5432, interval=60),
        ServiceConfig(name="svc3", type="system", target="/", interval=120),
    ]

    scheduler = setup_scheduler(services, MagicMock())

    assert len(scheduler.get_jobs()) == 3
    job_ids = {j.id for j in scheduler.get_jobs()}
    assert job_ids == {"check_svc1", "check_svc2", "check_svc3"}


def test_setup_scheduler_job_intervals():
    services = [
        ServiceConfig(name="fast", type="http", target="http://x.com", interval=15),
        ServiceConfig(name="slow", type="http", target="http://y.com", interval=300),
    ]

    scheduler = setup_scheduler(services, MagicMock())

    jobs = {j.id: j for j in scheduler.get_jobs()}
    assert jobs["check_fast"].trigger.interval.total_seconds() == 15
    assert jobs["check_slow"].trigger.interval.total_seconds() == 300


def test_setup_scheduler_empty_services():
    scheduler = setup_scheduler([], MagicMock())
    assert scheduler.get_jobs() == []


# ---------------------------------------------------------------------------
# _build_check
# ---------------------------------------------------------------------------


def test_build_check_http():
    svc = ServiceConfig(name="web", type="http", target="https://example.com", interval=60)
    from monitor.checks import HttpCheck

    check = _build_check(svc)
    assert isinstance(check, HttpCheck)
    assert check.target == "https://example.com"


def test_build_check_tcp():
    svc = ServiceConfig(
        name="db", type="tcp", target="localhost", port=5432, interval=30
    )
    from monitor.checks import TcpCheck

    check = _build_check(svc)
    assert isinstance(check, TcpCheck)
    assert check.port == 5432


def test_build_check_system():
    svc = ServiceConfig(name="sys", type="system", target="/", interval=60)
    from monitor.checks import SystemCheck

    check = _build_check(svc)
    assert isinstance(check, SystemCheck)


# ---------------------------------------------------------------------------
# _run_service_job
# ---------------------------------------------------------------------------


async def test_run_service_job_persists_result():
    svc = ServiceConfig(name="web", type="http", target="https://example.com", interval=60)

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)

    outcome = CheckOutcome(
        name="web",
        check_type="http",
        target="https://example.com",
        state=CheckState.UP,
        latency_ms=42.0,
    )

    with patch("monitor.scheduler._build_check") as mock_build:
        mock_check = AsyncMock()
        mock_check.run.return_value = outcome
        mock_build.return_value = mock_check

        await _run_service_job(svc, mock_factory, notifier=None)

    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()
    added = mock_session.add.call_args[0][0]
    assert added.name == "web"
    assert added.status == "up"
    assert added.latency_ms == 42.0


async def test_run_service_job_calls_notifier_when_set():
    svc = ServiceConfig(name="web", type="http", target="https://example.com", interval=60)

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    outcome = CheckOutcome(
        name="web",
        check_type="http",
        target="https://example.com",
        state=CheckState.DOWN,
        latency_ms=None,
    )
    mock_notifier = AsyncMock()

    with patch("monitor.scheduler._build_check") as mock_build:
        mock_check = AsyncMock()
        mock_check.run.return_value = outcome
        mock_build.return_value = mock_check

        await _run_service_job(svc, MagicMock(return_value=mock_session), notifier=mock_notifier)

    mock_notifier.notify.assert_awaited_once_with(outcome)
