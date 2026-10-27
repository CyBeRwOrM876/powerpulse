"""PowerPulse API."""
from __future__ import annotations
import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import engine as eng, ingest as ing, reports, schedule
from .config import settings, BASE
from .db import Audit, Business, Device, Equipment, Outage, Reading, Report, User, get_db, init_db
from .security import (audit, check_password_strength, current_user, EMAIL_RE, hash_password, limiter, make_token,
                       new_device_secret, owned_business, require_role, verify_audit_chain, verify_password)
from .templates import TEMPLATES

logging.basicConfig(level=logging.INFO)
TZ = ZoneInfo(settings.tz)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ing.start_mqtt()
    reports.signer()
    yield


app = FastAPI(title="PowerPulse", version=eng.ENGINE_VERSION, lifespan=lifespan, docs_url="/api/docs" if settings.env != "prod" else None,
              redoc_url=None, openapi_url="/api/openapi.json" if settings.env != "prod" else None)
if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if int(request.headers.get("content-length", 0) or 0) > 1_000_000:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    resp = await call_next(request)
    resp.headers.update({
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        "Cache-Control": resp.headers.get("Cache-Control", "no-store") if request.url.path.startswith("/api") else resp.headers.get("Cache-Control", "no-cache"),
    })
    if settings.env == "prod":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


# ---------------- schemas ----------------
class Creds(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=200)


class EquipIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    power_kw: float = Field(gt=0, le=500)
    hours_per_day: float = Field(ge=0, le=24)
    start_hour: int = Field(0, ge=0, le=23)
    critical: bool = False
    flexible: bool = False
    value_per_hour: float = Field(0, ge=0, le=1e7)
    spoil_per_hour: float = Field(0, ge=0, le=1e7)
    channel: int | None = Field(None, ge=0, le=7)


class BusinessIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sector: str = Field("custom", max_length=40)
    region: str = Field(max_length=80)
    group_id: str = Field(max_length=20)
    schedule_id: str = Field(max_length=60)
    days_per_week: float = Field(6, ge=1, le=7)
    tariff: float = Field(1.8, gt=0, le=1000)
    diesel_price: float = Field(30.0, gt=0, le=1000)
    existing_gen_kw: float = Field(0, ge=0, le=1000)
    equipment: list[EquipIn] = Field(min_length=1, max_length=60)


class AnalyseIn(BaseModel):
    assumptions: dict[str, float] = {}
    use_measured: bool = False
    include_sensitivity: bool = True

    @field_validator("assumptions")
    @classmethod
    def _known(cls, v):
        names = {f.name for f in fields(eng.Assumptions)}
        for k, x in v.items():
            if k not in names:
                raise ValueError(f"Unknown assumption: {k}")
            if not (0 <= x <= 1e9):
                raise ValueError(f"Assumption out of range: {k}")
        return v


class QuoteIn(BaseModel):
    battery_kwh: float = Field(0, ge=0, le=1000)
    pv_kwp: float = Field(0, ge=0, le=1000)
    inverter_kw: float = Field(0, ge=0, le=1000)
    generator_kw: float = Field(0, ge=0, le=1000)
    price_zmw: float = Field(gt=0, le=1e9)


class DiaryIn(BaseModel):
    start_ts: int
    end_ts: int

    @field_validator("end_ts")
    @classmethod
    def _order(cls, v, info):
        if "start_ts" in info.data and v <= info.data["start_ts"]:
            raise ValueError("end must be after start")
        return v


class VerifyIn(BaseModel):
    canonical: str = Field(max_length=500_000)
    signature: str = Field(max_length=200)
    public_key_pem: str | None = Field(None, max_length=2000)


# ---------------- helpers ----------------
def _equips(b: Business, overrides: dict[int, float] | None = None) -> list[eng.Equip]:
    out = []
    for e in b.equipment:
        p = overrides.get(e.channel, e.power_kw) if overrides and e.channel is not None else e.power_kw
        out.append(eng.Equip(e.name, p, e.hours_per_day, e.start_hour, e.critical, e.flexible, e.value_per_hour, e.spoil_per_hour))
    return out


