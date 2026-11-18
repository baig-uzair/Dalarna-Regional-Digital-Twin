# Project Architecture, Scalability, and Interoperability Report

## 1. Purpose and scope

This report compares the original implementation with the code changes performed during the project work and evaluates the resulting ingestion architecture, scalability mechanisms, and interoperability checks. It covers the adapter refactor, configurable scheduler/database capacity, normalized source-data contract, production validation, and browser/deployment fixes.

This is a targeted modularization and evaluation of the ingestion subsystem, not a complete redesign of the whole application. FastAPI routing, the overall scheduler pattern, the database schema, and the frontend/backend deployment topology remain in place. The work does not claim that the platform is infinitely scalable or production-capacity certified. The benchmarks use a local Docker Compose deployment and deterministic simulated connector work.

## 2. Before-and-after architecture comparison

| Area | Original implementation | Current implementation |
| --- | --- | --- |
| Source integration | `app/jobs.py` imported provider connector modules directly and its job map coupled a source job to a connector module/function. Connection tests used source-specific branching. | Each of the six providers has a dedicated adapter module; `adapters/registry.py` resolves adapters by source ID and `adapters/base.py` defines their shared interface. Jobs request adapters through the registry. |
| Source data contract | Five regional connectors returned normalized records; SMHI returned its provider-specific `Observation` object. Validation checked only basic IDs/attributes. | All six adapters return `list[NormalizedRecord]`, including SMHI. `connector_runner.py` validates record type, identifiers, attributes, timestamps, and coordinates before storage. |
| Scheduling | Registry-driven scheduler with an in-flight guard and a fixed four-worker pool. | The same scheduler architecture is retained, but worker count is configurable and range-checked. The in-flight guard and per-source locks remain. |
| Database access | Threaded connection pool fixed at minimum 1 and maximum 12 connections. | Minimum and maximum pool sizes are configurable; scheduler worker count cannot exceed the configured maximum. |
| Visualization/integration | Existing FastAPI dashboard, scheduler, TimescaleDB, Orion-LD, MongoDB, and Grafana stack. | The overall stack remains the same. A map initialization resilience fix and clearer disabled-source state were added after E2E testing. |

The adapter work changes the **data-ingestion boundary**: external provider implementations are separated behind dedicated adapters and a registry. It does not create one container or microservice per provider, and it does not replace the FastAPI application architecture.

### Supporting setup and verification work

- `playwright==1.65.0` was added to `requirements-dev.txt` to run existing browser tests. Chromium and required browser libraries were installed in the Docker test container.
- The Compose app host port was changed from 8000 to 8001 because port 8000 was already occupied on the development machine. The container still listens on port 8000 internally.
- `tests/test_sources_honesty.py` gained an adapter-registry regression check; `tests/test_system_evaluation.py` contains the new conformity and scalability tests.

## 3. Original scalability and interoperability limitations

Before this iteration, ingestion capacity was controlled by fixed values in code:

- The scheduler created a thread pool with `max_workers=4` in `app/scheduler.py`.
- The database layer created a `ThreadedConnectionPool` with a fixed minimum of 1 and maximum of 12 connections in `app/db.py`.
- These values could not be adjusted through documented environment configuration to compare operating points.
- Five regional source adapters returned `NormalizedRecord` objects, while the SMHI adapter returned its provider-specific `Observation` dataclass. Thus, the adapter interface did not produce one normalized return type across all six sources.
- The connector runner checked source and dataset IDs and attributes, but did not check the normalized record type, timestamp timezone, coordinate pairing, numeric coordinate values, or WGS84 bounds.
- Browser tests also exposed a map-readiness issue: the map initialization waited for the MapLibre `load` event while its external Esri tile host was blocked by the offline E2E fixture. The local municipality layers therefore never became ready in that environment.

The original design already had useful resilience foundations: scheduled due-source checks, per-source run locks, an in-flight guard, and pooled database access. This work makes capacity tunable and makes the adapter contract and its validation consistent rather than replacing those mechanisms.

## 4. Implemented changes

### 3.1 Configurable ingestion capacity

The scheduler worker limit and database pool bounds are now environment-configurable:

- `SCHEDULER_WORKERS`: default 4, accepted range 1–32.
- `POSTGRES_POOL_MIN`: default 1, accepted range 1–32.
- `POSTGRES_POOL_MAX`: default 12, accepted range from the configured minimum through 64.

