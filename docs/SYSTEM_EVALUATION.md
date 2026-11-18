# Scalability and Interoperability Evaluation

## Scope

This evaluation addresses source-adapter interoperability and bounded concurrency in the ingestion job runner. It does not claim that the complete deployment, external providers, or all data persistence paths have been load-tested.

## Code changes made

- Scheduler capacity is configurable with `SCHEDULER_WORKERS` (default 4, accepted range 1–32).
- Database pool sizing is configurable with `POSTGRES_POOL_MIN` (default 1, range 1–32) and `POSTGRES_POOL_MAX` (default 12, range from the minimum through 64). Scheduler workers may not exceed the configured pool maximum, preventing a configuration that schedules more ingestion workers than the DB pool can serve.
- The common adapter contract now returns `list[NormalizedRecord]`. SMHI is converted from its provider-specific observation into this common shape while preserving its existing TimescaleDB telemetry write and Orion-LD mirror.
- The connector runner validates record type, required identity fields, dictionary attributes, timezone-aware timestamps, paired numeric coordinates, and WGS84 coordinate ranges before records can reach storage.
- `.env.example` documents the capacity variables. Tune these values using the evaluation workload and available DB/server resources rather than assuming a higher worker count is automatically faster.

## Test protocol

The tests run inside the Docker Compose ingestor container against the Compose TimescaleDB service. Adapter tests use deterministic fixtures so network availability and provider response time do not affect conformity results.

The job persistence benchmark replaces connector jobs with a controlled 5 ms delay and a one-record `JobResult`. For each worker setting (1, 2, and 4), it runs six registered source IDs, four runs per source, and three repetitions: 72 runs per setting. Each completed run writes its run-history row through the production `run_source` and `record_run` paths. A separate scheduler dispatch benchmark now calls the production `Scheduler.tick()` with six due sources and controlled 20 ms jobs; it records wall time, observed maximum active jobs, and throughput for worker settings 1, 2, and 4.

Metrics:

- Median trial throughput: completed runs divided by wall-clock time for each repetition; reported as the median of the three repetitions. This includes run-history persistence.
- Median and p95 job duration: `JobResult.duration_ms` as recorded by `run_source`. The current implementation captures this before inserting the run-history row, so these values exclude run-history database latency.
- Failed-run count: runs whose status was not `ok`.

## Historical baseline

Pre-change baseline measured on 2026-11-11 in the project’s Python 3.12 Docker container, with TimescaleDB running under Compose:

The initial focused evaluation suite completed with **6 passed**. Its results are retained below strictly as the pre-change baseline.

| Worker count | Runs | Median trial throughput (runs/s) | Median job duration (ms) | p95 job duration (ms) | Failed runs |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 72 | 65.68 | 5 | 5 | 0 |
| 2 | 72 | 42.47 | 5 | 5 | 0 |
| 4 | 72 | 24.48 | 5 | 7 | 0 |

In this bounded synthetic workload, increasing concurrency did not increase throughput. This is an observed result, not a diagnosis of the cause; the benchmark does not isolate database contention, Docker resource limits, or thread scheduling. It does show that the current evidence does not justify a broad claim that higher concurrency improves scalability.

## Post-change measurements

Measured on 2026-11-12 after configurable capacity and normalized-record validation were implemented. The tests ran in the Python 3.12 Compose ingestor against TimescaleDB.

Job execution and run-history persistence benchmark (72 runs per worker setting; three repetitions; 5 ms simulated connector delay):

| Workers | Runs | Median trial throughput (runs/s) | Median job duration (ms) | p95 job duration (ms) | Failed runs |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 72 | 99.55 | 5 | 5 | 0 |
| 2 | 72 | 104.22 | 5 | 5 | 0 |
| 4 | 72 | 96.86 | 5 | 6 | 0 |

Actual scheduler dispatch benchmark (`Scheduler.tick()`, six due sources, three worker settings, 20 ms simulated job duration):

| Workers | Sources dispatched | Maximum active jobs | Wall time (s) | Throughput (sources/s) |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 6 | 1 | 0.1223 | 49.06 |
| 2 | 6 | 2 | 0.0619 | 96.99 |
| 4 | 6 | 4 | 0.0421 | 142.41 |

Interpretation: controlled scheduler dispatch scaled close to the worker limit for six short simulated tasks. The DB-backed run-history benchmark did not scale monotonically: two workers were marginally faster than one, while four were slightly slower. This suggests scheduler concurrency works, but database/history overhead or measurement noise limits gains in this small workload. It is not a production capacity estimate.

Post-change verification: **13 focused evaluation tests passed**; the combined source-honesty and evaluation suites reported **32 passed**. The full browser E2E suite reported **39 passed in 98.79 seconds** after fixing map initialization with blocked external tiles and making the disabled-source card explicitly show its unavailable state. A fail-fast run of the broader non-E2E backend suite reported 16 passed before an unrelated XLSX export test failed because the endpoint fell back to CSV instead of returning the expected Excel content type; later tests were not run in that pass.

