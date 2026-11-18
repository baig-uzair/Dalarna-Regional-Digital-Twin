"""Spec B interactions (B-4 ... B-18) against the real app in a real browser."""
import json
import re

from playwright.sync_api import expect

from .conftest import BASE

URL = f"{BASE}/?e2e=1"
STATE_KEYS = ("mode", "scope", "sources", "source_intervals", "layers", "components", "risk_thresholds", "active_scenario", "simulation_year")


def open_main(page, sim=False):
    page.goto(URL)
    page.wait_for_selector(".kpi-card")
    page.wait_for_function("window.__dt && window.__dt.loaded")
    if sim:
        page.click('[data-act="mode"][data-mode="simulation"]')
        page.wait_for_selector("#sim-control")
    return page


def run_sim(page):
    page.click("#btn-run-sim")
    page.wait_for_selector(".results-meta")


def open_municipalities(page):
    page.click('#nav a[data-route="municipalities"]')
    page.wait_for_selector(".muni-card")


def apply_state(api, **changes):
    state = api.get(f"{BASE}/api/control/state").json()
    body = {k: state[k] for k in STATE_KEYS}
    for key, value in changes.items():
        body[key] = {**body[key], **value} if isinstance(value, dict) else value
    r = api.post(f"{BASE}/api/control/apply", json={"base_version": state["version"], "state": body})
    assert r.status_code == 200, r.text


def test_cards_come_from_the_registry_not_hard_coded(page, api):
    open_main(page)
    summary = api.get(f"{BASE}/api/datasources/summary").json()
    names = [s["name"] for s in summary["sources"]]
    cards = page.locator(".kpi-card .kpi-name").all_inner_texts()
    assert cards == names and len(cards) == 6
    html = page.content()
    for fake in ("12,450", "~450,000 ha", "4,320 buildings"):
        assert fake not in html


def test_source_cards_are_spaced_from_dashboard_panels(page):
    open_main(page, sim=True)
    for width in (1600, 900):
        page.set_viewport_size({"width": width, "height": 900})
        cards = page.locator("#kpi-slot").bounding_box()
        panels = page.locator(".mid-row-grid").bounding_box()
        assert panels["y"] - (cards["y"] + cards["height"]) >= 14


def test_requested_logo_is_shown_in_navigation(page):
    open_main(page)
    logo = page.get_by_role("img", name="Region Dalarna")
    expect(logo).to_be_visible()
    assert logo.get_attribute("src").startswith("https://encrypted-tbn0.gstatic.com/images?")
    for width in (1600, 390):
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        expect(logo).to_be_visible()


def test_light_dashboard_keeps_overview_content(page):
    open_main(page, sim=True)
    page.set_viewport_size({"width": 1600, "height": 900})
    assert page.locator(".kpi-card").count() == 6
    expect(page.locator("#map-slot")).to_be_visible()
    expect(page.locator("#sim-control")).to_be_visible()
    expect(page.locator("#results-panel")).to_be_visible()
    expect(page.locator(".muni-card")).to_have_count(0)
    assert page.locator("body").evaluate("(el) => getComputedStyle(el).backgroundColor") == "rgb(245, 246, 247)"
    assert page.locator("html").evaluate("(el) => getComputedStyle(el).backgroundColor") == "rgb(245, 246, 247)"
    panels = [page.locator(selector).bounding_box() for selector in (".map-card", "#sim-control", "#results-panel")]
    assert max(panel["height"] for panel in panels) - min(panel["height"] for panel in panels) < 1


def test_results_fill_panel_and_remain_usable_on_mobile(page):
    page.set_viewport_size({"width": 1920, "height": 1000})
    open_main(page, sim=True)
    run_sim(page)
    panel = page.locator("#results-panel").bounding_box()
    bottom = page.locator(".res-split-bottom").bounding_box()
    assert 0 <= panel["y"] + panel["height"] - (bottom["y"] + bottom["height"]) <= 20
    assert page.locator(".impact-table tbody tr").count() == 6
    assert page.locator(".radial-gauge-svg").bounding_box()["width"] >= 200

    page.set_viewport_size({"width": 390, "height": 900})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    gauge = page.locator(".gauge-box").bounding_box()
    svg = page.locator(".radial-gauge-svg").bounding_box()
    assert svg["width"] >= 200 and svg["x"] >= gauge["x"] and svg["x"] + svg["width"] <= gauge["x"] + gauge["width"]
    expect(page.locator(".takeaway-box")).to_be_visible()
    expect(page.locator('.takeaway-box [data-act="methodology"]')).to_be_visible()


