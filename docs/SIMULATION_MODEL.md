# Simulation model

Describes `simulation/engine.py` and `config/simulation_model.json` (model version 1.4.0). The engine is pure Python: it never reads or writes the database, so a simulation cannot overwrite live data. Results are **scenarios (what-if estimates), not forecasts from any agency**, and the UI labels every simulated value `SIMULATED`.

## 1. Dependency chain

```
SCB population -> Trafikverket traffic and road-temperature stress -> infrastructure risk
               -> Lantmateriet land development ---------------------> infrastructure/environment risk
               -> Skogsstyrelsen harvest / forest-area change --------> environment risk
               -> SGU groundwater / water-stress change -------------> snow/flood risk
               -> SMHI temperature / precipitation change ------------> snow/flood/environment risk
                                                                       -> overall regional risk
```

The Simulation page provides fixed, fully specified source assumptions for each preset. Population changes propagate through the dependency chain, while each other source also has a direct scenario input. Only the single `WHAT IF` option enables source-input editing and run naming. Preset drivers are resolved by the API, not accepted from editable client inputs. A source switched off in the request (`excluded_sources`) contributes nothing and is shown as "excluded"; its stored data is untouched. Coefficients are model assumptions and should not be read as agency forecasts.

Population percentage is a **total change by the selected target year**, not an annual rate. Changing the year alone does not change the calculation. Sources disabled in the administrator's saved configuration are also excluded automatically. A run is rejected with a validation message if its combined population/forest or population/water inputs would imply a negative modeled forest area or water count.

Freeze-thaw cycles and precipitation change are not shown in the scenario panel. Existing preset values still contribute to calculations, and saved WHAT IF runs retain their recorded values.

Built-in scenarios cover a combined baseline, population growth, rapid urbanization, winter freeze-thaw, summer drought, heavy precipitation, increased forest harvest, moderate regional growth, urban growth and transport pressure, warmer/wetter winters, and forest/water recovery. Each preset has a fixed value for SCB population, Trafikverket roads, Lantmäteriet land use, Skogsstyrelsen forest, SGU water stress, and SMHI weather. These values are illustrative planning assumptions, not official forecasts. The separate `WHAT IF` option lets users name a custom run and edit the source assumptions.

The Key Takeaway lists only source inputs and dependency switches manually changed since the scenario was selected, including edits to zero. It reports the risk category selected above the map for the run's scope: the municipality score for a municipal run, or the unweighted mean of municipality scores for Dalarna. The map colours represent individual municipality scores. Other scenario inputs still contribute to the result, even when they are not listed. The initial input and dependency values are stored with new runs so the explanation survives reloads; older runs without this information are marked as unavailable rather than guessed.

When population was manually changed and included, the takeaway also shows modeled population before and after the scenario. The model baseline is a configuration assumption, not an SCB observation. Model results and risk scores remain unvalidated.

## 2. Coefficients (config/simulation_model.json, scenario `population_growth`)

| Key | Default | Meaning |
|---|---|---|
| `traffic_demand` | 1.2 | traffic change = population change × 1.2 (Trafikverket) |
| `infrastructure_pressure` | 0.85 | infrastructure score += traffic change × 0.85 |
| `land_development` | 1.0 | land change = population change × 1.0 (Lantmäteriet) |
| `forest_reduction` | -0.20 | forest change = population change × -0.20 (Skogsstyrelsen) |
| `water_exposure` | 0.60 | water change = population change × 0.60 (SGU) |
| `road_temperature_risk_per_c` | 0.04 | infrastructure-risk change per °C of road-temperature change |
| `freeze_cycle_risk_per_cycle` | 0.003 | infrastructure-risk change per freeze-thaw cycle |
| `land_development_risk` | 0.10 | additional infrastructure-risk change per unit of land-development change |
| `precipitation_flood_risk` | 0.20 | snow/flood-risk change per unit of precipitation change |
| `temperature_flood_risk_per_c` | 0.01 | snow/flood-risk change per °C of temperature change |
| `temperature_environment_risk_per_c` | 0.01 | environment-risk change per °C of temperature change |

## 3. Per-municipality formulas (15 municipalities from `municipalities_baseline`)

Let `p` = population change (e.g. 0.10).

- Population: `simulated = round(base_pop × (1 + p))`
- Traffic change = `p × 1.2`; the user-entered road-temperature delta is separately applied to infrastructure risk.
- Freeze-thaw cycles directly add to infrastructure risk using the configured per-cycle coefficient.
- Land change = `p × 1.0 + land_development_change`.
- Forest-area change = `p × -0.20 - forest_harvest_change`.
- Water change = `p × 0.60 + water_stress_change`.
- SMHI temperature and precipitation inputs are applied directly to snow/flood and environment risk.
- Infrastructure score = `clamp(base_infra + traffic_change × 0.85 + |road_temp_delta| × 0.04 + max(land_change, 0) × 0.10, 0, 1)`
- Food score = `clamp(base_food + p × 0.30, 0, 1)`
- Snow/Flood score = `clamp(base_flood + water_change × 0.20 + precipitation_change × 0.20 + temperature_change × 0.01, 0, 1)`
- Environment score = `clamp(base_env + |forest_change| × 0.40 + max(land_change, 0) × 0.10 + temperature_change × 0.01, 0, 1)`
- Overall = the weighted mean of infrastructure, food, flood and environment scores using saved category weights. The configured population weight is not used in that mean.
- Overall score = mean of the selected municipality scores: one score for a municipality or the mean of all 15 for Dalarna.

Levels come from the thresholds (default LOW < 0.30 ≤ MEDIUM < 0.60 ≤ HIGH < 0.80 ≤ CRITICAL). The Main Dashboard re-classifies from the scores with the live saved thresholds, so labels follow the Control Room settings.
Municipal summary population and category scores use the selected scope. Non-population impact-by-source quantities remain illustrative regional indices, even when a municipality is selected.

## 4. Request and result

`POST /api/simulation/run` (also `/api/simulation/test`, admin only, which never replaces the official latest result):

```json
{"scenario": "population_growth", "target_year": 2030, "scope": "dalarna",
 "driver": {"population_change": 0.10, "road_temp_delta": 2.0,
   "land_development_change": 0.15, "forest_harvest_change": 0.20,
   "water_stress_change": -0.15, "temperature_change": 2.0,
   "precipitation_change_pct": 0.0}, "excluded_sources": []}
```

Percentage driver values are decimal ratios (`0.15` = `+15%`). Temperature inputs are degrees Celsius. The result contains `impact_by_source` rows for all six sources, with SMHI marked `external` only when its scenario inputs are unchanged, plus municipality risk results and requested/effective driver values in `assumptions`.

## 5. Known deviations from the spec (documented as xfail tests in tests/test_spec_gaps.py)

1. **Baselines are literals** in the JSON (municipality population and base risk scores) and several "current" values in `impact_by_source` are not read from stored source data.
2. **Results are cached in process memory as well as persisted** in `simulation_runs`; cache behavior across multiple workers should be verified.
3. Saved scenarios created by administrators are merged into the built-in scenario list; the user-facing `WHAT IF` option is the only built-in custom-input option.