Configuration is validated when read. Invalid integers and out-of-range values raise a clear `ValueError`. The scheduler refuses a worker count above `POSTGRES_POOL_MAX`, preventing the scheduler from being configured to run more concurrent source jobs than the database pool can serve. The settings are documented in `.env.example`.

Changed implementation files: `app/config.py`, `app/scheduler.py`, and `app/db.py`.

### 3.2 Common normalized adapter contract

The adapter contract now specifies `fetch_records() -> list[NormalizedRecord]`. The SMHI adapter converts its observation into the common representation, including source/dataset identifiers, feature ID, observed timestamp, WGS84 coordinates and geometry, temperature attribute, provider URL, and license. Its persistence destination remains the existing telemetry table, and the best-effort Orion-LD mirror remains in place.

Changed implementation files: `adapters/base.py`, `adapters/smhi_adapter.py`, and `app/jobs.py`.

### 3.3 Production conformity validation

The connector runner now rejects records that:

- are not `NormalizedRecord` instances;
- lack source or dataset identifiers;
- do not contain attributes as a dictionary;
- contain non-timezone-aware `fetched_at` or `observed_at` values;
- provide only one coordinate, nonnumeric coordinates, or longitude/latitude outside WGS84 ranges.

This means invalid output is rejected at the connector boundary before it is sent to source storage. The checks apply consistently to all adapters.

Changed implementation file: `connector_runner.py`.

### 3.4 Deployment resilience and browser validation

The map now initializes its local background and municipality layers before attaching the optional remote Esri imagery source. If that external tile service is blocked or unavailable, local map layers can still initialize and work.

Disabled source cards now explicitly show `Unavailable` in addition to the disabled status. This makes source availability clear to dashboard users and satisfies the E2E behavior specification.

Changed implementation files: `static/js/dtmap.js` and `static/js/dashboard.js`.

### 3.5 Live-provider and deployment checks

The provider verifier was run against the live sources without persisting ingestion records. The Compose stack was also checked at runtime: all five services were running, the FastAPI API and cockpit, Grafana health, Orion-LD version/entity endpoints, and MongoDB ping responded successfully; the authenticated system health endpoint reported the database healthy and scheduler alive. The scheduler run-history table showed successful scheduled runs for the four currently configured sources.

| Provider | Check result | Time | Records | Notes |
| --- | --- | ---: | ---: | --- |
| SMHI | Live response | 0.24 s | 1 | Live observation validated. |
| Trafikverket | Live response | 0.57 s | 83 | API key configured; live stations validated. |
| SGU | Live response | 5.11 s | 293 | Public API records validated. |
| SCB | Labelled fallback | 27.06 s | 15 | Live API timed out; these are not live-fetched records. |
| Skogsstyrelsen | Skipped | 0 s | 0 | Required OAuth credentials absent. |
| Lantmäteriet | Skipped | 0 s | 0 | API key absent. |

This was a single integration snapshot, not repeated provider-latency measurement. The timing and credentials available on the test date limit what can be concluded about provider interoperability.

## 4. Evaluation method

The tests ran in the Python 3.12 Docker Compose ingestor container with TimescaleDB available on the Compose network. The test database host was explicitly set to `timescaledb`.

### 4.1 Job and run-history benchmark

For each worker setting (1, 2, and 4), the benchmark performed 72 jobs: six source IDs, four runs per source, and three repetitions. Each synthetic connector job slept for 5 ms and returned one record. The production `run_source()` and run-history insertion path were used.

Reported metrics:

- Median trial throughput: completed runs divided by each repetition's wall time; the median of three trials is reported.
- Median and p95 `JobResult.duration_ms`.
- Failed run count.

`duration_ms` is measured before the run-history insert, so it excludes that database write; throughput includes it.

### 4.2 Scheduler dispatch benchmark

The new scheduler benchmark calls the production `Scheduler.tick()` with six due sources and deterministic 20 ms jobs. It is repeated at worker counts 1, 2, and 4. It records wall time, throughput, and observed maximum simultaneous jobs. It is a scheduler-dispatch benchmark, not an external-provider or full-system stress test.

### 4.3 Interoperability/conformity tests

Automated tests check all six registered adapters through the shared interface and send representative outputs through the production connector runner. They test required IDs, attributes, timestamp timezone awareness, coordinate pairing and bounds, adapter connectivity response shape, and rejection of malformed records. A fault-isolation check verifies that one failing source does not prevent its five peers from completing.

### 4.4 End-to-end browser tests