def test_compact_scenario_inputs_and_dependencies_on_mobile(page):
    open_main(page, sim=True)
    page.set_viewport_size({"width": 390, "height": 900})
    page.select_option("#sim-scenario", "what_if")
    page.fill("#driver-road_temp_delta-number", "-3.5")
    assert page.locator("#driver-road_temp_delta-number").evaluate("(el) => el.scrollWidth <= el.clientWidth")
    page.locator(".source-dependencies summary").click()
    toggle = page.locator('.sim-toggle-row:has-text("Trafikverket") input')
    before = toggle.is_checked()
    page.locator('.sim-toggle-row:has-text("Trafikverket") .toggle-switch-slider').click()
    assert toggle.is_checked() != before
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_run_button_is_at_top_of_scenario_panel(page):
    open_main(page, sim=True)
    button = page.locator("#btn-run-sim").bounding_box()
    scenario = page.locator("#sim-scenario").bounding_box()
    assert button["y"] + button["height"] < scenario["y"]


def test_online_badge_is_read_only(page, api):
    open_main(page)
    badge = page.locator(".kpi-card").first.locator(".status-pill")
    assert badge.evaluate("e => e.tagName") == "SPAN"
    before = api.get(f"{BASE}/api/control/state").json()["sources"]
    badge.click()  # opens the drill-down (card click) but must not toggle anything
    page.keyboard.press("Escape")
    assert api.get(f"{BASE}/api/control/state").json()["sources"] == before


def test_header_chips_show_health_and_timestamps(page):
    open_main(page)
    page.click('[data-act="sys-health"]')
    expect(page.locator("#popover")).to_contain_text("System health")
    page.click('[data-act="last-update"]')
    expect(page.locator("#popover")).to_contain_text("Last update per source")
    page.click('[data-act="scope-info"]')
    expect(page.locator("#popover")).to_contain_text("Scope: Dalarna")
    page.click('[data-act="user-menu"]')
    expect(page.locator("#popover")).to_contain_text("Not signed in")


def test_mode_toggle_is_a_session_view_and_does_not_change_saved_state(page, api):
    open_main(page)
    expect(page.locator(".banner.live")).to_be_visible()
    page.click('[data-act="mode"][data-mode="simulation"]')
    expect(page.locator(".banner.sim")).to_contain_text("SIMULATION MODE")
    assert api.get(f"{BASE}/api/control/state").json()["mode"] == "live"  # global default untouched
    page.click('[data-act="mode"][data-mode="live"]')
    expect(page.locator(".banner.live")).to_be_visible()
    assert page.locator("#results-panel .sim-tag").count() == 0  # no simulated values in LIVE


def test_every_nav_route_opens_its_own_page(page):
    open_main(page)
    expected = {"municipalities": ".muni-card", "data-sources": "text=Read-only overview", "reports": "text=Reports & exports", "settings": "text=Personal settings"}
    for route, marker in expected.items():
        page.click(f'#nav a[data-route="{route}"]')
        page.wait_for_selector(marker)
        assert page.evaluate("location.hash") == f"#/{route}"
        assert "Real-time telemetry" not in page.content()  # the old placeholder text is gone
    for route in ("map", "simulation", "insights"):
        assert page.locator(f'#nav a[data-route="{route}"]').count() == 0
    page.goto(f"{URL}#/insights")
    expect(page.locator("#map-slot")).to_be_visible()
    expect(page.locator(".muni-card")).to_have_count(0)


def test_source_card_drilldown(page):
    open_main(page)
    for name in ("SMHI", "Trafikverket", "SCB", "SGU", "Skogsstyrelsen", "Lantmäteriet"):
        page.locator(".kpi-card", has_text=name).first.click()
        expect(page.locator("#drawer")).to_contain_text(name)
        page.wait_for_selector("#src-body dl")
        page.keyboard.press("Escape")


