"""Unit and integration tests for Causal Simulation Engine and endpoints."""
import pytest
from simulation.engine import classify_risk, run_simulation


def test_classify_risk_thresholds():
    th = {"low": 0.30, "medium": 0.60, "high": 0.80, "critical": 1.00}
    assert classify_risk(0.15, th) == "Low"
    assert classify_risk(0.35, th) == "Medium"
    assert classify_risk(0.75, th) == "High"
    assert classify_risk(0.95, th) == "Critical"


def test_simulation_engine_formula_and_propagation():
    res = run_simulation(
        scenario="population_growth",
        target_year=2030,
        scope="dalarna",
        driver={"population_change": 0.10},
        is_test=True,
    )
    assert res["is_test"] is True
    assert res["simulated"] is True
    assert res["target_year"] == 2030
    assert len(res["municipalities"]) == 15

    # Check causal formula P_future = P_current * (1 + 0.10)
    for m in res["municipalities"]:
        expected_pop = int(round(m["current_population"] * 1.10))
        assert m["simulated_population"] == expected_pop
        assert "overall_risk" in m
        assert m["overall_risk"]["score"] >= 0.0

    takeaway = res["summary"]["key_takeaway"]
    baseline = res["summary"]["population_current"]
    simulated = res["summary"]["population_simulated"]
    assert f"{baseline:,}" in takeaway and f"{simulated:,}" in takeaway
    assert f"{simulated - baseline:+,} people" in takeaway
    assert "illustrative" in takeaway and "not an official forecast" in takeaway


@pytest.mark.parametrize(
    ("change", "excluded"),
    [(0.30, []), (-0.075, []), (0.0, []), (0.30, ["scb"])],
)
def test_key_takeaway_uses_scoped_calculated_population(change, excluded):
    result = run_simulation(
        scenario="what_if", scope="2081",
        driver={"population_change": change}, excluded_sources=excluded,
    )
    borlange = next(m for m in result["municipalities"] if m["code"] == "2081")
    current, simulated = borlange["current_population"], borlange["simulated_population"]
    takeaway = result["summary"]["key_takeaway"]
    assert f"{current:,}" in takeaway and f"{simulated:,}" in takeaway
    assert f"{simulated - current:+,} people" in takeaway
    assert ("Population source excluded" in takeaway) == bool(excluded)


def test_key_takeaway_reports_unavailable_scope():
    from simulation.engine import ScenarioInputError

    with pytest.raises(ScenarioInputError, match="scope"):
        run_simulation(scope="unknown")


def test_combined_scenario_applies_all_six_source_inputs():
    population_driver = {"population_change": 0.10}
    result = run_simulation(scenario="population_growth", target_year=2030, driver={
        **population_driver,
        "road_temp_delta": 2.0,
        "freeze_cycles": 14,
        "land_development_change": 0.15,
        "forest_harvest_change": 0.20,
        "water_stress_change": -0.15,
        "temperature_change": 2.0,
        "precipitation_change_pct": 0.25,
    }, is_test=True)
    baseline = run_simulation(driver=population_driver)
    impacts = {impact["id"]: impact for impact in result["impact_by_source"]}
    def municipality(run):
        return next(m for m in run["municipalities"] if m["code"] == "2081")

    base_municipality = municipality(baseline)
    scenarios = {
        "road": run_simulation(driver={**population_driver, "road_temp_delta": 2.0}),
        "freeze_thaw": run_simulation(driver={**population_driver, "freeze_cycles": 14}),
        "land": run_simulation(driver={**population_driver, "land_development_change": 0.15}),
        "forest": run_simulation(driver={**population_driver, "forest_harvest_change": 0.20}),
        "water": run_simulation(driver={**population_driver, "water_stress_change": -0.15}),
        "temperature": run_simulation(driver={**population_driver, "temperature_change": 2.0}),
        "precipitation": run_simulation(driver={**population_driver, "precipitation_change_pct": 0.25}),
    }

    assert len(impacts) == 6
    assert impacts["trafikverket"]["drivers"]["road_temperature_delta_c"] == 2.0
    assert impacts["trafikverket"]["drivers"]["freeze_thaw_cycles"] == 14
    assert impacts["lantmateriet"]["drivers"]["total_change"] == pytest.approx(0.25)
    assert impacts["skogsstyrelsen"]["drivers"]["harvest_intensity_change"] == 0.20
    assert impacts["sgu"]["drivers"]["water_stress_change"] == -0.15
    assert impacts["smhi"]["status"] == "included"
    assert impacts["smhi"]["drivers"] == {
        "temperature_change_c": 2.0,
        "precipitation_change_pct": 0.25,
    }
    assert result["assumptions"]["effective_driver"]["population_change"] == 0.10
    assert municipality(scenarios["road"])["infrastructure_risk"]["score"] > base_municipality["infrastructure_risk"]["score"]
    assert municipality(scenarios["freeze_thaw"])["infrastructure_risk"]["score"] > base_municipality["infrastructure_risk"]["score"]
    assert municipality(scenarios["land"])["infrastructure_risk"]["score"] > base_municipality["infrastructure_risk"]["score"]
    assert municipality(scenarios["forest"])["environment_risk"]["score"] > base_municipality["environment_risk"]["score"]
    assert municipality(scenarios["water"])["snow_flood_risk"]["score"] < base_municipality["snow_flood_risk"]["score"]
    assert municipality(scenarios["temperature"])["snow_flood_risk"]["score"] > base_municipality["snow_flood_risk"]["score"]
    assert municipality(scenarios["precipitation"])["snow_flood_risk"]["score"] > base_municipality["snow_flood_risk"]["score"]