The complete Playwright E2E suite runs Chromium against a real FastAPI process and test database. It covers control-room workflows, dashboard behavior, configuration preview/apply, and map interactions.

## 6. Results

### 5.1 Job and run-history benchmark after changes

| Workers | Runs | Median trial throughput (runs/s) | Median job duration (ms) | p95 job duration (ms) | Failed runs |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 72 | 99.55 | 5 | 5 | 0 |
| 2 | 72 | 104.22 | 5 | 5 | 0 |
| 4 | 72 | 96.86 | 5 | 6 | 0 |

The run-history benchmark is nearly flat. Two workers were about 4.7% above the one-worker result, while four workers were about 2.7% below it. Therefore, increasing concurrency did not produce a reliable throughput gain for the DB-backed synthetic job workload.

### 5.2 Scheduler dispatch after changes

| Workers | Sources dispatched | Maximum active jobs | Wall time (s) | Throughput (sources/s) |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 6 | 1 | 0.1223 | 49.06 |
| 2 | 6 | 2 | 0.0619 | 96.99 |
| 4 | 6 | 4 | 0.0421 | 142.41 |

The scheduler respected the configured concurrency at each setting and completed all six source tasks. Throughput increased from 49.06 to 96.99 to 142.41 sources/s as workers increased from 1 to 2 to 4. This controlled result demonstrates that the scheduler dispatches work concurrently; it does not prove that the real provider/database pipeline will scale at the same rate.

### 5.3 Test results

- Focused scalability and interoperability suite: **13 passed**.
- Combined source honesty and evaluation suites: **32 passed**, with one existing Starlette/httpx deprecation warning.
- Full browser E2E suite: **39 passed in 98.79 seconds**.
- The three previously failing E2E cases for map recoloring, map layers/zoom, and disabled-source status were rerun after the fixes: **3 passed in 9.92 seconds**.
- A fail-fast run of the broader non-E2E backend suite reached **16 passed, 1 failed**. The first failure is `tests/test_control_extended.py::test_multi_format_export`: the XLSX request returned `text/csv` rather than an Excel content type because the existing export code falls back to CSV when XLSX generation fails. Export behavior is outside the scalability/interoperability changes; later backend tests were not run in this fail-fast pass.

## 7. Interpretation

The work provides concrete but bounded evidence:

1. Capacity can now be tuned through environment settings, with validation that prevents scheduler-worker configuration from exceeding the DB pool maximum.
2. The scheduler dispatch path demonstrably runs up to the configured worker limit and improves throughput for independent, short-running simulated tasks.
3. The DB-backed job benchmark is nearly flat across the tested worker counts. This indicates that additional threads alone do not guarantee greater end-to-end throughput and that database write cost, thread scheduling, and measurement variance need separate profiling.
4. All six adapters now use one internal record contract, and malformed records are rejected before storage. This improves internal interoperability and fault isolation.
5. The E2E suite passing confirms key user-facing workflows, including map behavior when remote imagery is unavailable. It is a functional integration test, not a load test.

The broader backend test suite is not entirely green because of the separate XLSX export failure above. This failure was not changed as part of this work, and the focused ingestion/evaluation and full browser suites pass.

## 8. Limitations

- Connector calls in the two benchmarks are simulated; provider latency, throttling, and varying payload sizes are not represented.
- The scheduler benchmark covers six tasks and three worker settings on one local Compose deployment.
- CPU, memory, queue wait time, database wait time, and end-to-end HTTP ingestion latency were not sampled.
- This project remains a single ingestor service; these changes do not add horizontal replicas, a distributed queue, or multi-instance coordination.
- The adapter tests validate the internal normalized contract, not formal certification against each provider’s full external API standard.
- Live-provider evidence remains mixed: the earlier verification snapshot had live SMHI, Trafikverket, and SGU responses; SCB used labelled fallback after timing out; Skogsstyrelsen and Lantmäteriet were skipped because required credentials were missing.

## 9. Conclusion

The code has moved from fixed concurrency limits and one source-specific adapter output toward configurable ingestion capacity and a validated common record contract across all six sources. The scheduler benchmark demonstrates concurrency benefit in a controlled dispatch workload, while the DB-backed benchmark shows little benefit from adding workers in the tested setup. The current evidence therefore supports improved configurability, scheduler concurrency, interoperability, and end-to-end functional correctness, but not a claim of production-scale capacity. Further capacity claims should be based on longer tests with representative live payload sizes and direct CPU, memory, queue, and database-latency measurements.
