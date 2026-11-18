"""Simulation routes: Scenarios CRUD, execution, safe test runs, persistence, and history."""
from __future__ import annotations

import csv
import io
import logging
import math
from typing import Any, Optional

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from app import auth, control, db
from simulation.engine import ScenarioInputError, get_latest_simulation, run_simulation, set_latest_simulation

logger = logging.getLogger("app.simulation")

router = APIRouter(prefix="/api/simulation", tags=["simulation"])

_RESULTS_CACHE: dict[str, dict[str, Any]] = {}
_LATEST_OFFICIAL: Optional[dict[str, Any]] = None


class RunSimulationRequest(BaseModel):
    scenario: str = Field(default="population_growth")
    scenario_name: Optional[str] = Field(default=None, max_length=100)
    target_year: int = Field(default=2030, ge=2020, le=2100)
    scope: str = Field(default="dalarna")
    driver: Optional[dict[str, Any]] = Field(default_factory=lambda: {"population_change": 0.10})
    excluded_sources: Optional[list[str]] = Field(default_factory=list)
    initial_driver: Optional[dict[str, Any]] = None
    initial_excluded_sources: Optional[list[str]] = None

    @field_validator("driver", "initial_driver")
    @classmethod
    def validate_driver(cls, driver, info: ValidationInfo):
        bounds = {
            "population_change": (-0.20, 0.30),
            "road_temp_delta": (-20, 20),
            "freeze_cycles": (0, 40),
            "land_development_change": (-1, 1),
            "forest_harvest_change": (-1, 2),
            "water_stress_change": (-1, 2),
            "temperature_change": (-15, 15),
            "precipitation_change_pct": (-1, 3),
        }
        for key, value in (driver or {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{info.field_name}.{key} must be a finite number")
            if key in bounds and not bounds[key][0] <= value <= bounds[key][1]:
                low, high = bounds[key]
                raise ValueError(f"{info.field_name}.{key} must be between {low} and {high}")
        return driver


class ScenarioModel(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    driver_source: str = Field(default="SCB")
    driver: dict[str, Any] = Field(default_factory=dict)
    target_year: int = Field(default=2030, ge=2020, le=2100)
    scope: str = Field(default="dalarna")
    affected_sources: list[str] = Field(default_factory=list)
    description: str = Field(default="")


class ScenarioUpdateModel(BaseModel):
    name: Optional[str] = None
    driver_source: Optional[str] = None
    driver: Optional[dict[str, Any]] = None
    target_year: Optional[int] = Field(default=None, ge=2020, le=2100)
    scope: Optional[str] = None
    affected_sources: Optional[list[str]] = None
    description: Optional[str] = None


_BASE_SCENARIO_DRIVER = {
    "population_change": 0.0,
    "road_temp_delta": 0.0,
    "freeze_cycles": 0,
    "land_development_change": 0.0,
    "forest_harvest_change": 0.0,
    "water_stress_change": 0.0,
    "temperature_change": 0.0,
    "precipitation_change_pct": 0.0,
}


def _scenario_driver(**values: int | float) -> dict[str, int | float]:
    return {**_BASE_SCENARIO_DRIVER, **values}


DEFAULT_SCENARIOS = [
    {
        "id": "combined_baseline",
        "name": "Combined Regional Baseline (2030)",
        "driver_source": "Multiple",
        "target_year": 2030,
        "scope": "dalarna",
        "driver": _scenario_driver(
            population_change=0.10,
            road_temp_delta=2.0,
            land_development_change=0.15,
            forest_harvest_change=0.20,
            water_stress_change=-0.15,
            temperature_change=2.0,
        ),
        "affected_sources": ["scb", "trafikverket", "lantmateriet", "skogsstyrelsen", "sgu", "smhi"],
        "description": "Fixed six-source starting point for regional planning; assumptions are illustrative, not forecasts.",
    },
    {
        "id": "population_growth",
        "name": "Population Growth (+10% by 2030)",
        "driver_source": "SCB",
        "target_year": 2030,
        "scope": "dalarna",
        "driver": _scenario_driver(population_change=0.10),
        "affected_sources": ["scb", "trafikverket", "lantmateriet", "skogsstyrelsen", "sgu", "smhi"],
        "description": "Baseline regional demographic growth across 15 municipalities.",
    },
    {
        "id": "rapid_urbanization",
        "name": "Rapid Urbanization (+18% by 2035)",
        "driver_source": "SCB",
        "target_year": 2035,
        "scope": "dalarna",
        "driver": _scenario_driver(population_change=0.18, land_development_change=0.15),
        "affected_sources": ["scb", "trafikverket", "lantmateriet", "skogsstyrelsen", "sgu", "smhi"],
        "description": "Concentrated expansion in Borlänge, Falun, and regional corridors.",
    },
    {
        "id": "climate_freeze_thaw",
        "name": "Severe Winter Freeze-Thaw",
        "driver_source": "Trafikverket",
        "target_year": 2026,
        "scope": "dalarna",
        "driver": _scenario_driver(road_temp_delta=-3.5, freeze_cycles=14, temperature_change=-2.0),
        "affected_sources": ["scb", "trafikverket", "lantmateriet", "skogsstyrelsen", "sgu", "smhi"],
        "description": "Infrastructure stress and black-ice risk on high-traffic routes (E16).",
    },
    {
        "id": "summer_drought",
        "name": "Summer Drought and Groundwater Stress",
        "driver_source": "SGU",
        "target_year": 2035,
        "scope": "dalarna",
        "driver": _scenario_driver(water_stress_change=0.35, temperature_change=2.5, precipitation_change_pct=-0.25),
        "affected_sources": ["scb", "trafikverket", "lantmateriet", "skogsstyrelsen", "sgu", "smhi"],
        "description": "A hotter, drier summer scenario with increased groundwater stress.",
    },
    {
        "id": "heavy_precipitation",
        "name": "Warmer Climate and Heavy Precipitation",
        "driver_source": "SMHI",
        "target_year": 2040,
        "scope": "dalarna",
        "driver": _scenario_driver(water_stress_change=0.10, temperature_change=2.0, precipitation_change_pct=0.50),
        "affected_sources": ["scb", "trafikverket", "lantmateriet", "skogsstyrelsen", "sgu", "smhi"],
        "description": "A warmer, wetter scenario that increases modeled snow/flood and environmental risk.",
    },
    {
        "id": "forest_harvest_increase",
        "name": "Increased Forest Harvest",
        "driver_source": "Skogsstyrelsen",
        "target_year": 2035,
        "scope": "dalarna",
        "driver": _scenario_driver(forest_harvest_change=0.25, land_development_change=0.05),
        "affected_sources": ["scb", "trafikverket", "lantmateriet", "skogsstyrelsen", "sgu", "smhi"],
        "description": "A higher forest-harvest intensity scenario with land and environmental effects.",
    },
    {
        "id": "moderate_regional_growth",
        "name": "Moderate Regional Growth (+6% by 2035)",
        "driver_source": "Multiple",
        "target_year": 2035,
        "scope": "dalarna",
        "driver": _scenario_driver(
            population_change=0.06,
            road_temp_delta=0.8,
            freeze_cycles=8,
            land_development_change=0.08,
            forest_harvest_change=0.05,
            water_stress_change=0.05,
            temperature_change=1.3,
            precipitation_change_pct=0.08,
        ),
        "affected_sources": ["scb", "trafikverket", "lantmateriet", "skogsstyrelsen", "sgu", "smhi"],
        "description": "A moderate regional planning case combining population growth, development, and gradual climate pressure.",
    },
    {
        "id": "urban_growth_transport",
        "name": "Urban Growth and Transport Pressure (+15% by 2040)",
        "driver_source": "Multiple",
        "target_year": 2040,
        "scope": "dalarna",
        "driver": _scenario_driver(
            population_change=0.15,
            road_temp_delta=1.2,
            freeze_cycles=9,
            land_development_change=0.25,
            forest_harvest_change=0.08,
            water_stress_change=0.10,
            temperature_change=1.8,
            precipitation_change_pct=0.10,
        ),
        "affected_sources": ["scb", "trafikverket", "lantmateriet", "skogsstyrelsen", "sgu", "smhi"],
        "description": "A higher-growth case with more land conversion, transport demand, and associated environmental pressure.",
    },
    {
        "id": "warmer_wetter_winter",
        "name": "Warmer, Wetter Winter with Freeze-Thaw (2040)",
        "driver_source": "Multiple",
        "target_year": 2040,
        "scope": "dalarna",
        "driver": _scenario_driver(
            population_change=0.04,
            road_temp_delta=1.5,
            freeze_cycles=14,
            land_development_change=0.03,
            forest_harvest_change=0.04,
            water_stress_change=0.18,
            temperature_change=2.0,
            precipitation_change_pct=0.30,
        ),
        "affected_sources": ["scb", "trafikverket", "lantmateriet", "skogsstyrelsen", "sgu", "smhi"],
        "description": "A milder, wetter winter assumption with repeated freeze-thaw events and increased runoff pressure.",
    },
    {
        "id": "forest_water_recovery",
        "name": "Managed Forest and Water Recovery (2035)",
        "driver_source": "Multiple",
        "target_year": 2035,
        "scope": "dalarna",
        "driver": _scenario_driver(
            population_change=0.03,
            road_temp_delta=0.5,
            freeze_cycles=8,
            land_development_change=-0.03,
            forest_harvest_change=-0.15,
            water_stress_change=-0.10,
            temperature_change=1.0,
            precipitation_change_pct=0.05,
        ),
        "affected_sources": ["scb", "trafikverket", "lantmateriet", "skogsstyrelsen", "sgu", "smhi"],
        "description": "A conservation-oriented case combining reduced forest-harvest pressure and improved water stress.",
    },
    {
        "id": "what_if",
        "name": "WHAT IF - Custom scenario",
        "driver_source": "Multiple",
        "target_year": 2030,
        "scope": "dalarna",
        "driver": _scenario_driver(
            population_change=0.10,
            road_temp_delta=2.0,
            land_development_change=0.15,
            forest_harvest_change=0.20,
            water_stress_change=-0.15,
            temperature_change=2.0,
        ),
        "affected_sources": ["scb", "trafikverket", "lantmateriet", "skogsstyrelsen", "sgu", "smhi"],
        "description": "Customize the source assumptions for this run.",
    },
]


def _scenario_driver_for_run(scenario_id: str, requested_driver: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if scenario_id == "what_if":
        return requested_driver

    default = next((scenario for scenario in DEFAULT_SCENARIOS if scenario["id"] == scenario_id), None)
    try:
        with db.cursor() as cur:
            cur.execute("SELECT driver FROM scenarios WHERE id = %s", (scenario_id,))
            row = cur.fetchone()
        if row:
            return {**(default["driver"] if default else {}), **(row.get("driver") or {})}
    except Exception as exc:
        logger.warning("Could not load fixed driver for scenario %s: %s", scenario_id, exc)
    return default["driver"] if default else requested_driver


def _persist_simulation_run(run_id: str, scenario_id: str, is_test: bool, input_data: dict, result: dict, model_version: str):
    try:
        with db.cursor() as cur:
            cur.execute("SELECT 1 FROM scenarios WHERE id = %s", (scenario_id,))
            sc_exists = cur.fetchone()
            sid = scenario_id if sc_exists else None
            cur.execute(
                """
                INSERT INTO simulation_runs (run_id, scenario_id, is_test, input, result, model_version, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (run_id) DO UPDATE SET result = EXCLUDED.result
                """,
                (
                    run_id,
                    sid,
                    is_test,
                    psycopg2.extras.Json(input_data),
                    psycopg2.extras.Json(result),
                    model_version,
                ),
            )
    except Exception as exc:
        logger.warning("Could not persist simulation run %s: %s", run_id, exc)


@router.get("/scenarios")
def list_scenarios():
    """List available simulation scenarios directly from database."""
    try:
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, name, driver_source, driver, target_year, scope, affected_sources, description, created_by, created_at, updated_at "
                "FROM scenarios ORDER BY created_at ASC, id ASC"
            )
            rows = cur.fetchall()
            if rows:
                saved = {row["id"]: row for row in rows}
                scenarios = []
                for default in DEFAULT_SCENARIOS:
                    row = saved.pop(default["id"], None)
                    if row is None:
                        scenarios.append(default)
                        continue
                    merged = {**default, **row}
                    merged["driver"] = {**default["driver"], **(row.get("driver") or {})}
                    merged["affected_sources"] = default["affected_sources"]
                    scenarios.append(merged)
                scenarios.extend(saved.values())
                return {"scenarios": scenarios}
    except Exception as exc:
        logger.warning("Could not fetch scenarios from database, using defaults: %s", exc)
    return {"scenarios": DEFAULT_SCENARIOS}


def _set_run_metadata(result: dict[str, Any], body: RunSimulationRequest, live: dict) -> None:
    if body.scenario == "what_if":
        result["scenario_name"] = (body.scenario_name or "").strip() or "WHAT IF Scenario"
    if body.initial_driver is not None:
        result["assumptions"]["initial_driver"] = body.initial_driver
    if body.initial_excluded_sources is not None:
        disabled = {source for source, enabled in live.get("sources", {}).items() if enabled is False}
        result["assumptions"]["initial_excluded_sources"] = sorted(set(body.initial_excluded_sources) | disabled)


def _run_for_live_state(body: RunSimulationRequest, live: dict, is_test: bool) -> tuple[dict, Optional[dict[str, Any]], list[str]]:
    driver = _scenario_driver_for_run(body.scenario, body.driver)
    disabled = {source for source, enabled in live.get("sources", {}).items() if enabled is False}
    excluded = sorted(set(body.excluded_sources or []) | disabled)
    try:
        result = run_simulation(
            scenario=body.scenario, target_year=body.target_year, scope=body.scope,
            driver=driver, excluded_sources=excluded, thresholds=live.get("risk_thresholds"),
            weights=live.get("weights"), is_test=is_test,
        )
    except ScenarioInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result, driver, excluded


@router.post("/scenarios", status_code=201)
def create_scenario(body: ScenarioModel, user: str = Depends(auth.require_admin)):
    """Create a new simulation scenario."""
    try:
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scenarios (id, name, driver_source, driver, target_year, scope, affected_sources, description, created_by, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    driver_source = EXCLUDED.driver_source,
                    driver = EXCLUDED.driver,
                    target_year = EXCLUDED.target_year,
                    scope = EXCLUDED.scope,
                    affected_sources = EXCLUDED.affected_sources,
                    description = EXCLUDED.description,
                    updated_at = now()
                RETURNING id, name, driver_source, driver, target_year, scope, affected_sources, description, created_by, created_at, updated_at
                """,
                (
                    body.id,
                    body.name,
                    body.driver_source,
                    psycopg2.extras.Json(body.driver or {}),
                    body.target_year,
                    body.scope,
                    psycopg2.extras.Json(body.affected_sources or []),
                    body.description or "",
                    user,
                ),
            )
            created = cur.fetchone()
            control.audit_action(user, "simulation.scenario_created", {"id": body.id, "name": body.name})
            return created
    except Exception as exc:
        logger.exception("Failed to create scenario %s: %s", body.id, exc)
        raise HTTPException(status_code=500, detail=f"Database error creating scenario: {exc}")


@router.put("/scenarios/{scenario_id}")
@router.put("/scenarios")
def update_scenario(
    body: ScenarioModel,
    scenario_id: Optional[str] = None,
    user: str = Depends(auth.require_admin),
):
    """Update an existing simulation scenario."""
    sid = scenario_id or body.id
    try:
        with db.cursor() as cur:
            cur.execute(
                """
                UPDATE scenarios
                SET name = %s,
                    driver_source = %s,
                    driver = %s,
                    target_year = %s,
                    scope = %s,
                    affected_sources = %s,
                    description = %s,
                    updated_at = now()
                WHERE id = %s
                RETURNING id, name, driver_source, driver, target_year, scope, affected_sources, description, created_by, created_at, updated_at
                """,
                (
                    body.name,
                    body.driver_source,
                    psycopg2.extras.Json(body.driver or {}),
                    body.target_year,
                    body.scope,
                    psycopg2.extras.Json(body.affected_sources or []),
                    body.description or "",
                    sid,
                ),
            )
            updated = cur.fetchone()
            if not updated:
                raise HTTPException(status_code=404, detail=f"Scenario '{sid}' not found")
            control.audit_action(user, "simulation.scenario_updated", {"id": sid, "name": body.name})
            return updated
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to update scenario %s: %s", sid, exc)
        raise HTTPException(status_code=500, detail=f"Database error updating scenario: {exc}")


@router.delete("/scenarios/{scenario_id}")
def delete_scenario(scenario_id: str, user: str = Depends(auth.require_admin)):
    """Delete a simulation scenario."""
    try:
        with db.cursor() as cur:
            # Nullify any foreign key references from simulation runs to allow clean scenario deletion
            cur.execute("UPDATE simulation_runs SET scenario_id = NULL WHERE scenario_id = %s", (scenario_id,))
            cur.execute("DELETE FROM scenarios WHERE id = %s RETURNING id, name", (scenario_id,))
            deleted = cur.fetchone()
            if not deleted:
                raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found")
            control.audit_action(user, "simulation.scenario_deleted", {"id": scenario_id, "name": deleted.get("name")})
            return {"ok": True, "message": f"Scenario '{scenario_id}' deleted successfully", "id": scenario_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to delete scenario %s: %s", scenario_id, exc)
        raise HTTPException(status_code=500, detail=f"Database error deleting scenario: {exc}")


@router.post("/run")
def execute_simulation(body: RunSimulationRequest, user: Optional[str] = Depends(auth.current_user)):
    """Execute scenario and persist as latest official result."""
    global _LATEST_OFFICIAL
    live = control.get_live()
    result, driver, excluded = _run_for_live_state(body, live, is_test=False)
    _set_run_metadata(result, body, live)
    _RESULTS_CACHE[result["run_id"]] = result
    _LATEST_OFFICIAL = result
    set_latest_simulation(result)
    input_data = body.model_dump()
    input_data["driver"] = driver
    input_data["excluded_sources"] = excluded
    _persist_simulation_run(
        run_id=result["run_id"],
        scenario_id=body.scenario,
        is_test=False,
        input_data=input_data,
        result=result,
        model_version=result.get("model_version", "1.4.0"),
    )
    if user:
        control.audit_action(
            user,
            "simulation.run_official",
            {"run_id": result["run_id"], "scenario": body.scenario, "year": body.target_year},
        )
    logger.info("Executed official simulation %s (scenario: %s, year: %d)", result["run_id"], body.scenario, body.target_year)
    return result


@router.post("/test")
def test_simulation(body: RunSimulationRequest, user: str = Depends(auth.require_admin)):
    """Run safe test simulation: does not update the official latest result."""
    live = control.get_live()
    result, driver, excluded = _run_for_live_state(body, live, is_test=True)
    _set_run_metadata(result, body, live)
    _RESULTS_CACHE[result["run_id"]] = result
    input_data = body.model_dump()
    input_data["driver"] = driver
    input_data["excluded_sources"] = excluded
    _persist_simulation_run(
        run_id=result["run_id"],
        scenario_id=body.scenario,
        is_test=True,
        input_data=input_data,
        result=result,
        model_version=result.get("model_version", "1.4.0"),
    )
    control.audit_action(user, "simulation.run_test", {"run_id": result["run_id"], "scenario": body.scenario, "year": body.target_year})
    logger.info("Executed safe test simulation %s by admin %s", result["run_id"], user)
    return result


@router.get("/result/latest")
def get_latest_result():
    """Retrieve latest official simulation result."""
    global _LATEST_OFFICIAL
    if _LATEST_OFFICIAL is not None:
        return _LATEST_OFFICIAL
    try:
        with db.cursor() as cur:
            cur.execute("SELECT result FROM simulation_runs WHERE is_test = FALSE ORDER BY created_at DESC LIMIT 1")
            row = cur.fetchone()
            if row and row.get("result"):
                _LATEST_OFFICIAL = row["result"]
                _RESULTS_CACHE[_LATEST_OFFICIAL["run_id"]] = _LATEST_OFFICIAL
                set_latest_simulation(_LATEST_OFFICIAL)
                return _LATEST_OFFICIAL
    except Exception as exc:
        logger.warning("Could not fetch latest simulation from database: %s", exc)
    _LATEST_OFFICIAL = get_latest_simulation()
    if _LATEST_OFFICIAL:
        _RESULTS_CACHE[_LATEST_OFFICIAL["run_id"]] = _LATEST_OFFICIAL
    return _LATEST_OFFICIAL


@router.get("/result/{run_id}")
def get_result_by_id(run_id: str):
    """Retrieve specific simulation run result by ID."""
    res = _RESULTS_CACHE.get(run_id)
    if res is not None:
        return res
    try:
        with db.cursor() as cur:
            cur.execute("SELECT result FROM simulation_runs WHERE run_id = %s", (run_id,))
            row = cur.fetchone()
            if row and row.get("result"):
                res = row["result"]
                _RESULTS_CACHE[run_id] = res
                return res
    except Exception as exc:
        logger.warning("Could not fetch simulation %s from database: %s", run_id, exc)
    raise HTTPException(status_code=404, detail=f"Simulation result '{run_id}' not found")


@router.get("/history")
def get_simulation_history(limit: int = Query(20, ge=1, le=100)):
    """Retrieve history of simulation runs (both test and official)."""
    try:
        with db.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, scenario_id, is_test, model_version, created_at, input,
                       result->'summary' as summary,
                       result->'target_year' as target_year,
                       result->'scope' as scope,
                       result->'scenario' as scenario,
                       result->>'scenario_name' as scenario_name
                FROM simulation_runs
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return {"runs": rows or []}
    except Exception as exc:
        logger.warning("Could not fetch simulation history: %s", exc)
        return {"runs": []}


@router.get("/export/{run_id}")
def export_simulation_run(run_id: str, format: str = Query("json")):
    """Export simulation run result in JSON or CSV format."""
    res = get_result_by_id(run_id)
    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Municipality Code", "Municipality Name", "Current Population", "Simulated Population",
            "Population Change %", "Overall Risk Score", "Overall Risk Level",
            "Infrastructure Risk", "Food Risk", "Snow/Flood Risk", "Environment Risk"
        ])
        for m in res.get("municipalities", []):
            writer.writerow([
                m.get("code", ""),
                m.get("name", ""),
                m.get("current_population", ""),
                m.get("simulated_population", ""),
                m.get("population_change_pct", ""),
                m.get("overall_risk", {}).get("score", ""),
                m.get("overall_risk", {}).get("level", ""),
                m.get("infrastructure_risk", {}).get("level", ""),
                m.get("food_risk", {}).get("level", ""),
                m.get("snow_flood_risk", {}).get("level", ""),
                m.get("environment_risk", {}).get("level", ""),
            ])
        csv_data = output.getvalue()
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=simulation_{run_id}.csv"},
        )
    return res