def _business_out(b: Business) -> dict:
    d = {c: getattr(b, c) for c in ("id", "name", "sector", "region", "group_id", "schedule_id", "days_per_week", "tariff", "diesel_price", "existing_gen_kw")}
    d["equipment"] = [{c: getattr(e, c) for c in ("id", "name", "power_kw", "hours_per_day", "start_hour", "critical", "flexible", "value_per_hour", "spoil_per_hour", "channel")} for e in b.equipment]
    return d


def _validate_group(body: BusinessIn):
    try:
        schedule.mask_for(body.schedule_id, body.region, body.group_id)
    except KeyError as e:
        raise HTTPException(422, str(e))


def _apply(b: Business, body: BusinessIn):
    for f in ("name", "sector", "region", "group_id", "schedule_id", "days_per_week", "tariff", "diesel_price", "existing_gen_kw"):
        setattr(b, f, getattr(body, f))
    b.equipment.clear()
    for e in body.equipment:
        b.equipment.append(Equipment(**e.model_dump()))


def measured_stats(db: Session, b: Business, days: int = 14) -> dict:
    devs = db.execute(select(Device).where(Device.business_id == b.id)).scalars().all()
    if not devs:
        return {"hours_of_data": 0}
    since = int(time.time()) - days * 86400
    rows = db.execute(select(Reading).where(Reading.device_id.in_([d.id for d in devs]), Reading.ts >= since).order_by(Reading.ts).limit(300000)).scalars().all()
    avail_n, avail_on, ch_sum, ch_n = {}, {}, {}, {}
    for r in rows:
        h = datetime.fromtimestamp(r.ts, TZ).hour
        avail_n[h] = avail_n.get(h, 0) + 1
        avail_on[h] = avail_on.get(h, 0) + (1 if r.grid else 0)
        if r.grid:
            for i, w in enumerate(r.watts.split(",")):
                w = int(w)
                if w >= 50:
                    ch_sum[i] = ch_sum.get(i, 0) + w
                    ch_n[i] = ch_n.get(i, 0) + 1
    availability = {h: avail_on[h] / avail_n[h] for h in avail_n if avail_n[h] >= 20}
    channel_kw = {i: ch_sum[i] / ch_n[i] / 1000.0 for i in ch_sum if ch_n[i] >= 20}
    span_h = (rows[-1].ts - rows[0].ts) / 3600 if len(rows) > 1 else 0
    return {"hours_of_data": round(span_h, 1), "readings": len(rows), "availability_by_hour": availability, "channel_mean_kw": channel_kw}


def run_analysis(db: Session, b: Business, body: AnalyseIn) -> dict:
    mask, wins, warns = schedule.mask_for(b.schedule_id, b.region, b.group_id)
    a = eng.Assumptions(**{**asdict(eng.Assumptions(tariff=b.tariff, diesel_price=b.diesel_price)), **body.assumptions})
    meas, overrides = None, None
    if body.use_measured:
        meas = measured_stats(db, b)
        if meas.get("hours_of_data", 0) >= 24:
            mask = eng.measured_grid_mask(meas["availability_by_hour"], mask)
            overrides = meas["channel_mean_kw"]
        else:
            warns.append("Fewer than 24 hours of measured data; the published schedule was used.")
            meas = None
    eq = _equips(b, overrides)
    res = eng.analyse(eq, mask, a, b.days_per_week, b.existing_gen_kw)
    res["schedule_warnings"] = warns
    res["assumptions"] = asdict(a)
    res["measured"] = ({"hours_of_data": meas["hours_of_data"], "channel_mean_kw": meas["channel_mean_kw"]} if meas else None)
    if body.include_sensitivity:
        res["sensitivity"] = eng.sensitivity(eq, mask, a, b.days_per_week, b.existing_gen_kw)
    return res


# ---------------- public ----------------
@app.get("/healthz")
def healthz():
    return {"ok": True, "engine": eng.ENGINE_VERSION}


@app.get("/api/v1/templates")
def templates():
    return TEMPLATES


@app.get("/api/v1/schedules")
def schedules():
    return schedule.schedules()


