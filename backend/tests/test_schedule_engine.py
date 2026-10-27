from app import schedule, engine as eng
from app.engine import Equip, Assumptions, analyse


def on_hours(mask):
    return [h for h in range(24) if mask[h]]


def test_plain_window():
    mask, _, _ = schedule.mask_for("ZESCO_14Oct2024", "Kitwe & Kalulushi", "A")  # 01:00-05:00
    assert on_hours(mask) == [1, 2, 3, 4]


def test_window_crossing_midnight():
    mask, wins, _ = schedule.mask_for("ZESCO_14Oct2024", "Kitwe & Kalulushi", "H")  # 21:00-01:00
    assert on_hours(mask) == [0, 21, 22, 23]


def test_twelve_hour_window_ending_at_midnight():
    mask, _, _ = schedule.mask_for("ZESCO_26Aug2024", "Kitwe & Kalulushi", "E")  # 12-00
    assert on_hours(mask) == list(range(12, 24))


def test_composite_group_id_matches_part():
    mask, _, _ = schedule.mask_for("ZESCO_14Oct2024", "Kitwe & Kalulushi", "G")  # F&G row
    assert on_hours(mask) == [17, 18, 19, 20, 21]


def test_half_hour_window_rounds_by_coverage():
    mask, _, _ = schedule.mask_for("ZESCO_7h_undated", "Northwestern", "C")  # 13:30-20:30
    assert on_hours(mask) == list(range(14, 21)) + [13] or on_hours(mask) == list(range(13, 21))


def test_unknown_group_raises():
    import pytest
    with pytest.raises(KeyError):
        schedule.mask_for("ZESCO_14Oct2024", "Kitwe & Kalulushi", "ZZ")


BAKERY = [Equip("Oven", 6, 8, 4, True, False, 250, 0), Equip("Mixer", 1.5, 3, 3, False, True, 80, 0),
          Equip("Fridge", 0.4, 24, 0, True, False, 20, 15)]
LATE = [h in (21, 22, 23, 0) for h in range(24)]


def test_no_outage_means_no_loss_and_baseline_recommended():
    r = analyse(BAKERY, [True] * 24, Assumptions(), 6, 0)
    assert r["supply_gap"]["baseline_annual_loss"] == 0
    assert r["recommended_id"] in ("none", "none+shift")


def test_outages_create_loss_and_recommend_backup():
    r = analyse(BAKERY, LATE, Assumptions(), 6, 0)
    assert r["supply_gap"]["baseline_annual_loss"] > 0
    assert r["recommended_id"] not in ("none",)
    best = next(s for s in r["strategies"] if s["id"] == r["recommended_id"])
    base = next(s for s in r["strategies"] if s["id"] == "none")
    assert best["total_annual_cost"] < base["total_annual_cost"]
    assert best["critical_ok"]


def test_shifting_moves_flexible_equipment_into_supply_window():
    r = analyse(BAKERY, LATE, Assumptions(), 6, 0)
    mixer = next(p for p in r["shift_plan"] if p["name"] == "Mixer")
    assert mixer["suggested_start"] != mixer["current_start"]
    run = Equip("Mixer", 1.5, 3).run_mask(mixer["suggested_start"])
    assert sum(1 for h in range(24) if run[h] and not LATE[h]) == 0


def test_higher_battery_price_never_lowers_battery_cost():
    cheap = analyse(BAKERY, LATE, Assumptions(batt_capex_per_kwh=3000), 6, 0)
    dear = analyse(BAKERY, LATE, Assumptions(batt_capex_per_kwh=12000), 6, 0)
    g = lambda r: next((s for s in r["strategies"] if s["id"] == "battery"), None)
    if g(cheap) and g(dear):
        assert g(dear)["total_annual_cost"] >= g(cheap)["total_annual_cost"]


def test_more_diesel_cost_raises_diesel_strategy_cost():
    a = analyse(BAKERY, LATE, Assumptions(diesel_price=20), 6, 0)
    b = analyse(BAKERY, LATE, Assumptions(diesel_price=40), 6, 0)
    f = lambda r: next(s for s in r["strategies"] if s["id"] == "diesel")
    assert f(b)["total_annual_cost"] > f(a)["total_annual_cost"]


def test_priority_puts_critical_first():
    r = analyse(BAKERY, LATE, Assumptions(), 6, 0)
    assert r["priority"][0]["critical"] is True


def test_sensitivity_lists_base_and_scenarios():
    s = eng.sensitivity(BAKERY, LATE, Assumptions(), 6, 0)
    assert s[0]["scenario"] == "Base case" and len(s) >= 6


def test_quote_checker_flags_oversized_battery():
    q = eng.check_quote(dict(battery_kwh=80, pv_kwp=0, inverter_kw=10, generator_kw=0, price_zmw=900000), BAKERY, LATE, Assumptions(), 6, 0)
    assert any("Battery capacity" in f for f in q["flags"])


def test_empty_equipment_rejected():
    import pytest
    with pytest.raises(ValueError):
        analyse([], LATE, Assumptions())


def test_measured_mask_overrides_schedule_only_where_data_exists():
    fallback = [h in (1, 2, 3) for h in range(24)]
    m = eng.measured_grid_mask({1: 0.1, 2: 0.9, 12: 0.8}, fallback)
    assert m[1] is False and m[2] is True and m[12] is True and m[3] is True and m[5] is False