def test_key_takeaway_uses_changed_inputs_and_selected_map_risk(page):
    open_main(page, sim=True)
    page.select_option("#sim-scenario", "what_if")
    page.fill("#pop-number", "30")
    page.fill("#driver-road_temp_delta-number", "-3.5")
    page.fill("#driver-land_development_change-number", "45")
    page.fill("#driver-forest_harvest_change-number", "50")
    page.fill("#driver-water_stress_change-number", "0")
    page.fill("#driver-temperature_change-number", "0")
    page.select_option("#sim-scope", "2081")
    page.locator(".source-dependencies summary").click()
    page.locator('.sim-toggle-row:has-text("Trafikverket") .toggle-switch-slider').click()
    with page.expect_response(lambda response: response.url.endswith("/api/simulation/run") and response.request.method == "POST") as response:
        page.click("#btn-run-sim")
    assert response.value.ok
    result = response.value.json()
    municipality = next(m for m in result["municipalities"] if m["code"] == "2081")
    assert result["summary"]["population_simulated"] == municipality["simulated_population"]
    assert result["summary"]["regional_risk"]["score"] == municipality["overall_risk"]["score"]
    results = page.locator("#results-panel")
    expect(results.locator(".m5-item", has_text="Population")).to_contain_text(
        re.compile(r"\s*".join(str(municipality["simulated_population"])))
    )
    expect(results.locator(".gauge-box")).to_contain_text(f'{municipality["overall_risk"]["score"]:.3f}')
    takeaway = page.locator("#results-panel .takeaway-box")
    expect(takeaway).to_contain_text("population growth rate at +30%")
    expect(takeaway).to_contain_text("temperature change at 0 °C")
    expect(takeaway).to_contain_text("road temperature change at -3.5 °C (source excluded; not applied)")
    expect(takeaway).to_contain_text("new development / land-use change at +45%")
    expect(takeaway).to_contain_text("forest harvest / change intensity at +50%")
    expect(takeaway).to_contain_text("groundwater / water-stress change at 0%")
    expect(takeaway).to_contain_text("Road (Trafikverket) source excluded")
    expect(takeaway).to_contain_text("overall risk")
    expect(takeaway).to_contain_text(f'{municipality["overall_risk"]["score"]:.3f} / 1')
    page.select_option("#map-risk-select", "food_risk")
    expect(results.locator(".gauge-title")).to_have_text("Food & supply risk – Borlänge")
    expect(results.locator(".gauge-box")).to_contain_text(f'{municipality["food_risk"]["score"]:.3f}')
    expect(takeaway).to_contain_text("food & supply risk")
    expect(takeaway).to_contain_text(f'{municipality["food_risk"]["score"]:.3f} / 1')
    expect(takeaway).not_to_contain_text("overall risk")
    for value in (municipality["current_population"], municipality["simulated_population"]):
        expect(takeaway).to_contain_text(re.compile(r"\s*".join(str(value))))
    expect(takeaway).to_contain_text("Borlänge")
    expect(takeaway).not_to_contain_text("Historical Dalarna County context")
    expect(takeaway).not_to_contain_text("Other scenario settings also contribute")
    page.reload()
    page.wait_for_function("window.__dt && window.__dt.loaded")
    expect(page.locator("#results-panel .takeaway-box")).to_contain_text("population growth rate at +30%")


def test_gauge_tracks_selected_regional_risk_and_handles_missing_scores(page):
    open_main(page, sim=True)
    run_sim(page)
    page.evaluate("""() => {
        const values = {overall_risk: 0, food_risk: 0.3, snow_flood_risk: 0.45,
                        infrastructure_risk: 0.6, environment_risk: 0.9};
        for (const municipality of window.__dt.result.municipalities)
            for (const [key, score] of Object.entries(values)) municipality[key].score = score;
    }""")
    categories = (
        ("overall_risk", "Overall risk", "0.000", "LOW"),
        ("food_risk", "Food & supply risk", "0.300", "MEDIUM"),
        ("snow_flood_risk", "Snow / Flood risk", "0.450", "MEDIUM"),
        ("infrastructure_risk", "Infrastructure risk", "0.600", "HIGH"),
        ("environment_risk", "Environment risk", "0.900", "CRITICAL"),
    )
    for key, label, score, level in categories:
        page.select_option("#map-risk-select", key)
        gauge = page.locator(".gauge-box")
        expect(gauge.locator(".gauge-title")).to_have_text(f"{label} – Dalarna")
        expect(gauge.locator(".gauge-label")).to_have_text(level)
        expect(gauge).to_contain_text(f"score {score}")
        expect(gauge.locator("svg line")).to_have_count(1)
        expect(page.locator(".takeaway-box")).to_contain_text(f"{score} / 1")
    page.click('[data-act="gauge"]')
    expect(page.locator("#drawer h2")).to_have_text("Environment risk – Dalarna")
    expect(page.locator("#drawer")).to_contain_text("0.900")
    expect(page.locator("#drawer")).to_contain_text("Contributing factors")
    page.keyboard.press("Escape")

    page.evaluate("window.__dt.result.municipalities[0].environment_risk.score = null")
    page.select_option("#map-risk-select", "environment_risk")
    expect(page.locator(".gauge-box")).to_contain_text("score —")
    expect(page.locator(".gauge-box svg line")).to_have_count(0)
    expect(page.locator(".takeaway-box")).to_contain_text("environment risk is unavailable")
    page.click('[data-act="gauge-label"]')
    expect(page.locator("#drawer h2")).to_have_text("Environment risk – Dalarna")
    expect(page.locator("#drawer")).to_contain_text("Score")
    expect(page.locator("#drawer")).to_contain_text("—")


