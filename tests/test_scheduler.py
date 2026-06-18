"""Tests for scheduler setup and job execution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monitor.checks.base import CheckOutcome, CheckState
from monitor.scheduler import (
    _build_check,
    _run_service_job,
    reconcile_jobs,
    setup_scheduler,
)
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


def test_load_services_rejects_duplicate_names(tmp_path):
    yaml_content = """
services:
  - name: web
    type: http
    target: https://a.com
  - name: web
    type: http
    target: https://b.com
"""
    f = tmp_path / "services.yaml"
    f.write_text(yaml_content)

    with pytest.raises(ValueError, match="duplicate service names: web"):
        load_services(f)


def test_load_services_rejects_tcp_without_port(tmp_path):
    yaml_content = """
services:
  - name: db
    type: tcp
    target: localhost
"""
    f = tmp_path / "services.yaml"
    f.write_text(yaml_content)

    with pytest.raises(ValueError, match="tcp checks require a port"):
        load_services(f)


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


def test_setup_scheduler_adds_purge_job_when_retention_set():
    scheduler = setup_scheduler([], MagicMock(), retention_days=30)
    job_ids = {j.id for j in scheduler.get_jobs()}
    assert "purge_old_results" in job_ids


def test_setup_scheduler_no_purge_job_when_retention_zero():
    scheduler = setup_scheduler([], MagicMock(), retention_days=0)
    assert scheduler.get_jobs() == []


# ---------------------------------------------------------------------------
# reconcile_jobs
# ---------------------------------------------------------------------------


def test_reconcile_adds_new_service():
    scheduler = setup_scheduler([], MagicMock())
    services = [ServiceConfig(name="web", type="http", target="http://a.com", interval=30)]

    diff = reconcile_jobs(scheduler, services, MagicMock())

    assert diff == {"added": ["web"], "removed": [], "updated": []}
    assert {j.id for j in scheduler.get_jobs()} == {"check_web"}


def test_reconcile_removes_dropped_service():
    services = [ServiceConfig(name="web", type="http", target="http://a.com", interval=30)]
    scheduler = setup_scheduler(services, MagicMock())

    diff = reconcile_jobs(scheduler, [], MagicMock())

    assert diff == {"added": [], "removed": ["web"], "updated": []}
    assert scheduler.get_jobs() == []


def test_reconcile_updates_changed_interval():
    services = [ServiceConfig(name="web", type="http", target="http://a.com", interval=30)]
    scheduler = setup_scheduler(services, MagicMock())

    changed = [ServiceConfig(name="web", type="http", target="http://a.com", interval=99)]
    diff = reconcile_jobs(scheduler, changed, MagicMock())

    assert diff == {"added": [], "removed": [], "updated": ["web"]}
    job = scheduler.get_job("check_web")
    assert job.trigger.interval.total_seconds() == 99


def test_reconcile_leaves_unchanged_service_untouched():
    services = [ServiceConfig(name="web", type="http", target="http://a.com", interval=30)]
    scheduler = setup_scheduler(services, MagicMock())

    diff = reconcile_jobs(scheduler, list(services), MagicMock())

    assert diff == {"added": [], "removed": [], "updated": []}


def test_reconcile_preserves_purge_job():
    scheduler = setup_scheduler([], MagicMock(), retention_days=30)
    services = [ServiceConfig(name="web", type="http", target="http://a.com", interval=30)]

    reconcile_jobs(scheduler, services, MagicMock())

    job_ids = {j.id for j in scheduler.get_jobs()}
    assert "purge_old_results" in job_ids
    assert "check_web" in job_ids


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


async def test_run_service_job_survives_persistence_failure():
    """A DB write failure must not suppress metrics or alerting."""
    svc = ServiceConfig(name="web", type="http", target="https://example.com", interval=60)

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.commit.side_effect = RuntimeError("db down")

    outcome = CheckOutcome(
        name="web",
        check_type="http",
        target="https://example.com",
        state=CheckState.DOWN,
        latency_ms=None,
    )
    mock_notifier = AsyncMock()

    with (
        patch("monitor.scheduler._build_check") as mock_build,
        patch("monitor.scheduler.record_outcome") as mock_record,
    ):
        mock_check = AsyncMock()
        mock_check.run.return_value = outcome
        mock_build.return_value = mock_check

        await _run_service_job(svc, MagicMock(return_value=mock_session), notifier=mock_notifier)

    mock_record.assert_called_once()
    mock_notifier.notify.assert_awaited_once_with(outcome)