def test_combined_scenario_rejects_out_of_range_weather_input():
    from app.routers.simulation import RunSimulationRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RunSimulationRequest(driver={"temperature_change": 16})
    with pytest.raises(ValidationError):
        RunSimulationRequest(initial_driver={"temperature_change": 16})


def test_changed_input_baseline_is_saved_with_run(client, admin):
    initial = {"population_change": 0.1, "temperature_change": 2}
    response = admin.post(
        "/api/simulation/run",
        json={
            "scenario": "what_if",
            "driver": {"population_change": 0.2, "temperature_change": 0},
            "excluded_sources": ["sgu"],
            "initial_driver": initial,
            "initial_excluded_sources": [],
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["assumptions"]["initial_driver"] == initial
    assert result["assumptions"]["initial_excluded_sources"] == []
    saved = client.get(f'/api/simulation/result/{result["run_id"]}').json()
    assert saved["assumptions"]["initial_driver"] == initial
    assert saved["assumptions"]["initial_excluded_sources"] == []


def test_simulation_api_endpoints(client, admin):
    # 1. Scenarios list
    r = client.get("/api/simulation/scenarios")
    res = r.json()
    scenarios = res.get("scenarios", res) if isinstance(res, dict) else res
    assert len(scenarios) >= 3
    assert any(s["id"] == "population_growth" for s in scenarios)
    scenario_ids = {scenario["id"] for scenario in scenarios}
    builtin_ids = {
        "combined_baseline", "population_growth", "rapid_urbanization", "climate_freeze_thaw",
        "summer_drought", "heavy_precipitation", "forest_harvest_increase",
        "moderate_regional_growth", "urban_growth_transport", "warmer_wetter_winter",
        "forest_water_recovery", "what_if",
    }
    assert builtin_ids <= scenario_ids
    assert sum(scenario["id"] == "what_if" for scenario in scenarios) == 1
    driver_keys = {
        "population_change", "road_temp_delta", "freeze_cycles", "land_development_change",
        "forest_harvest_change", "water_stress_change", "temperature_change",
        "precipitation_change_pct",
    }
    for scenario in scenarios:
        if scenario["id"] in builtin_ids:
            assert driver_keys <= scenario["driver"].keys()
            assert set(scenario["affected_sources"]) == {
                "scb", "trafikverket", "lantmateriet", "skogsstyrelsen", "sgu", "smhi",
            }

    # 2. Test simulation (safe run)
    r = admin.post(
        "/api/simulation/test",
        json={"scenario": "population_growth", "target_year": 2035, "driver": {"population_change": 0.15}},
    )
    assert r.status_code == 200
    test_run = r.json()
    assert test_run["is_test"] is True
    assert test_run["target_year"] == 2035

    # 3. Official simulation run
    r = admin.post(
        "/api/simulation/run",
        json={"scenario": "rapid_urbanization", "target_year": 2035, "driver": {"population_change": 0.20}},
    )
    assert r.status_code == 200
    official_run = r.json()
    assert official_run["is_test"] is False
    assert official_run["target_year"] == 2035
    assert official_run["assumptions"]["driver"]["population_change"] == 0.18
    assert official_run["assumptions"]["driver"]["land_development_change"] == 0.15

    # 4. Latest result retrieval
    r = client.get("/api/simulation/result/latest")
    assert r.status_code == 200
    latest = r.json()
    assert latest["run_id"] == official_run["run_id"]


def test_what_if_scenario_name_is_returned(client, admin):
    response = admin.post(
        "/api/simulation/run",
        json={
            "scenario": "what_if",
            "scenario_name": "Dry summer alternative",
            "target_year": 2035,
            "driver": {"population_change": 0.02, "water_stress_change": 0.35},
        },
    )
    assert response.status_code == 200
    assert response.json()["scenario_name"] == "Dry summer alternative"
    history = client.get("/api/simulation/history?limit=1").json()
    assert history["runs"][0]["scenario_name"] == "Dry summer alternative"