def test_key_takeaway_handles_unchanged_and_legacy_runs(page):
    open_main(page, sim=True)
    page.select_option("#sim-scenario", "combined_baseline")
    with page.expect_response(lambda response: response.url.endswith("/api/simulation/run") and response.request.method == "POST") as response:
        page.click("#btn-run-sim")
    assert response.value.ok
    result = response.value.json()
    takeaway = page.locator("#results-panel .takeaway-box")
    expect(takeaway).to_contain_text("no manually changed inputs")
    expect(takeaway).not_to_contain_text("SCB table TAB638")
    expected = sum(m["snow_flood_risk"]["score"] for m in result["municipalities"]) / len(result["municipalities"])
    page.select_option("#map-risk-select", "snow_flood_risk")
    expect(takeaway).to_contain_text(f"{expected:.3f} / 1")
    expect(takeaway).to_contain_text("unweighted municipal average")
    page.evaluate("delete window.__dt.result.assumptions.initial_driver")
    page.select_option("#map-risk-select", "food_risk")
    expect(page.locator("#view .takeaway-box")).to_contain_text("Changed inputs are unavailable for this saved run")


def test_simulation_control_flow_pending_year_exclusion_and_run(page, api):
    open_main(page, sim=True)
    page.select_option("#sim-scenario", "what_if")
    for key in ("road_temp_delta", "land_development_change", "forest_harvest_change", "water_stress_change", "temperature_change"):
        expect(page.locator(f"#driver-{key}-number")).to_be_visible()
        expect(page.locator(f"#driver-{key}-number")).to_be_enabled()
    for key in ("freeze_cycles", "precipitation_change_pct"):
        expect(page.locator(f"#driver-{key}-number")).to_have_count(0)
        expect(page.locator(f"#driver-{key}-slider")).to_have_count(0)
    # slider <-> numeric input stay in sync
    page.fill("#pop-number", "15")
    assert page.input_value("#pop-slider") == "15"
    page.evaluate("const s = document.querySelector('#pop-slider'); s.value = 4; s.dispatchEvent(new Event('input', {bubbles: true}))")
    assert page.input_value("#pop-number") == "4"
    # invalid value: highlighted, message shown, input kept
    page.fill("#pop-number", "99")
    page.click("#btn-run-sim")
    expect(page.locator("#toasts")).to_contain_text("Please correct the highlighted value(s).")
    assert page.input_value("#pop-number") == "99"
    expect(page.locator(".num-wrap.invalid")).to_be_visible()
    page.fill("#pop-number", "10")
    page.fill("#driver-temperature_change-number", "2")
    page.click('[data-act="year"][data-year="2030"]')
    run_sim(page)
    expect(page.locator("#toasts")).to_contain_text("Simulation completed successfully. Result ID: sim-")
    expect(page.locator(".results-meta")).to_contain_text("2030")
    assert page.locator(".impact-table tbody tr").count() == 6
    expect(page.locator('.impact-table tr:has-text("Weather") .risk-badge')).to_have_text("Changed")
    result = api.get(f"{BASE}/api/simulation/result/latest").json()
    assert set(result["assumptions"]["driver"]) >= {
        "population_change", "road_temp_delta", "freeze_cycles", "land_development_change",
        "forest_harvest_change", "water_stress_change", "temperature_change",
        "precipitation_change_pct",
    }
    # changing the year makes the 2030 result pending: it must never be shown as 2035
    page.click('[data-act="year"][data-year="2035"]')
    expect(page.locator(".pending-card")).to_be_visible()
    assert page.locator(".results-meta").count() == 0 and page.locator(".impact-table").count() == 0
    # back to the same inputs: the result is valid again
    page.click('[data-act="year"][data-year="2030"]')
    expect(page.locator(".results-meta")).to_be_visible()
    # a slider/number change also marks the result pending
    page.fill("#pop-number", "12")
    expect(page.locator(".pending-card")).to_be_visible()
    # excluding a source marks its consequence 'excluded' but keeps the data available
    page.fill("#pop-number", "10")
    page.locator(".source-dependencies summary").click()
    page.locator('.sim-toggle-row:has-text("Trafikverket") .toggle-switch-slider').click()
    run_sim(page)
    expect(page.locator('.impact-table tr:has-text("Traffic")')).to_have_class(re.compile("excluded"))
    expect(page.locator('.impact-table tr:has-text("Traffic") .risk-badge')).to_have_text("Excluded")
    # Reset restores the inputs of the last result and deletes nothing
    page.fill("#pop-number", "3")
    page.click('[data-act="reset"]')
    assert page.input_value("#pop-number") == "10"


