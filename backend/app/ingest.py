"""Telemetry ingestion shared by the HTTP endpoint and the MQTT subscriber."""
from __future__ import annotations
import hmac
import json
import logging
import threading
import time
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .config import settings
from .db import Device, Outage, Reading, SessionLocal
from . import alerts
from .db import Business, Equipment
from .security import device_canonical, device_sign

log = logging.getLogger("powerpulse.ingest")


class IngestError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code, self.msg = code, msg


def ingest(db: Session, msg: dict) -> dict:
    try:
        d, seq, ts = str(msg["d"]), int(msg["seq"]), int(msg["ts"])
        v, pf, f, grid = float(msg["v"]), float(msg["pf"]), float(msg["f"]), int(msg["grid"])
        ch = [int(round(float(x))) for x in msg["ch"]]
        sig = str(msg["sig"])
    except (KeyError, TypeError, ValueError):
        raise IngestError(422, "Malformed telemetry message")
    if not (0 < len(ch) <= 8) or grid not in (0, 1) or not (0 <= v <= 500) or not (0 <= pf <= 1.0) or not (0 <= f <= 70):
        raise IngestError(422, "Telemetry values out of range")
    if any(w < 0 or w > 100000 for w in ch):
        raise IngestError(422, "Channel values out of range")
    dev = db.execute(select(Device).where(Device.device_key == d)).scalar_one_or_none()
    if not dev:
        raise IngestError(401, "Unknown device")
    expect = device_sign(dev.secret_hex, device_canonical(d, seq, ts, v, pf, f, grid, ch))
    if not hmac.compare_digest(expect, sig):
        raise IngestError(401, "Bad signature")
    now = int(time.time())
    if ts > now + settings.max_clock_skew_s or ts < now - settings.max_backlog_age_s:
        raise IngestError(422, "Timestamp outside accepted window")
    if seq <= dev.last_seq:
        return {"status": "duplicate"}  # idempotent: a retried or replayed message is acknowledged but ignored
    db.add(Reading(device_id=dev.id, seq=seq, ts=ts, v=v, pf=pf, f=f, grid=grid, watts=",".join(map(str, ch))))
    _track_outage(db, dev, ts, grid)
    prev_grid = dev.last_grid
    dev.last_seq, dev.last_ts, dev.last_grid = seq, max(dev.last_ts, ts), grid
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"status": "duplicate"}
    _alert(db, dev, ts, grid, prev_grid, ch)
    return {"status": "stored"}


def _alert(db: Session, dev: Device, ts: int, grid: int, prev_grid: int, ch: list[int]):
    """Outage and overload notifications. Only for fresh data (older backlog records are history, not news)."""
    try:
        if time.time() - ts > 120:
            return
        b = db.get(Business, dev.business_id)
        if prev_grid == 1 and grid == 0:
            alerts.send(f"out{dev.id}", f"Power outage detected at {b.name}.", 60)
        elif prev_grid == 0 and grid == 1:
            alerts.send(f"back{dev.id}", f"Power restored at {b.name}.", 60)
        if grid == 1:
            for e in db.execute(select(Equipment).where(Equipment.business_id == b.id, Equipment.channel.is_not(None))).scalars():
                if e.channel < len(ch) and ch[e.channel] > 1.25 * e.power_kw * 1000:
                    alerts.send(f"over{dev.id}-{e.channel}", f"{b.name}: {e.name} is drawing {ch[e.channel]} W, above its {e.power_kw * 1000:.0f} W rating.")
    except Exception:
        log.exception("alert evaluation failed")


def _track_outage(db: Session, dev: Device, ts: int, grid: int):
    open_ev = db.execute(select(Outage).where(Outage.business_id == dev.business_id, Outage.source == "measured",
                                              Outage.end_ts.is_(None)).order_by(Outage.id.desc())).scalars().first()
    if grid == 0 and not open_ev:
        db.add(Outage(business_id=dev.business_id, start_ts=ts, source="measured"))
    elif grid == 1 and open_ev:
        open_ev.end_ts = ts


# ---------------- optional MQTT subscriber ----------------
def start_mqtt() -> None:
    if not settings.mqtt_host:
        log.info("MQTT disabled (PP_MQTT_HOST not set)")
        return
    import paho.mqtt.client as mqtt
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="powerpulse-backend")
    if settings.mqtt_user:
        client.username_pw_set(settings.mqtt_user, settings.mqtt_pass)
    if settings.mqtt_tls:
        client.tls_set(ca_certs=settings.mqtt_ca or None)

    def on_connect(c, userdata, flags, rc, props=None):
        log.info("MQTT connected rc=%s", rc)
        c.subscribe("powerpulse/+/telemetry", qos=1)

    def on_message(c, userdata, m):
        try:
            msg = json.loads(m.payload)
            topic_dev = m.topic.split("/")[1]
            if msg.get("d") != topic_dev:
                return  # topic and payload must agree
            with SessionLocal() as db:
                ingest(db, msg)
        except IngestError as e:
            log.warning("rejected telemetry: %s", e.msg)
        except Exception:
            log.exception("telemetry handler error")

    client.on_connect, client.on_message = on_connect, on_message
    client.reconnect_delay_set(1, 60)

    def run():
        while True:
            try:
                client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
                client.loop_forever(retry_first_connection=True)
            except Exception:
                log.exception("MQTT loop failed; retrying in 10 s")
                time.sleep(10)

    threading.Thread(target=run, name="mqtt", daemon=True).start()
