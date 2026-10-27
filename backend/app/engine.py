"""PowerPulse decision engine.

Pure functions, no I/O. Everything is deterministic so results can be reproduced by hand.
All money values are in ZMW and all default prices are PLACEHOLDERS that must be replaced with
current local supplier quotes before any result is presented as a finding.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, asdict, field

ENGINE_VERSION = "1.0.0"


@dataclass
class Equip:
    name: str
    power_kw: float
    hours_per_day: float
    start_hour: int = 8
    critical: bool = False
    flexible: bool = False
    value_per_hour: float = 0.0   # revenue lost for each hour this equipment cannot run
    spoil_per_hour: float = 0.0   # extra loss per unpowered hour (e.g. cold room spoilage)

    def run_mask(self, start: int | None = None) -> list[bool]:
        s = self.start_hour if start is None else start
        n = max(0, min(24, int(round(self.hours_per_day))))
        return [((h - s) % 24) < n for h in range(24)]


@dataclass
class Assumptions:
    tariff: float = 1.8                 # ZMW per kWh from the grid
    diesel_price: float = 30.0          # ZMW per litre
    gen_capex_per_kw: float = 3500.0
    gen_life: float = 10
    gen_l_per_kwh: float = 0.35
    gen_maint_per_hour: float = 15.0
    batt_capex_per_kwh: float = 7000.0
    batt_life: float = 8
    batt_dod: float = 0.8
    batt_eff: float = 0.9
    batt_crate: float = 0.5
    inv_capex_per_kw: float = 3000.0
    inv_life: float = 10
    pv_capex_per_kwp: float = 10000.0
    pv_life: float = 20
    psh: float = 5.5                    # peak sun hours per day
    pv_derate: float = 0.8
    om_rate: float = 0.015              # annual O&M as share of PV/battery/inverter capex
    discount: float = 0.15
    co2_per_litre: float = 2.68         # kg CO2 per litre of diesel (editable default)
    max_batt_kwh: float = 40.0
    max_pv_kwp: float = 15.0
    shift_penalty_per_hour: float = 0.0  # ZMW per moved equipment-hour (night premium, etc.)


def crf(i: float, n: float) -> float:
    return 1.0 / n if i <= 0 else i * (1 + i) ** n / ((1 + i) ** n - 1)


def pv_shape() -> list[float]:
    w = [max(0.0, math.sin(math.pi * (h + 0.5 - 6) / 12)) if 6 <= h < 18 else 0.0 for h in range(24)]
    s = sum(w)
    return [x / s for x in w]


def _priority(equips: list[Equip]) -> list[int]:
    return sorted(range(len(equips)), key=lambda i: (not equips[i].critical,
                  -((equips[i].value_per_hour + equips[i].spoil_per_hour) / max(equips[i].power_kw, 1e-9))))


def best_start(e: Equip, grid_on: list[bool]) -> int:
    best = None
    for s in range(24):
        m = e.run_mask(s)
        off = sum(1 for h in range(24) if m[h] and not grid_on[h])
        dist = min((s - e.start_hour) % 24, (e.start_hour - s) % 24)
        key = (off, dist)
        if best is None or key < best[0]:
            best = (key, s)
    return best[1]


def simulate(equips: list[Equip], masks: list[list[bool]], grid_on: list[bool], cfg: dict, a: Assumptions) -> dict:
    """Simulate one representative operating day (run twice so battery state-of-charge settles)."""
    gen_kw, cap = cfg.get("gen_kw", 0.0), cfg.get("batt_kwh", 0.0)
    inv, pvk = cfg.get("inv_kw", 0.0), cfg.get("pv_kwp", 0.0)
    order = _priority(equips)
    shape = pv_shape()
    pv = [pvk * a.psh * a.pv_derate * s for s in shape]
    soc, reserve = cap, (1 - a.batt_dod) * cap
    tot = {}
    for _ in range(2):
        tot = dict(grid_kwh=0.0, pv_kwh=0.0, gen_kwh=0.0, fuel_l=0.0, gen_hours=0.0, loss=0.0,
                   unserved_kwh=0.0, crit_unserved_kwh=0.0, crit_off_kwh=0.0, off_demand_kwh=0.0)
        for h in range(24):
            p = [e.power_kw if masks[i][h] else 0.0 for i, e in enumerate(equips)]
            demand = sum(p)
            if grid_on[h]:
                use = min(pv[h], demand) if inv > 0 else 0.0
                grid = demand - use
                pv_used = use
                if cap > 0:
                    room_in = (cap - soc) / a.batt_eff
                    take = min(room_in, cap * a.batt_crate)
                    from_pv = min(max(pv[h] - use, 0.0) if inv > 0 else 0.0, take)
                    from_grid = take - from_pv
                    soc += take * a.batt_eff
                    grid += from_grid
                    pv_used += from_pv
                tot["grid_kwh"] += grid
                tot["pv_kwh"] += pv_used
            else:
                tot["off_demand_kwh"] += demand
                use = min(pv[h], demand, inv) if inv > 0 else 0.0
                rem = demand - use
                cap_left = max(0.0, inv - use)
                deliverable = max(0.0, soc - reserve) * a.batt_eff
                b = min(rem, cap_left, deliverable) if cap > 0 else 0.0
                soc -= b / a.batt_eff
                rem -= b
                pv_used = use
                if cap > 0 and inv > 0:
                    surplus = max(pv[h] - use, 0.0)
                    add = min(surplus, (cap - soc) / a.batt_eff, cap * a.batt_crate)
                    soc += add * a.batt_eff
                    pv_used += add
                g = min(rem, gen_kw)
                rem -= g
                tot["pv_kwh"] += pv_used
                tot["gen_kwh"] += g
                tot["fuel_l"] += g * a.gen_l_per_kwh
                tot["gen_hours"] += 1.0 if g > 0 else 0.0
                tot["unserved_kwh"] += rem
                supply = demand - rem
                for i in order:
                    if p[i] <= 0:
                        continue
                    s = min(p[i], supply)
                    supply -= s
                    frac = 1.0 - s / p[i]
                    tot["loss"] += frac * (equips[i].value_per_hour + equips[i].spoil_per_hour)
                    if equips[i].critical:
                        tot["crit_off_kwh"] += p[i]
                        tot["crit_unserved_kwh"] += p[i] - s
    return tot


def _max_off_block_energy(load: list[float], grid_on: list[bool]) -> float:
    best = cur = 0.0
    for k in range(48):
        h = k % 24
        if grid_on[h]:
            cur = 0.0
        else:
            cur += load[h]
            best = max(best, cur)
    return best


def _ceil_half(x: float) -> float:
    return math.ceil(x * 2 - 1e-9) / 2


def size_systems(equips: list[Equip], masks: list[list[bool]], grid_on: list[bool], a: Assumptions) -> dict:
    served = [i for i, e in enumerate(equips) if e.critical] or list(range(len(equips)))
    crit_load = [sum(equips[i].power_kw for i in served if masks[i][h]) for h in range(24)]
    all_load = [sum(e.power_kw for i, e in enumerate(equips) if masks[i][h]) for h in range(24)]
    need = _max_off_block_energy(crit_load, grid_on)
    peak_crit, peak_all = max(crit_load, default=0), max(all_load, default=0)
    batt = min(a.max_batt_kwh, _ceil_half(need / (a.batt_dod * a.batt_eff))) if need > 0 else 0.0
    inv = _ceil_half(peak_crit * 1.1) if batt > 0 else 0.0
    daily = sum(all_load)
    pvk = min(a.max_pv_kwp, _ceil_half(daily * 0.5 / (a.psh * a.pv_derate))) if daily > 0 and need > 0 else 0.0
    inv_sol = max(inv, _ceil_half(min(peak_all, pvk))) if pvk > 0 else inv
    return dict(batt_kwh=batt, inv_kw=inv, pv_kwp=pvk, inv_kw_solar=inv_sol, peak_all=peak_all, peak_crit=peak_crit,
                off_energy_kwh=need)


def _cost(cfg: dict, existing_gen: float, sim: dict, a: Assumptions, days: float, extra_penalty: float) -> dict:
    new_gen = max(0.0, cfg.get("gen_kw", 0.0) - existing_gen)
    parts = {
        "generator": new_gen * a.gen_capex_per_kw,
        "battery": cfg.get("batt_kwh", 0.0) * a.batt_capex_per_kwh,
        "inverter": cfg.get("inv_kw", 0.0) * a.inv_capex_per_kw,
        "solar": cfg.get("pv_kwp", 0.0) * a.pv_capex_per_kwp,
    }
    capex = sum(parts.values())
    ann_capex = (parts["generator"] * crf(a.discount, a.gen_life) + parts["battery"] * crf(a.discount, a.batt_life)
                 + parts["inverter"] * crf(a.discount, a.inv_life) + parts["solar"] * crf(a.discount, a.pv_life))
    om = a.om_rate * (parts["battery"] + parts["inverter"] + parts["solar"])
    fuel_l = sim["fuel_l"] * days
    opex = fuel_l * a.diesel_price + sim["gen_hours"] * days * a.gen_maint_per_hour + om
    grid_cost = sim["grid_kwh"] * days * a.tariff
    loss = sim["loss"] * days + extra_penalty
    run = opex + grid_cost + loss
    return dict(parts=parts, capex=capex, annualised_capex=ann_capex, opex=opex, grid_cost=grid_cost,
                unserved_loss=loss, running=run, total=ann_capex + run, fuel_l=fuel_l)


def analyse(equips: list[Equip], grid_on: list[bool], a: Assumptions, days_per_week: float = 6.0,
            existing_gen_kw: float = 0.0) -> dict:
    if not equips:
        raise ValueError("At least one piece of equipment is required")
    days = days_per_week * 52
    base_masks = [e.run_mask() for e in equips]
    starts = [best_start(e, grid_on) if e.flexible else e.start_hour for e in equips]
    shift_masks = [e.run_mask(s) for e, s in zip(equips, starts)]
    moved_hours = sum(e.hours_per_day for e, s in zip(equips, starts) if e.flexible and s != e.start_hour)
    penalty = moved_hours * a.shift_penalty_per_hour * days

    results = []
    for shift in (False, True):
        masks = shift_masks if shift else base_masks
        sz = size_systems(equips, masks, grid_on, a)
        cfgs = {
            "none": dict(gen_kw=existing_gen_kw),
            "diesel": dict(gen_kw=max(existing_gen_kw, _ceil_half(sz["peak_all"] * 1.25))),
            "battery": dict(gen_kw=existing_gen_kw, batt_kwh=sz["batt_kwh"], inv_kw=sz["inv_kw"]),
            "solar_battery": dict(gen_kw=existing_gen_kw, batt_kwh=sz["batt_kwh"], inv_kw=sz["inv_kw_solar"],
                                  pv_kwp=sz["pv_kwp"]),
        }
        for supply, cfg in cfgs.items():
            if supply == "diesel" and sz["off_energy_kwh"] <= 0:
                continue
            if supply in ("battery", "solar_battery") and cfg.get("batt_kwh", 0) <= 0:
                continue
            sim = simulate(equips, masks, grid_on, cfg, a)
            c = _cost(cfg, existing_gen_kw, sim, a, days, penalty if shift else 0.0)
            crit_share = (sim["crit_unserved_kwh"] / sim["crit_off_kwh"]) if sim["crit_off_kwh"] > 0 else 0.0
            sid = supply + ("+shift" if shift else "")
            results.append(dict(
                id=sid, supply=supply, shift=shift, config=cfg, sizing={k: sz[k] for k in ("batt_kwh", "inv_kw", "pv_kwp")},
                capex=c["capex"], capex_breakdown=c["parts"], annualised_capex=c["annualised_capex"], opex=c["opex"],
                grid_cost=c["grid_cost"], unserved_loss=c["unserved_loss"], running_cost=c["running"], total_annual_cost=c["total"],
                diesel_litres_per_year=c["fuel_l"], co2_tonnes_per_year=c["fuel_l"] * a.co2_per_litre / 1000.0,
                unserved_kwh_per_year=sim["unserved_kwh"] * days, critical_unserved_share=crit_share,
                critical_ok=crit_share <= 0.05))
    base = next(r for r in results if r["id"] == "none")
    for r in results:
        saving = base["running_cost"] - r["running_cost"]
        if r["capex"] <= 0:
            r["payback_years"] = 0.0 if saving >= 0 else None
        else:
            r["payback_years"] = r["capex"] / saving if saving > 0 else None
        r["annual_saving_vs_current"] = saving
        r["label"] = _label(r)
    eligible = [r for r in results if r["critical_ok"]] or results
    best = min(eligible, key=lambda r: r["total_annual_cost"])
    results.sort(key=lambda r: r["total_annual_cost"])
    off_hours = sum(1 for g in grid_on if not g)
    off_operating = sum(1 for h in range(24) if not grid_on[h] and any(m[h] for m in base_masks))
    warnings = []
    if not any(r["critical_ok"] for r in results):
        warnings.append("No strategy keeps critical equipment running within the size limits; review limits and inputs.")
    return dict(
        engine_version=ENGINE_VERSION, baseline_id="none", recommended_id=best["id"], strategies=results,
        supply_gap=dict(grid_off_hours_per_day=off_hours, off_hours_while_operating_per_day=off_operating,
                        off_hours_while_operating_per_week=off_operating * days_per_week,
                        baseline_annual_loss=base["unserved_loss"]),
        priority=[dict(name=equips[i].name, loss_per_kw_hour=(equips[i].value_per_hour + equips[i].spoil_per_hour) / max(equips[i].power_kw, 1e-9),
                       critical=equips[i].critical) for i in _priority(equips)],
        shift_plan=[dict(name=e.name, current_start=e.start_hour, suggested_start=s, hours=e.hours_per_day)
                    for e, s in zip(equips, starts) if e.flexible],
        grid_on=grid_on, warnings=warnings)


def _label(r: dict) -> str:
    names = {"none": "Current practice", "diesel": "Diesel generator", "battery": "Battery backup",
             "solar_battery": "Solar plus battery"}
    return names[r["supply"]] + (" with shifted hours" if r["shift"] else "")


def sensitivity(equips: list[Equip], grid_on: list[bool], a: Assumptions, days_per_week: float, existing_gen_kw: float) -> list[dict]:
    out = []
    base_best = analyse(equips, grid_on, a, days_per_week, existing_gen_kw)["recommended_id"]
    def run(name, **chg):
        a2 = Assumptions(**{**asdict(a), **{k: getattr(a, k) * v for k, v in chg.items()}})
        best = analyse(equips, grid_on, a2, days_per_week, existing_gen_kw)["recommended_id"]
        out.append(dict(scenario=name, recommended_id=best, changed=best != base_best))
    run("Diesel price -30%", diesel_price=0.7)
    run("Diesel price +30%", diesel_price=1.3)
    run("Battery price -30%", batt_capex_per_kwh=0.7)
    run("Battery price +30%", batt_capex_per_kwh=1.3)
    run("Solar price -30%", pv_capex_per_kwp=0.7)
    run("Grid tariff +30%", tariff=1.3)
    run("Discount rate +50%", discount=1.5)
    return [dict(scenario="Base case", recommended_id=base_best, changed=False)] + out


def check_quote(quote: dict, equips: list[Equip], grid_on: list[bool], a: Assumptions, days_per_week: float,
                existing_gen_kw: float) -> dict:
    masks = [e.run_mask() for e in equips]
    sz = size_systems(equips, masks, grid_on, a)
    batt, pvk = float(quote.get("battery_kwh", 0)), float(quote.get("pv_kwp", 0))
    inv, gen = float(quote.get("inverter_kw", 0)), float(quote.get("generator_kw", 0))
    price = float(quote.get("price_zmw", 0))
    fair = batt * a.batt_capex_per_kwh + pvk * a.pv_capex_per_kwp + inv * a.inv_capex_per_kw + gen * a.gen_capex_per_kw
    flags = []
    if batt > 1.5 * max(sz["batt_kwh"], 0.5) and sz["batt_kwh"] > 0:
        flags.append(f"Battery capacity {batt:g} kWh is more than 1.5 times the modelled need of {sz['batt_kwh']:g} kWh.")
    if pvk > 1.5 * max(sz["pv_kwp"], 0.5) and sz["pv_kwp"] > 0:
        flags.append(f"Solar size {pvk:g} kWp is more than 1.5 times the modelled size of {sz['pv_kwp']:g} kWp.")
    if inv > 1.5 * max(sz["inv_kw_solar"], 0.5):
        flags.append(f"Inverter {inv:g} kW is much larger than the modelled peak of {sz['inv_kw_solar']:g} kW.")
    if 0 < fair and price > 1.3 * fair:
        flags.append("The price is more than 30% above the price implied by the current assumptions. Ask the supplier to itemise it.")
    if 0 < fair and price < 0.6 * fair:
        flags.append("The price is far below the modelled price. Check equipment quality and warranty terms.")
    cfg = dict(gen_kw=max(existing_gen_kw, gen), batt_kwh=batt, inv_kw=inv, pv_kwp=pvk)
    days = days_per_week * 52
    base = _cost(dict(gen_kw=existing_gen_kw), existing_gen_kw, simulate(equips, masks, grid_on, dict(gen_kw=existing_gen_kw), a), a, days, 0)
    this = _cost(cfg, existing_gen_kw, simulate(equips, masks, grid_on, cfg, a), a, days, 0)
    saving = base["running"] - this["running"]
    payback = price / saving if saving > 0 and price > 0 else None
    if payback is None:
        flags.append("On the stated inputs this quotation does not reduce running costs.")
    elif payback > 5:
        flags.append(f"Simple payback is about {payback:.1f} years on these inputs.")
    return dict(modelled_need=dict(battery_kwh=sz["batt_kwh"], pv_kwp=sz["pv_kwp"], inverter_kw=sz["inv_kw_solar"]),
                implied_price=fair, quoted_price=price, payback_years=payback, flags=flags)


def measured_grid_mask(avail_by_hour: dict[int, float], fallback: list[bool]) -> list[bool]:
    """Build a grid mask from measured availability per hour of day (fraction of time power was on)."""
    return [(avail_by_hour[h] >= 0.5) if h in avail_by_hour else fallback[h] for h in range(24)]