def test_what_if_inputs_and_preset_scenarios(page, api):
    open_main(page, sim=True)
    options = page.locator("#sim-scenario option").all_text_contents()
    assert any("Summer Drought" in option for option in options)
    assert any("Heavy Precipitation" in option for option in options)
    assert any("Moderate Regional Growth" in option for option in options)
    assert any("Urban Growth and Transport" in option for option in options)
    assert any("Warmer, Wetter Winter" in option for option in options)
    assert any("Forest and Water Recovery" in option for option in options)
    assert any(option.startswith("WHAT IF") for option in options)
    page.select_option("#sim-scenario", "combined_baseline")
    assert page.input_value("#pop-number") == "10"
    assert page.input_value("#driver-road_temp_delta-number") == "2.0"
    assert page.input_value("#driver-land_development_change-number") == "15"
    assert page.input_value("#driver-forest_harvest_change-number") == "20"
    assert page.input_value("#driver-water_stress_change-number") == "-15"
    assert page.input_value("#driver-temperature_change-number") == "2.0"
    expect(page.locator("#driver-freeze_cycles-number")).to_have_count(0)
    expect(page.locator("#driver-precipitation_change_pct-number")).to_have_count(0)
    expect(page.locator("#pop-number")).to_be_disabled()
    expect(page.locator("#driver-road_temp_delta-number")).to_be_disabled()
    for scenario_id, driver in (("climate_freeze_thaw", "freeze_cycles"), ("heavy_precipitation", "precipitation_change_pct")):
        page.select_option("#sim-scenario", scenario_id)
        values = page.evaluate(
            """([scenarioId, key]) => ({
              current: window.__dt.form.drivers[key],
              preset: window.__dt.scenarios.find(s => s.id === scenarioId).driver[key]
            })""",
            [scenario_id, driver],
        )
        assert values["current"] == values["preset"] and values["preset"] != 0
        expect(page.locator(f"#driver-{driver}-number")).to_have_count(0)
    page.select_option("#sim-scenario", "population_growth")
    expect(page.locator("#what-if-name")).to_have_count(0)
    expect(page.locator("#pop-number")).to_be_disabled()

    page.select_option("#sim-scenario", "what_if")
    expect(page.locator("#what-if-name")).to_have_count(0)
    expect(page.locator("#sim-control")).not_to_contain_text("Enter a name")
    expect(page.locator("#pop-number")).to_be_enabled()
    expect(page.locator("#driver-road_temp_delta-number")).to_be_enabled()
    page.fill("#driver-water_stress_change-number", "45")
    run_sim(page)
    result = api.get(f"{BASE}/api/simulation/result/latest").json()
    assert result["scenario_name"] == "WHAT IF Scenario"
    assert result["assumptions"]["driver"]["water_stress_change"] == 0.45

    page.select_option("#sim-scenario", "population_growth")
    expect(page.locator("#what-if-name")).to_have_count(0)
    expect(page.locator("#pop-number")).to_be_disabled()


