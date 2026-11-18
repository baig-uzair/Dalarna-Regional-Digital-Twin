"""Deterministic adapter conformity and ingestion concurrency evaluation."""
from __future__ import annotations

import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from adapters.base import BaseSourceAdapter
from adapters.registry import get_adapter, list_adapters
from app import config, jobs
from app.scheduler import Scheduler
from connector_runner import run_connector
from connectors import lantmateriet, scb, sgu, skogsstyrelsen, smhi, trafikverket
from connectors.common import ConnectorError, NormalizedRecord


REGIONAL_SOURCES = {
    "trafikverket": (trafikverket, "Trafikverket"),
    "scb": (scb, "SCB"),
    "sgu": (sgu, "SGU"),
    "skogsstyrelsen": (skogsstyrelsen, "Skogsstyrelsen"),
    "lantmateriet": (lantmateriet, "Lantmateriet"),
}


def _record(source_name: str) -> NormalizedRecord:
    return NormalizedRecord(
        source_id=source_name,
        dataset_id="evaluation-fixture",
        feature_id="fixture-1",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        attributes={"value": 1},
        lon=15.0,
        lat=60.0,
    )


def _assert_regional_record(record: NormalizedRecord, source_name: str) -> None:
    assert isinstance(record, NormalizedRecord)
    assert record.source_id == source_name
    assert record.dataset_id
    assert isinstance(record.attributes, dict)
    assert record.fetched_at.tzinfo is not None
    assert (record.lon is None) == (record.lat is None)
    if record.lon is not None:
        assert -180 <= record.lon <= 180
        assert -90 <= record.lat <= 90
    if record.observed_at is not None:
        assert record.observed_at.tzinfo is not None


def test_adapter_registry_and_source_outputs_conform(monkeypatch):
    adapters = list_adapters()
    assert len(adapters) == 6
    assert all(isinstance(adapter, BaseSourceAdapter) for adapter in adapters)
    assert len({adapter.source_id for adapter in adapters}) == len(adapters)

    for source_id, (connector, source_name) in REGIONAL_SOURCES.items():
        record = _record(source_name)
        monkeypatch.setattr(connector, "fetch_records", lambda *args, _record=record, **kwargs: [_record])
        adapter = get_adapter(source_id)
        result = run_connector(source_id, adapter.fetch_records)
        assert result.status == "ok", result.evidence
        records = result.records
        assert len(records) == 1
        _assert_regional_record(records[0], source_name)

        connection = adapter.test_connection()
        assert connection["source"] == source_id
        assert connection["status"] == "ok"
        assert connection["record_count"] == 1
        assert isinstance(connection["message"], str)

    observation = smhi.Observation(
        temperature=4.2,
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(smhi, "fetch_observation", lambda: observation)
    smhi_adapter = get_adapter("smhi")
    result = run_connector("smhi", smhi_adapter.fetch_records)
    assert result.status == "ok", result.evidence
    [fetched] = result.records
    _assert_regional_record(fetched, "SMHI")
    assert fetched.attributes["air_temperature_c"] == observation.temperature
    connection = smhi_adapter.test_connection()
    assert connection["source"] == "smhi"
    assert connection["status"] == "ok"
    assert connection["record_count"] == 1


def test_capacity_settings_are_configurable_and_bounded(monkeypatch):
    monkeypatch.setenv("SCHEDULER_WORKERS", "8")
    monkeypatch.setenv("POSTGRES_POOL_MIN", "2")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "16")
    assert config.scheduler_workers() == 8
    assert config.db_pool_min_connections() == 2
    assert config.db_pool_max_connections() == 16

    monkeypatch.setenv("SCHEDULER_WORKERS", "0")
    with pytest.raises(ValueError, match="SCHEDULER_WORKERS"):
        config.scheduler_workers()

    monkeypatch.setenv("POSTGRES_POOL_MAX", "1")
    with pytest.raises(ValueError, match="POSTGRES_POOL_MAX"):
        config.db_pool_max_connections()

    monkeypatch.setenv("POSTGRES_POOL_MAX", "4")
    monkeypatch.setenv("SCHEDULER_WORKERS", "5")
    with pytest.raises(ValueError, match="cannot exceed"):
        config.scheduler_workers()


def test_regional_record_validation_rejects_missing_required_attributes():
    record = _record("SCB")
    record.attributes = None

    result = run_connector("SCB", lambda: [record])

    assert result.status == "error"
    assert "attributes must be an object" in result.evidence