@app.get("/api/v1/schedules/{schedule_id}/regions")
def sched_regions(schedule_id: str):
    return schedule.regions(schedule_id)


@app.get("/api/v1/schedules/{schedule_id}/groups")
def sched_groups(schedule_id: str, region: str):
    g = schedule.groups(schedule_id, region)
    if not g:
        raise HTTPException(404, "No groups")
    return g


@app.get("/api/v1/schedules/{schedule_id}/mask")
def sched_mask(schedule_id: str, region: str, group_id: str):
    try:
        mask, wins, warns = schedule.mask_for(schedule_id, region, group_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"grid_on": mask, "windows": wins, "warnings": warns}


@app.get("/api/v1/public-key")
def public_key():
    s = reports.signer()
    return {"key_id": s.key_id, "public_key_pem": s.public_pem, "algorithm": "Ed25519"}


@app.post("/api/v1/verify", dependencies=[Depends(limiter("verify", 60))])
def verify(body: VerifyIn):
    pem = body.public_key_pem or reports.signer().public_pem
    ok = reports.verify(pem, body.canonical, body.signature)
    return {"valid": ok, "payload_sha256": hashlib.sha256(body.canonical.encode()).hexdigest()}


@app.get("/api/v1/public/reports/{report_id}", dependencies=[Depends(limiter("pubreport", 60))])
def public_report(report_id: str, db: Session = Depends(get_db)):
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(404, "Report not found")
    s = reports.signer()
    return {"id": r.id, "created": r.created, "key_id": r.key_id, "signature": r.sig_hex, "canonical": r.payload,
            "report": json.loads(r.payload), "public_key_pem": s.public_pem, "valid": reports.verify(s.public_pem, r.payload, r.sig_hex)}


# ---------------- auth ----------------
@app.post("/api/v1/auth/register", status_code=201, dependencies=[Depends(limiter("register", 5))])
def register(c: Creds, db: Session = Depends(get_db)):
    email = c.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Invalid email")
    check_password_strength(c.password)
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(409, "Email already registered")
    u = User(email=email, pw_hash=hash_password(c.password), role="owner")
    db.add(u)
    db.commit()
    audit(db, email, "register")
    return {"token": make_token(u), "role": u.role}


@app.post("/api/v1/auth/login", dependencies=[Depends(limiter("login", 10))])
def login(c: Creds, db: Session = Depends(get_db)):
    email = c.email.strip().lower()
    u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    ok = verify_password(c.password, u.pw_hash) if u else verify_password(c.password, "scrypt$00$00")  # constant-ish work
    if not u or not ok:
        audit(db, email[:80], "login_failed")
        raise HTTPException(401, "Incorrect email or password")
    audit(db, email, "login")
    return {"token": make_token(u), "role": u.role}


@app.get("/api/v1/me")
def me(u: User = Depends(current_user)):
    return {"id": u.id, "email": u.email, "role": u.role}


# ---------------- businesses ----------------
@app.get("/api/v1/businesses")
def list_businesses(u: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Business) if u.role == "admin" else select(Business).where(Business.owner_id == u.id)
    return [_business_out(b) for b in db.execute(q).scalars().all()]


@app.post("/api/v1/businesses", status_code=201)
def create_business(body: BusinessIn, u: User = Depends(require_role("owner", "admin")), db: Session = Depends(get_db)):
    _validate_group(body)
    b = Business(owner_id=u.id)
    _apply(b, body)
    db.add(b)
    db.commit()
    audit(db, u.email, "business_create", str(b.id))
    return _business_out(b)


@app.get("/api/v1/businesses/{business_id}")
def get_business(b: Business = Depends(owned_business)):
    return _business_out(b)


@app.put("/api/v1/businesses/{business_id}")
def update_business(body: BusinessIn, b: Business = Depends(owned_business), u: User = Depends(current_user), db: Session = Depends(get_db)):
    _validate_group(body)
    _apply(b, body)
    db.commit()
    audit(db, u.email, "business_update", str(b.id))
    return _business_out(b)


