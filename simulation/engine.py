"""Pure Python cross-sector causal simulation engine for Dalarna Regional Digital Twin.

Never touches storage or network. Deterministic, unit-testable, dependency-based.
Formula: future = current * (1 + population_change)
Propagation: Population -> Traffic demand -> Infrastructure pressure
                         -> Land development -> Forest reduction
                         -> Water exposure
Scenario inputs can also change the five sector sources independently.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

MODEL_PATH = Path(__file__).resolve().parent.parent / "config" / "simulation_model.json"


class ScenarioInputError(ValueError):
    """A scenario combination produces an invalid modeled quantity."""


def load_model_config() -> dict[str, Any]:
    if MODEL_PATH.exists():
        try:
            return json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "model_version": "1.4.0",
        "default_thresholds": {"low": 0.30, "medium": 0.60, "high": 0.80, "critical": 1.00},
    }


def classify_risk(score: float, thresholds: Optional[dict[str, float]] = None) -> str:
    """Classify 0..1 score according to configurable thresholds."""
    th = thresholds or {"low": 0.30, "medium": 0.60, "high": 0.80, "critical": 1.00}
    t_low = float(th.get("low", 0.30))
    t_med = float(th.get("medium", 0.60))
    t_high = float(th.get("high", 0.80))

    if score < t_low:
        return "Low"
    elif score < t_med:
        return "Medium"
    elif score < t_high:
        return "High"
    else:
        return "Critical"


def run_simulation(
    scenario: str = "population_growth",
    target_year: int = 2030,
    scope: str = "dalarna",
    driver: Optional[dict[str, Any]] = None,
    excluded_sources: Optional[list[str]] = None,
    thresholds: Optional[dict[str, float]] = None,
    weights: Optional[dict[str, float]] = None,
    is_test: bool = False,
) -> dict[str, Any]:
    """Execute dependency-based simulation scenario across Dalarna and its 15 municipalities."""
    cfg = load_model_config()
    model_version = cfg.get("model_version", "1.4.0")
    excluded = set(excluded_sources or [])
    th = thresholds or cfg.get("default_thresholds", {"low": 0.30, "medium": 0.60, "high": 0.80, "critical": 1.00})
    w = weights or {"infrastructure": 0.30, "food": 0.25, "snow_flood": 0.25, "environment": 0.20}
    w_infra = float(w.get("infrastructure", 0.30))
    w_food = float(w.get("food", 0.25))
    w_flood = float(w.get("snow_flood", 0.25))
    w_env = float(w.get("environment", 0.20))
    sum_w = w_infra + w_food + w_flood + w_env
    total_w = sum_w if sum_w > 0 else 1.0

    driver_data = dict(driver or {})
    default_population_change = 0.10 if scenario == "population_growth" else 0.0
    pop_change = float(driver_data.get("population_change", default_population_change))
    if "scb" in excluded:
        pop_change = 0.0

    dep_cfg = cfg.get("dependencies", {}).get(scenario, {})
    coeffs = dep_cfg.get("coefficients", {
        "traffic_demand": 1.2,
        "infrastructure_pressure": 0.85,
        "land_development": 1.0,
        "forest_reduction": -0.20,
        "water_exposure": 0.60,
        "road_temperature_risk_per_c": 0.04,
        "freeze_cycle_risk_per_cycle": 0.003,
        "land_development_risk": 0.10,
        "precipitation_flood_risk": 0.20,
        "temperature_flood_risk_per_c": 0.01,
        "temperature_environment_risk_per_c": 0.01,
    })

    # 1. Cross-Source Multipliers
    road_temp_delta = float(driver_data.get("road_temp_delta", 0.0)) if "trafikverket" not in excluded else 0.0
    freeze_cycles = float(driver_data.get("freeze_cycles", 0.0)) if "trafikverket" not in excluded else 0.0
    land_change = float(driver_data.get("land_development_change", 0.0)) if "lantmateriet" not in excluded else 0.0
    forest_harvest_change = float(driver_data.get("forest_harvest_change", 0.0)) if "skogsstyrelsen" not in excluded else 0.0
    water_stress_change = float(driver_data.get("water_stress_change", 0.0)) if "sgu" not in excluded else 0.0
    temperature_change = float(driver_data.get("temperature_change", 0.0)) if "smhi" not in excluded else 0.0
    precipitation_change = float(driver_data.get("precipitation_change_pct", 0.0)) if "smhi" not in excluded else 0.0

    traffic_pct = pop_change * coeffs.get("traffic_demand", 1.2) if "trafikverket" not in excluded else 0.0
    land_pct = (
        pop_change * coeffs.get("land_development", 1.0) + land_change
        if "lantmateriet" not in excluded else 0.0
    )
    forest_pct = (
        pop_change * coeffs.get("forest_reduction", -0.20) - forest_harvest_change
        if "skogsstyrelsen" not in excluded else 0.0
    )
    water_pct = (
        pop_change * coeffs.get("water_exposure", 0.60) + water_stress_change
        if "sgu" not in excluded else 0.0
    )
    if forest_pct < -1:
        raise ScenarioInputError("The population and forest inputs produce a negative modeled forest area. Reduce the forest harvest or population change.")
    if water_pct < -1:
        raise ScenarioInputError("The population and water inputs produce a negative modeled water count. Adjust the water stress or population change.")

    # 2. Municipalities Calculation
    mun_baselines = cfg.get("municipalities_baseline", {})
    mun_results = []
    total_current_pop = 0
    total_sim_pop = 0

    for code, m in mun_baselines.items():
        base_pop = int(m.get("base_pop", 10000))
        sim_pop = int(round(base_pop * (1.0 + pop_change)))
        total_current_pop += base_pop
        total_sim_pop += sim_pop

        # Specific category risk scores (0..1)
        base_infra = float(m.get("base_infra_risk", 0.50))
        infra_score = min(1.0, max(0.0, base_infra
            + (traffic_pct * coeffs.get("infrastructure_pressure", 0.85))
            + (abs(road_temp_delta) * coeffs.get("road_temperature_risk_per_c", 0.04))
            + (freeze_cycles * coeffs.get("freeze_cycle_risk_per_cycle", 0.003))
            + (max(0.0, land_pct) * coeffs.get("land_development_risk", 0.10))))
        
        base_food = float(m.get("base_food_risk", 0.40))
        food_score = min(1.0, max(0.0, base_food + (pop_change * 0.30)))

        base_flood = float(m.get("base_flood_risk", 0.45))
        flood_score = min(1.0, max(0.0, base_flood
            + (water_pct * 0.20)
            + (precipitation_change * coeffs.get("precipitation_flood_risk", 0.20))
            + (temperature_change * coeffs.get("temperature_flood_risk_per_c", 0.01))))

        base_env = float(m.get("base_env_risk", 0.35))
        env_score = min(1.0, max(0.0, base_env
            + abs(forest_pct) * 0.40
            + max(0.0, land_pct) * 0.10
            + (temperature_change * coeffs.get("temperature_environment_risk_per_c", 0.01))))

        # Overall risk: dynamically weighted combination based on control configuration
        overall_score = round((w_infra * infra_score + w_food * food_score + w_flood * flood_score + w_env * env_score) / total_w, 3)

        mun_results.append({
            "code": code,
            "name": m.get("name"),
            "current_population": base_pop,
            "simulated_population": sim_pop,
            "population_change_pct": round(pop_change * 100, 1),
            "food_risk": {"score": round(food_score, 2), "level": classify_risk(round(food_score, 2), th)},
            "snow_flood_risk": {"score": round(flood_score, 2), "level": classify_risk(round(flood_score, 2), th)},
            "infrastructure_risk": {"score": round(infra_score, 2), "level": classify_risk(round(infra_score, 2), th)},
            "environment_risk": {"score": round(env_score, 2), "level": classify_risk(round(env_score, 2), th)},
            "overall_risk": {"score": overall_score, "level": classify_risk(overall_score, th)},
        })

    # Sort so top municipalities Borlänge, Falun, Mora appear first
    priority_order = ["2081", "2080", "2062", "2085", "2084"]
    mun_results.sort(key=lambda x: priority_order.index(x["code"]) if x["code"] in priority_order else 99)
    scoped_results = [m for m in mun_results if scope == "dalarna" or m["code"] == scope]
    if not scoped_results:
        raise ScenarioInputError(f"No modeled municipality is available for scope '{scope}'.")
    total_current_pop = sum(m["current_population"] for m in scoped_results)
    total_sim_pop = sum(m["simulated_population"] for m in scoped_results)

    # 3. Regional Totals & Cross-Sector Impacts
    pop_pct_str = f"+{int(round(pop_change * 100))}%" if pop_change >= 0 else f"{int(round(pop_change * 100))}%"
    traffic_pct_str = f"+{int(round(traffic_pct * 100))}%" if traffic_pct >= 0 else f"{int(round(traffic_pct * 100))}%"
    land_pct_str = f"+{int(round(land_pct * 100))}%" if land_pct >= 0 else f"{int(round(land_pct * 100))}%"
    forest_pct_str = f"{int(round(forest_pct * 100))}%"
    water_pct_str = f"+{int(round(water_pct * 100))}%" if water_pct >= 0 else f"{int(round(water_pct * 100))}%"

    impact_by_source = [
        {
            "id": "scb",
            "name": "Population (SCB)",
            "role": "Driver",
            "current": f"{total_current_pop:,}",
            "simulated": f"{total_sim_pop:,}",
            "change": pop_pct_str,
            "impact": "Driver",
            "status": "included" if "scb" not in excluded else "excluded",
        },
        {
            "id": "trafikverket",
            "name": "Traffic (Trafikverket)",
            "role": "Infrastructure Demand",
            "current": "100 (index)",
            "simulated": f"{int(round(100 * (1 + traffic_pct)))}",
            "change": f"{traffic_pct_str}; road temp {road_temp_delta:+.1f} °C",
            "impact": "High" if traffic_pct >= 0.10 else "Medium",
            "status": "included" if "trafikverket" not in excluded else "excluded",
            "drivers": {
                "traffic_demand_change": traffic_pct,
                "road_temperature_delta_c": road_temp_delta,
                "freeze_thaw_cycles": freeze_cycles,
            },
        },
        {
            "id": "lantmateriet",
            "name": "Land parcels (Lantmäteriet)",
            "role": "Development Pressure",
            "current": "12,450",
            "simulated": f"{int(round(12450 * (1 + land_pct))):,}",
            "change": land_pct_str,
            "impact": "High" if land_pct >= 0.10 else "Medium",
            "status": "included" if "lantmateriet" not in excluded else "excluded",
            "drivers": {"land_development_change": land_change, "total_change": land_pct},
        },
        {
            "id": "skogsstyrelsen",
            "name": "Forest area (Skogsstyrelsen)",
            "role": "Environmental Balance",
            "current": "450,000 ha",
            "simulated": f"{int(round(450000 * (1 + forest_pct))):,} ha",
            "change": forest_pct_str,
            "impact": "Medium",
            "status": "included" if "skogsstyrelsen" not in excluded else "excluded",
            "drivers": {"harvest_intensity_change": forest_harvest_change, "forest_area_change": forest_pct},
        },
        {
            "id": "sgu",
            "name": "Water exposure (SGU)",
            "role": "Hydrogeological Exposure",
            "current": "293",
            "simulated": f"{int(round(293 * (1 + water_pct)))}",
            "change": water_pct_str,
            "impact": "Medium",
            "status": "included" if "sgu" not in excluded else "excluded",
            "drivers": {"water_stress_change": water_stress_change, "total_change": water_pct},
        },
        {
            "id": "smhi",
            "name": "Weather (SMHI)",
            "role": "External Condition",
            "current": "baseline",
            "simulated": f"{temperature_change:+.1f} °C anomaly",
            "change": f"{temperature_change:+.1f} °C; precipitation {precipitation_change * 100:+.0f}%",
            "impact": "External" if temperature_change == 0 and precipitation_change == 0 else "Changed",
            "status": (
                "excluded" if "smhi" in excluded else
                "external" if temperature_change == 0 and precipitation_change == 0 else
                "included"
            ),
            "drivers": {"temperature_change_c": temperature_change, "precipitation_change_pct": precipitation_change},
        },
    ]

    # Overall regional score average
    avg_overall_score = round(sum(m["overall_risk"]["score"] for m in scoped_results) / len(scoped_results), 3)
    regional_risk_level = classify_risk(avg_overall_score, th)

    run_id = f"sim-{uuid.uuid4().hex[:8]}"

    if scoped_results:
        baseline = sum(m["current_population"] for m in scoped_results)
        simulated = sum(m["simulated_population"] for m in scoped_results)
        change = simulated - baseline
        percent = f"; {change / baseline * 100:+.2f}%" if baseline else ""
        key_takeaway = (
            f"The model's illustrative population baseline of {baseline:,} changes to "
            f"{simulated:,} in the {target_year} scenario ({change:+,} people{percent}). "
            f"{'Population source excluded. ' if 'scb' in excluded else ''}"
            "This is a scenario calculation, not an official forecast."
        )
    else:
        key_takeaway = "No modeled population is available for the selected scope."

    return {
        "run_id": run_id,
        "is_test": is_test,
        "simulated": True,
        "scenario": scenario,
        "target_year": target_year,
        "scope": scope,
        "model_version": model_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "population_current": total_current_pop,
            "population_simulated": total_sim_pop,
            "population_change_pct": round(pop_change * 100, 1),
            "regional_risk": {
                "score": avg_overall_score,
                "level": regional_risk_level,
            },
            "category_risks": {
                key: classify_risk(
                    round(sum(m[key]["score"] for m in scoped_results) / len(scoped_results), 3), th
                )
                for key in ("food_risk", "snow_flood_risk", "infrastructure_risk", "environment_risk")
            },
            "key_takeaway": key_takeaway,
        },
        "impact_by_source": impact_by_source,
        "municipalities": mun_results,
        "assumptions": {
            "formula": "population future = base * (1 + population change); sector change = population propagation + direct source input",
            "driver": driver_data,
            "effective_driver": {
                "population_change": pop_change,
                "road_temp_delta": road_temp_delta,
                "freeze_cycles": freeze_cycles,
                "land_development_change": land_change,
                "forest_harvest_change": forest_harvest_change,
                "water_stress_change": water_stress_change,
                "temperature_change": temperature_change,
                "precipitation_change_pct": precipitation_change,
            },
            "coefficients": coeffs,
            "thresholds": th,
            "note": "All six source inputs are scenario estimates. Zero database overwriting.",
        },
    }


_LATEST_SIMULATION_RESULT: Optional[dict[str, Any]] = None


def get_latest_simulation() -> Optional[dict[str, Any]]:
    global _LATEST_SIMULATION_RESULT
    if _LATEST_SIMULATION_RESULT is None:
        _LATEST_SIMULATION_RESULT = run_simulation()
    return _LATEST_SIMULATION_RESULT


def set_latest_simulation(result: dict[str, Any]) -> None:
    global _LATEST_SIMULATION_RESULT
    _LATEST_SIMULATION_RESULT = result