## Interoperability and fault-isolation checks

The automated checks verify:

- All six registered adapters implement the shared adapter base interface and expose distinct source identifiers.
- All six adapters, including SMHI, now emit lists of `NormalizedRecord` through the adapter contract.
- Five regional adapters and SMHI are passed through the production connector runner using representative provider records; checks cover required identifiers, dictionary attributes, timezone-aware timestamps, and valid paired WGS84 coordinates when present.
- Adapter connection checks use a consistent response shape (`source`, `status`, `record_count`, and `message`).
- The connector runner rejects a regional record missing required attributes.
- New conformity cases reject non-record values, non-object attributes, naive timestamps, unpaired coordinates, and out-of-range WGS84 coordinates.
- A simulated connector error from one source is reported as an error while the other five concurrent source jobs still succeed.

These are deterministic conformity tests of the internal adapter/runner boundary. They do not prove that every provider API is currently reachable or conforms to its external standard; live-provider checks require credentials and a separately recorded network test.

## Live-provider and deployed-system integration snapshot

On 2026-11-11, the existing `verify_sources.py` workflow was run from the deployed ingestor container. It performs provider fetches and connector validation without writing ingestion records.

| Provider | Result | Duration | Records | Interpretation |
| --- | --- | ---: | ---: | --- |
| SMHI | Live response | 0.24 s | 1 | Live observation fetched and validated. |
| Trafikverket | Live response | 0.57 s | 83 | API key was configured; live station records fetched and validated. |
| SGU | Live response | 5.11 s | 293 | Public API returned and validated regional records. |
| SCB | Fallback | 27.06 s | 15 | The live request timed out; the connector returned explicitly labelled fallback records. This is not a live-provider success. |
| Skogsstyrelsen | Skipped | 0 s | 0 | Client ID and client secret are not configured. |
| Lantmäteriet | Skipped | 0 s | 0 | API key is not configured; base URL and collection are configured. |

Summary: 3 live responses, 1 labelled fallback, 2 credential-based skips, and no connector hard errors. These durations are single-run observations, not latency percentiles.

The deployed Compose snapshot had all **5 of 5 services running**: ingestor, TimescaleDB, Orion-LD, MongoDB, and Grafana. Read-only checks returned HTTP 200 for the datasource summary API, cockpit UI, Orion-LD version endpoint, typed Orion-LD entity query, and Grafana health endpoint. MongoDB's ping command returned `1`. The authenticated `/api/system/health` endpoint reported `database.ok=true`, `scheduler.enabled=true`, and `scheduler.alive=true`.

The scheduler run-history table contained successful scheduled runs for four currently runnable sources at the time checked: SMHI (9), Trafikverket (9), SCB (1), and SGU (1). Skogsstyrelsen and Lantmäteriet were not scheduled because required credentials were missing. These counts describe the existing database history and are not a controlled scheduler load test.

## Qualitative assessment

The adapters provide source isolation at the application boundary: callers use the registry and common adapter operations, while all providers normalize provider-specific responses into `NormalizedRecord`. SMHI still writes its single telemetry observation to the dedicated telemetry table and mirrors it to Orion-LD; its adapter output contract is no longer an exception. The fault-isolation test confirms that a connector failure is returned for that run without cancelling its peers.

## Reproduction

Install the development dependencies and Chromium in the running ingestor container, then run:

```powershell
docker compose exec ingestor pip install -r requirements-dev.txt
docker compose exec ingestor python -m playwright install --with-deps chromium
docker compose exec -e TEST_POSTGRES_HOST=timescaledb ingestor pytest tests/test_system_evaluation.py -q -s
```

Set `SCHEDULER_WORKERS`, `POSTGRES_POOL_MIN`, and `POSTGRES_POOL_MAX` in `.env` before starting the service to evaluate different capacity configurations. Keep scheduler workers at or below the database pool maximum.

The `TEST_POSTGRES_HOST` value is required because the test fixture otherwise defaults to `localhost`, which refers to the ingestor container rather than the Compose database service.

## Limitations and next evaluation

- Connector work is synthetic; the benchmark does not exercise external HTTP calls or source-specific data-table writes.
- The job persistence benchmark exercises `app.jobs.run_source` and run-history persistence; the separate scheduler benchmark exercises `Scheduler.tick()` with deterministic jobs. Neither is a full HTTP/API capacity test.
- CPU, memory, queue delay, and end-to-end ingestion latency were not measured.
- Three repetitions and 72 runs per concurrency setting provide a bounded project evaluation, not a production capacity estimate.
- Internal adapter checks are not a full OGC API Features, PxWeb, NGSI-LD, or provider-specific standards certification.
- The E2E suite validates browser workflows but is not a load test; it does not establish system behavior under many concurrent users.

A broader claim would require repeated load and stress tests against a deployment-like stack, with representative payload volumes, resource monitoring, external-provider latency separated from local processing, and explicitly defined acceptance criteria.