@app.delete("/api/v1/businesses/{business_id}", status_code=204)
def delete_business(b: Business = Depends(owned_business), u: User = Depends(current_user), db: Session = Depends(get_db)):
    for model in (Reading,):
        pass
    devs = db.execute(select(Device).where(Device.business_id == b.id)).scalars().all()
    for d in devs:
        for r in db.execute(select(Reading).where(Reading.device_id == d.id)).scalars():
            db.delete(r)
        db.delete(d)
    for o in db.execute(select(Outage).where(Outage.business_id == b.id)).scalars():
        db.delete(o)
    for r in db.execute(select(Report).where(Report.business_id == b.id)).scalars():
        db.delete(r)
    db.delete(b)
    db.commit()
    audit(db, u.email, "business_delete", str(b.id))


@app.post("/api/v1/businesses/{business_id}/analyse")
def analyse(body: AnalyseIn, b: Business = Depends(owned_business), db: Session = Depends(get_db)):
    try:
        return run_analysis(db, b, body)
    except KeyError as e:
        raise HTTPException(422, str(e))


@app.post("/api/v1/businesses/{business_id}/quote-check")
def quote_check(q: QuoteIn, b: Business = Depends(owned_business), db: Session = Depends(get_db)):
    mask, _, _ = schedule.mask_for(b.schedule_id, b.region, b.group_id)
    a = eng.Assumptions(tariff=b.tariff, diesel_price=b.diesel_price)
    return eng.check_quote(q.model_dump(), _equips(b), mask, a, b.days_per_week, b.existing_gen_kw)


# ---------------- devices and telemetry ----------------
@app.post("/api/v1/businesses/{business_id}/devices", status_code=201)
def add_device(b: Business = Depends(owned_business), u: User = Depends(current_user), db: Session = Depends(get_db)):
    key = f"node-{secrets.token_hex(4)}"
    secret = new_device_secret()
    db.add(Device(business_id=b.id, device_key=key, secret_hex=secret))
    db.commit()
    audit(db, u.email, "device_create", key)
    return {"device_key": key, "secret_hex": secret, "note": "Store this secret in the firmware config now. It is not shown again."}


@app.post("/api/v1/ingest", dependencies=[Depends(limiter("ingest", 1200))])
def ingest_http(msg: dict, db: Session = Depends(get_db)):
    try:
        return ing.ingest(db, msg)
    except ing.IngestError as e:
        raise HTTPException(e.code, e.msg)


@app.get("/api/v1/businesses/{business_id}/live")
def live(b: Business = Depends(owned_business), db: Session = Depends(get_db)):
    devs = db.execute(select(Device).where(Device.business_id == b.id)).scalars().all()
    out = []
    for d in devs:
        r = db.execute(select(Reading).where(Reading.device_id == d.id).order_by(Reading.ts.desc()).limit(1)).scalar_one_or_none()
        out.append({"device_key": d.device_key, "last_seen": d.last_ts, "online": bool(r and time.time() - r.ts < 30),
                    "latest": ({"ts": r.ts, "v": r.v, "pf": r.pf, "f": r.f, "grid": r.grid, "watts": [int(x) for x in r.watts.split(",")]} if r else None)})
    return out