@pytest.mark.parametrize(
    ("record_updates", "error_text"),
    [
        ({"lon": 200.0, "lat": 60.0}, "valid WGS84"),
        ({"lon": 15.0, "lat": None}, "provided together"),
        ({"observed_at": datetime(2026, 1, 1)}, "timezone-aware"),
    ],
)
def test_regional_record_validation_rejects_nonconformant_fields(record_updates, error_text):
    record = _record("SCB")
    for field_name, value in record_updates.items():
        setattr(record, field_name, value)

    result = run_connector("SCB", lambda: [record])

    assert result.status == "error"
    assert error_text in result.evidence


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_ingestion_runner_concurrency_is_measurable(clean, monkeypatch, workers):
    source_ids = ["smhi", "trafikverket", "scb", "sgu", "skogsstyrelsen", "lantmateriet"]
    runs_per_source = 4
    repetitions = 3
    simulated_connector_seconds = 0.005

    def fake_job():
        time.sleep(simulated_connector_seconds)
        return jobs.JobResult("ok", record_count=1, stored_count=1, data_origin="live")

    for source_id in source_ids:
        monkeypatch.setitem(jobs.CONNECTORS, source_id, fake_job)

    results = []
    trial_throughputs = []
    for _ in range(repetitions):
        trial_results = []
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for _ in range(runs_per_source):
                futures = [
                    executor.submit(jobs.run_source, source_id, source_id, "evaluation")
                    for source_id in source_ids
                ]
                trial_results.extend(future.result() for future in futures)
        elapsed_seconds = time.perf_counter() - started
        results.extend(trial_results)
        trial_throughputs.append(len(trial_results) / elapsed_seconds)

    assert len(results) == len(source_ids) * runs_per_source * repetitions
    assert all(result.status == "ok" for result in results)
    assert all(result.record_count == 1 and result.stored_count == 1 for result in results)

    durations_ms = sorted(result.duration_ms for result in results)
    p95_index = max(0, math.ceil(0.95 * len(durations_ms)) - 1)
    report = {
        "workers": workers,
        "runs": len(results),
        "repetitions": repetitions,
        "simulated_connector_delay_ms": simulated_connector_seconds * 1000,
        "median_trial_throughput_runs_per_second": round(sorted(trial_throughputs)[len(trial_throughputs) // 2], 2),
        "job_duration_median_ms": durations_ms[len(durations_ms) // 2],
        "job_duration_p95_ms": durations_ms[p95_index],
        "failed_runs": sum(result.status != "ok" for result in results),
    }
    print("EVALUATION_METRIC " + json.dumps(report, sort_keys=True))


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_scheduler_dispatch_scales_to_configured_worker_limit(monkeypatch, workers):
    monkeypatch.setenv("POSTGRES_POOL_MIN", "1")
    monkeypatch.setenv("POSTGRES_POOL_MAX", "12")
    source_ids = ["smhi", "trafikverket", "scb", "sgu", "skogsstyrelsen", "lantmateriet"]
    due = [{"id": source_id, "connector": source_id} for source_id in source_ids]
    completed = 0
    active = 0
    max_active = 0
    lock = threading.Lock()
    enough_workers_started = threading.Event()
    all_completed = threading.Event()
    simulated_job_seconds = 0.02

    def fake_run_source(source_id, connector, trigger):
        nonlocal active, max_active, completed
        with lock:
            active += 1
            max_active = max(max_active, active)
            if active >= min(workers, len(source_ids)):
                enough_workers_started.set()
        enough_workers_started.wait(timeout=5)
        time.sleep(simulated_job_seconds)
        with lock:
            active -= 1
            completed += 1
            if completed == len(source_ids):
                all_completed.set()

    scheduler = Scheduler(max_workers=workers)
    monkeypatch.setattr(scheduler, "due_sources", lambda: due)
    monkeypatch.setattr(jobs, "run_source", fake_run_source)

    started = time.perf_counter()
    scheduler.tick()
    assert all_completed.wait(timeout=10), "scheduler did not finish all due sources"
    elapsed_seconds = time.perf_counter() - started
    scheduler._pool.shutdown(wait=True)

    assert completed == len(source_ids)
    assert max_active == min(workers, len(source_ids))
    print("SCHEDULER_METRIC " + json.dumps({
        "workers": workers,
        "sources_dispatched": completed,
        "max_active_jobs": max_active,
        "wall_time_seconds": round(elapsed_seconds, 4),
        "throughput_sources_per_second": round(completed / elapsed_seconds, 2),
        "simulated_job_delay_ms": simulated_job_seconds * 1000,
    }, sort_keys=True))


def test_one_source_failure_does_not_abort_peer_sources(clean, monkeypatch):
    source_ids = ["smhi", "trafikverket", "scb", "sgu", "skogsstyrelsen", "lantmateriet"]

    def failed_job():
        raise ConnectorError("fixture failure")

    def successful_job():
        return jobs.JobResult("ok", record_count=1, stored_count=1, data_origin="live")

    for source_id in source_ids:
        monkeypatch.setitem(jobs.CONNECTORS, source_id, failed_job if source_id == "sgu" else successful_job)

    with ThreadPoolExecutor(max_workers=len(source_ids)) as executor:
        results = list(executor.map(lambda source_id: jobs.run_source(source_id, source_id, "evaluation"), source_ids))

    assert [result.status for result in results].count("error") == 1
    assert [result.status for result in results].count("ok") == len(source_ids) - 1