def test_run_disables_button_and_shows_running_message(page):
    open_main(page, sim=True)

    def slow(route):
        page.wait_for_timeout(900)
        route.continue_()

    page.route("**/api/simulation/run", slow)
    page.click("#btn-run-sim")
    expect(page.locator("#btn-run-sim")).to_be_disabled()
    expect(page.locator("#run-status")).to_have_text("Running simulation... Please wait.")
    page.wait_for_selector(".results-meta")
    expect(page.locator("#btn-run-sim")).to_be_enabled()


def test_failed_run_keeps_previous_result_and_shows_error(page):
    open_main(page, sim=True)
    run_sim(page)
    run_id = page.locator(".results-meta b").inner_text()
    page.route("**/api/simulation/run", lambda route: route.fulfill(status=500, content_type="application/json", body=json.dumps({"error": {"code": "internal_error", "message": "Something went wrong on the server."}})))
    page.click("#btn-run-sim")
    expect(page.locator("#toasts")).to_contain_text("Something went wrong")
    assert page.locator(".results-meta b").inner_text() == run_id  # previous valid result kept


def test_risk_selector_recolours_the_map(page):
    open_main(page, sim=True)
    run_sim(page)
    page.wait_for_function("window.__dt.map && window.__dt.map.map && window.__dt.map.map.getLayer('muni-fill')")
    page.wait_for_timeout(500)

    def paint():
        return page.evaluate("JSON.stringify(window.__dt.map.map.getPaintProperty('muni-fill','fill-color'))")

    overall = paint()
    page.select_option("#map-risk-select", "infrastructure_risk")
    page.wait_for_function("document.querySelector('#map-title').textContent.includes('Infrastructure')")
    page.wait_for_timeout(400)
    assert paint() != overall


def test_layers_menu_and_zoom(page):
    open_main(page)
    page.wait_for_function("window.__dt.map && window.__dt.map.map && window.__dt.map.map.getLayer('muni-line')")
    z0 = page.evaluate("window.__dt.map.map.getZoom()")
    page.click('[data-act="zoom-in"]')
    page.wait_for_timeout(700)
    assert page.evaluate("window.__dt.map.map.getZoom()") > z0
    page.click('[data-act="zoom-out"]')
    page.click('[data-act="layers-menu"]')
    assert page.locator("#popover input[data-act=layer]").count() == 8
    page.uncheck('#popover input[data-layer="boundaries"]')
    page.wait_for_timeout(400)
    assert page.evaluate("window.__dt.map.map.getLayoutProperty('muni-line','visibility')") == "none"


def test_municipality_search_view_detail_and_back(page):
    open_main(page, sim=True)
    run_sim(page)
    expect(page.locator(".muni-card")).to_have_count(0)
    open_municipalities(page)
    expect(page.locator(".muni-sim-title")).to_contain_text("Simulation Results")
    expect(page.locator(".muni-sim-title")).to_contain_text("SIMULATED")
    assert page.locator(".muni-full-table tbody tr").count() == 15
    page.fill("#muni-search", "borlange")  # diacritic-insensitive
    assert page.locator(".muni-full-table tbody tr").count() == 1
    expect(page.locator(".muni-full-table tbody")).to_contain_text("Borlänge")
    page.fill("#muni-search", "zzz")
    expect(page.locator(".muni-full-table tbody")).to_contain_text("No municipality found.")
    page.fill("#muni-search", "Borl")
    page.locator(".btn-icon-view").first.click()
    drawer = page.locator("#drawer")
    for text in ("Borlänge", "Current population", "Simulated (2030)", "Food", "Snow", "Infrastructure", "Environment", "Overall", "Contributing sources", "Trend", "Assumptions", "Location"):
        expect(drawer).to_contain_text(text)
    page.click('[data-act="back-regional"]')
    assert page.locator("#drawer").count() == 0
    # row click and risk badge click
    page.fill("#muni-search", "")
    page.locator(".muni-full-table tbody tr").first.locator("td").nth(1).click()
    expect(page.locator("#drawer")).to_be_visible()
    page.keyboard.press("Escape")
    page.locator(".muni-full-table tbody .risk-badge.clickable").first.click()
    expect(page.locator("#drawer")).to_contain_text("Score")


