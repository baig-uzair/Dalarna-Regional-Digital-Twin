"""Focused simulation checks that do not access the shared database."""
from itertools import combinations
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from app.routers import simulation
from simulation.engine import ScenarioInputError, classify_risk, run_simulation


class SimulationRegressionTests(TestCase):
    def test_scoped_summary_and_total_change_by_year(self):
        runs = [run_simulation(scenario="what_if", scope="2081", target_year=year,
                               driver={"population_change": 0.1}) for year in (2026, 2040)]
        for result in runs:
            municipality = next(m for m in result["municipalities"] if m["code"] == "2081")
            self.assertEqual(result["summary"]["population_current"], municipality["current_population"])
            self.assertEqual(result["summary"]["population_simulated"], municipality["simulated_population"])
            self.assertEqual(result["summary"]["regional_risk"]["score"], municipality["overall_risk"]["score"])
            self.assertEqual(result["summary"]["category_risks"]["food_risk"], municipality["food_risk"]["level"])
            population_impact = next(i for i in result["impact_by_source"] if i["id"] == "scb")
            self.assertEqual(int(population_impact["current"].replace(",", "")), municipality["current_population"])
        self.assertEqual(runs[0]["summary"]["population_simulated"], runs[1]["summary"]["population_simulated"])
        self.assertEqual(len(runs[0]["municipalities"]), 15)

    def test_negative_quantities_rejected_and_zero_allowed(self):
        for driver, message in (
            ({"forest_harvest_change": 2}, "forest area"),
            ({"population_change": -0.2, "water_stress_change": -1}, "water count"),
        ):
            with self.subTest(driver=driver), self.assertRaisesRegex(ScenarioInputError, message):
                run_simulation(scenario="what_if", driver=driver)
        result = run_simulation(scenario="what_if", driver={"forest_harvest_change": 1, "water_stress_change": -1})
        impacts = {item["id"]: item for item in result["impact_by_source"]}
        self.assertEqual(impacts["skogsstyrelsen"]["simulated"], "0 ha")
        self.assertEqual(impacts["sgu"]["simulated"], "0")
        run_simulation(scenario="what_if", driver={"forest_harvest_change": 2}, excluded_sources=["skogsstyrelsen"])

    def test_risk_level_matches_returned_rounded_score(self):
        thresholds = {"low": 0.42, "medium": 0.6, "high": 0.8}
        result = run_simulation(scenario="what_if", driver={"population_change": -0.0033333333}, thresholds=thresholds)
        food = next(m for m in result["municipalities"] if m["code"] == "2081")["food_risk"]
        self.assertEqual(food["score"], 0.42)
        self.assertEqual(food["level"], classify_risk(food["score"], thresholds))

    def test_regional_level_matches_displayed_average_at_threshold(self):
        thresholds = {"low": 0.457, "medium": 0.6, "high": 0.8}
        result = run_simulation(scenario="what_if", thresholds=thresholds)
        average = sum(m["snow_flood_risk"]["score"] for m in result["municipalities"]) / 15
        self.assertLess(average, thresholds["low"])
        self.assertEqual(round(average, 3), thresholds["low"])
        self.assertEqual(result["summary"]["category_risks"]["snow_flood_risk"], classify_risk(thresholds["low"], thresholds))

    def test_source_exclusions_and_saved_admin_disable(self):
        source_fields = {
            "scb": "population_change", "trafikverket": "road_temp_delta", "lantmateriet": "land_development_change",
            "skogsstyrelsen": "forest_harvest_change", "sgu": "water_stress_change", "smhi": "temperature_change",
        }
        driver = {"population_change": 0.1, "road_temp_delta": 2, "land_development_change": 0.15,
                  "forest_harvest_change": 0.2, "water_stress_change": -0.15, "temperature_change": 2}
        sources = list(source_fields)
        for count in range(len(sources) + 1):
            for excluded in combinations(sources, count):
                result = run_simulation(scenario="what_if", driver=driver, excluded_sources=list(excluded))
                for source, field in source_fields.items():
                    self.assertEqual(result["assumptions"]["effective_driver"][field], 0 if source in excluded else driver[field])

        body = simulation.RunSimulationRequest(scenario="what_if", driver=driver, initial_excluded_sources=[])
        live = {"sources": {source: source != "smhi" for source in sources}}
        with patch.object(simulation.control, "get_live", return_value=live), \
             patch.object(simulation, "_persist_simulation_run") as persist, \
             patch.object(simulation, "set_latest_simulation"), \
             patch.object(simulation, "_RESULTS_CACHE", {}), \
             patch.object(simulation, "_LATEST_OFFICIAL", None):
            result = simulation.execute_simulation(body, user=None)
            self.assertEqual(result["assumptions"]["effective_driver"]["temperature_change"], 0)
            self.assertIn("smhi", result["assumptions"]["initial_excluded_sources"])
            self.assertIn("smhi", persist.call_args.kwargs["input_data"]["excluded_sources"])

    def test_http_validation_error_does_not_save_a_result(self):
        body = simulation.RunSimulationRequest(scenario="what_if", driver={"forest_harvest_change": 2})
        with patch.object(simulation.control, "get_live", return_value={"sources": {}}), \
             patch.object(simulation, "_persist_simulation_run") as persist:
            with self.assertRaises(HTTPException) as exc:
                simulation.execute_simulation(body, user=None)
            self.assertEqual(exc.exception.status_code, 422)
            self.assertIn("forest area", exc.exception.detail)
            persist.assert_not_called()
