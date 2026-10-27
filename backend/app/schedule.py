"""ZESCO rationing schedule dataset: lookup of supply windows and the 24-hour grid-on mask."""
from __future__ import annotations
import csv
import re
from functools import lru_cache
from pathlib import Path
from .config import settings


def _hm(s: str) -> float:
    h, m = s.split(":")
    return int(h) + int(m) / 60.0


@lru_cache(maxsize=4)
def load(path: str | None = None) -> tuple[dict, ...]:
    p = Path(path) if path else settings.schedule_csv
    with open(p, newline="", encoding="utf-8") as f:
        rows = []
        for r in csv.DictReader(f):
            r["start"] = _hm(r["on_start"])
            r["end"] = _hm(r["on_end"])
            rows.append(r)
    return tuple(rows)


def schedules() -> list[dict]:
    seen: dict[str, dict] = {}
    for r in load():
        seen.setdefault(r["schedule_id"], {"schedule_id": r["schedule_id"], "date": r["schedule_date"],
                                           "date_status": r["date_status"]})
    return list(seen.values())


def regions(schedule_id: str | None = None) -> list[str]:
    return sorted({r["region"] for r in load() if schedule_id in (None, r["schedule_id"])})


def _group_matches(wanted: str, row_group: str) -> bool:
    parts = {p.strip().upper() for p in re.split(r"[&,]", row_group)}
    return wanted.strip().upper() in parts or wanted.strip().upper() == row_group.strip().upper()


def groups(schedule_id: str, region: str) -> list[dict]:
    out, seen = [], set()
    for r in load():
        if r["schedule_id"] == schedule_id and r["region"] == region:
            key = (r["group_id"], r["category"])
            if key not in seen:
                seen.add(key)
                out.append({"group_id": r["group_id"], "category": r["category"]})
    return out


def windows(schedule_id: str, region: str, group_id: str, period: str | None = None) -> tuple[list[tuple[float, float]], list[str]]:
    """Return ([(start_h, end_h)...], warnings). end may be < start for windows that cross midnight."""
    rows = [r for r in load() if r["schedule_id"] == schedule_id and r["region"] == region
            and _group_matches(group_id, r["group_id"])]
    if period is None and rows:
        periods = sorted({r["period_label"] for r in rows})
        period = periods[0]
    rows = [r for r in rows if r["period_label"] == (period or "")]
    warns = sorted({r["flags"] for r in rows if r["flags"]})
    if rows and len({r["group_id"] + r["category"] for r in rows}) > 1:
        warns.append("More than one source row matched this group; their windows were combined.")
    if rows and rows[0]["date_status"] != "stated":
        warns.append("The schedule is undated or its date is inferred.")
    return [(r["start"], r["end"]) for r in rows], warns


def grid_on_mask(wins: list[tuple[float, float]]) -> list[bool]:
    """Hour h is 'on' when the supply covers at least half of that hour."""
    cov = [0.0] * 24
    for s, e in wins:
        if e <= s:
            e += 24
        for shift in (0, -24):
            for h in range(24):
                lo, hi = max(h, s + shift), min(h + 1, e + shift)
                if hi > lo:
                    cov[h] += hi - lo
    return [c >= 0.5 for c in cov]


def mask_for(schedule_id: str, region: str, group_id: str, period: str | None = None):
    wins, warns = windows(schedule_id, region, group_id, period)
    if not wins:
        raise KeyError(f"No supply windows for {region} group {group_id} in {schedule_id}")
    return grid_on_mask(wins), wins, warns