def test_result_cards_impact_rows_and_gauge_open_details(page):
    open_main(page, sim=True)
    run_sim(page)
    page.locator('[data-act="open-cat"][data-cat="infrastructure_risk"]').click()
    expect(page.locator("#drawer")).to_contain_text("Contributing factors")
    page.keyboard.press("Escape")
    page.locator('.impact-table tr:has-text("Traffic")').click()
    expect(page.locator("#drawer")).to_contain_text("Coefficient")
    page.keyboard.press("Escape")
    page.locator('.impact-table tr:has-text("Weather")').click()
    expect(page.locator("#drawer")).to_contain_text("External condition")
    page.keyboard.press("Escape")
    page.click('[data-act="gauge"]')
    expect(page.locator("#drawer")).to_contain_text("Category contributions")
    page.keyboard.press("Escape")
    page.click('[data-act="methodology"]')
    expect(page.locator("#drawer")).to_contain_text("not agency forecasts")
    expect(page.locator("#drawer")).to_contain_text("Population change (total)")
    expect(page.locator("#drawer")).to_contain_text("Scenario inputs")
    page.evaluate("""() => {
        const result = window.__dt.result;
        result.impact_by_source.find((item) => item.id === "scb").status = "excluded";
        result.assumptions.formula = "long".repeat(150);
    }""")
    page.keyboard.press("Escape")
    page.click('[data-act="methodology"]')
    expect(page.locator(".methodology-panel")).to_contain_text("Population change (total) (excluded)")
    page.set_viewport_size({"width": 320, "height": 700})
    assert page.locator(".methodology-panel").evaluate("(el) => el.scrollWidth <= el.clientWidth")
    assert page.locator("#drawer").evaluate("(el) => el.scrollWidth <= el.clientWidth")


def test_current_simulated_change_columns_and_legend_are_not_buttons(page):
    open_main(page, sim=True)
    run_sim(page)
    assert page.locator(".impact-table td.ro button").count() == 0
    assert page.locator(".map-risk-legend button, .map-scale-bar button").count() == 0


def test_exports_csv_json_and_messages(page):
    open_main(page, sim=True)
    run_sim(page)
    open_municipalities(page)
    page.fill("#muni-search", "Falun")  # export honours the filtered view
    page.click('[data-act="export-menu"]')
    with page.expect_download() as d:
        page.click('[data-act="export"][data-fmt="csv"]')
    csv = open(d.value.path(), encoding="utf-8-sig").read()
    assert "Falun" in csv and "Borlänge" not in csv and "SIMULATED" in csv
    expect(page.locator("#toasts")).to_contain_text("Export created successfully.")
    page.click('[data-act="export-menu"]')
    with page.expect_download() as d2:
        page.click('[data-act="export"][data-fmt="json"]')
    payload = json.load(open(d2.value.path(), encoding="utf-8"))
    assert payload["run_id"].startswith("sim-") and payload["assumptions"] and len(payload["municipalities"]) == 1


def test_main_dashboard_uses_backend_thresholds_and_components(page, api):
    apply_state(api, risk_thresholds={"low": 0.2, "medium": 0.4, "high": 0.5}, components={"municipality_table": False})
    open_main(page, sim=True)
    expect(page.locator(".map-risk-legend")).to_contain_text("0.40")
    page.click('#nav a[data-route="municipalities"]')
    expect(page.locator("#muni-slot")).to_contain_text("hidden by the administrator")
    assert page.locator(".muni-card").count() == 0  # component hidden by admin; data untouched


def test_disabled_source_is_shown_as_disabled_on_main(page, api):
    apply_state(api, sources={"smhi": False})
    open_main(page)
    card = page.locator(".kpi-card", has_text="SMHI")
    expect(card).to_contain_text("Disabled")
    expect(card).to_contain_text("Unavailable")
    page.click('[data-act="layers-menu"]')
    expect(page.locator('#popover .layer-row:has-text("Weather")')).to_contain_text("turned off")


def test_no_console_errors_and_no_secrets_in_frontend(page, server):
    import requests

    open_main(page, sim=True)
    run_sim(page)
    assert page.errors == []
    for path in ("/js/dashboard.js", "/js/control-room.js", "/js/shared.js", "/js/dtmap.js", "/js/util.js", "/js/api.js", "/admin.html", "/index.html"):
        text = requests.get(BASE + path).text
        assert not re.search(r"(api[_-]?key|client[_-]?secret|password)\s*[:=]\s*['\"][^'\"]{6,}", text, re.I), path