@app.get("/api/v1/businesses/{business_id}/history")
def history(hours: int = 24, b: Business = Depends(owned_business), db: Session = Depends(get_db)):
    hours = max(1, min(hours, 168))
    devs = db.execute(select(Device).where(Device.business_id == b.id)).scalars().all()
    since = int(time.time()) - hours * 3600
    buckets: dict[int, list] = {}
    for d in devs:
        for r in db.execute(select(Reading).where(Reading.device_id == d.id, Reading.ts >= since).order_by(Reading.ts)).scalars():
            buckets.setdefault(r.ts // 300 * 300, []).append(sum(int(x) for x in r.watts.split(",")))
    return [{"ts": k, "watts": sum(v) / len(v)} for k, v in sorted(buckets.items())]


@app.get("/api/v1/businesses/{business_id}/outages")
def outages(b: Business = Depends(owned_business), db: Session = Depends(get_db)):
    rows = db.execute(select(Outage).where(Outage.business_id == b.id).order_by(Outage.start_ts.desc()).limit(200)).scalars().all()
    return [{"id": o.id, "start_ts": o.start_ts, "end_ts": o.end_ts, "source": o.source,
             "minutes": None if o.end_ts is None else round((o.end_ts - o.start_ts) / 60, 1)} for o in rows]


@app.post("/api/v1/businesses/{business_id}/outages", status_code=201)
def add_diary(body: DiaryIn, b: Business = Depends(owned_business), db: Session = Depends(get_db)):
    db.add(Outage(business_id=b.id, start_ts=body.start_ts, end_ts=body.end_ts, source="diary"))
    db.commit()
    return {"ok": True}


@app.get("/api/v1/businesses/{business_id}/reliability")
def reliability(b: Business = Depends(owned_business), db: Session = Depends(get_db)):
    """Scheduled versus measured grid availability by hour of day."""
    mask, _, _ = schedule.mask_for(b.schedule_id, b.region, b.group_id)
    m = measured_stats(db, b)
    av = m.get("availability_by_hour", {})
    hours = [{"hour": h, "scheduled_on": mask[h], "measured_availability": av.get(h)} for h in range(24)]
    gap = [h for h in range(24) if mask[h] and av.get(h) is not None and av[h] < 0.5]
    return {"hours_of_data": m.get("hours_of_data", 0), "hours": hours, "scheduled_on_but_measured_off": gap}


# ---------------- reports ----------------
@app.post("/api/v1/businesses/{business_id}/reports", status_code=201)
def make_report(body: AnalyseIn, b: Business = Depends(owned_business), u: User = Depends(current_user), db: Session = Depends(get_db)):
    res = run_analysis(db, b, body)
    default = asdict(eng.Assumptions(tariff=b.tariff, diesel_price=b.diesel_price))
    prov = {"schedule": "ZESCO dataset", "equipment": "owner", "assumptions_changed_by_owner": sorted(k for k, v in body.assumptions.items() if default.get(k) != v),
            "measured_data_used": bool(res.get("measured")), "price_defaults_are_placeholders": True}
    payload = {
        "report_version": 1, "engine_version": eng.ENGINE_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "business": {"sector": b.sector, "region": b.region, "group_id": b.group_id, "schedule_id": b.schedule_id,
                     "days_per_week": b.days_per_week, "existing_gen_kw": b.existing_gen_kw},
        "equipment": [{k: v for k, v in e.items() if k not in ("id", "channel")} for e in _business_out(b)["equipment"]],
        "assumptions": res["assumptions"], "provenance": prov, "schedule_warnings": res["schedule_warnings"],
        "supply_gap": res["supply_gap"], "measured": res["measured"], "recommended_id": res["recommended_id"],
        "strategies": [{k: s[k] for k in ("id", "label", "capex", "total_annual_cost", "running_cost", "unserved_loss", "payback_years",
                                           "diesel_litres_per_year", "co2_tonnes_per_year", "critical_ok", "sizing")} for s in res["strategies"]],
        "shift_plan": res["shift_plan"],
        "statement": "Projection based on the stated inputs and assumptions. Prices are defaults unless listed as changed. Not a guarantee of savings.",
    }
    canon, sig, key_id = reports.signer().sign(payload)
    rid = str(uuid.uuid4())
    db.add(Report(id=rid, business_id=b.id, created=int(time.time()), payload=canon, sig_hex=sig, key_id=key_id))
    db.commit()
    audit(db, u.email, "report_create", rid)
    return {"id": rid, "verify_url": f"/verify.html?id={rid}"}


@app.get("/api/v1/businesses/{business_id}/reports")
def list_reports(b: Business = Depends(owned_business), db: Session = Depends(get_db)):
    rows = db.execute(select(Report).where(Report.business_id == b.id).order_by(Report.created.desc())).scalars().all()
    return [{"id": r.id, "created": r.created} for r in rows]


@app.get("/api/v1/audit/verify")
def audit_verify(_: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    return verify_audit_chain(db)


# ---------------- frontend ----------------
_fe = Path(os.getenv("PP_FRONTEND", BASE.parent / "frontend"))
if _fe.exists():
    app.mount("/", StaticFiles(directory=_fe, html=True), name="frontend")
