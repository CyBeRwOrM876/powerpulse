import time
from app.security import device_canonical, device_sign

BODY = {"name": "Test Bakery", "sector": "bakery", "region": "Kitwe & Kalulushi", "group_id": "H", "schedule_id": "ZESCO_14Oct2024",
        "days_per_week": 6, "tariff": 1.8, "diesel_price": 30, "existing_gen_kw": 0,
        "equipment": [{"name": "Oven", "power_kw": 6, "hours_per_day": 8, "start_hour": 4, "critical": True, "flexible": False, "value_per_hour": 250, "spoil_per_hour": 0, "channel": 0},
                      {"name": "Mixer", "power_kw": 1.5, "hours_per_day": 3, "start_hour": 3, "critical": False, "flexible": True, "value_per_hour": 80, "spoil_per_hour": 0, "channel": 1}]}


def test_requires_auth(client):
    assert client.get("/api/v1/businesses").status_code == 401


def test_weak_password_rejected(client):
    assert client.post("/api/v1/auth/register", json={"email": "x@example.com", "password": "short"}).status_code == 422


def test_full_flow(client, auth):
    r = client.post("/api/v1/businesses", json=BODY, headers=auth)
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    a = client.post(f"/api/v1/businesses/{bid}/analyse", json={}, headers=auth)
    assert a.status_code == 200 and a.json()["recommended_id"]
    rep = client.post(f"/api/v1/businesses/{bid}/reports", json={}, headers=auth)
    assert rep.status_code == 201
    rid = rep.json()["id"]
    pub = client.get(f"/api/v1/public/reports/{rid}")
    assert pub.status_code == 200 and pub.json()["valid"] is True
    # tamper test
    tampered = pub.json()["canonical"].replace('"days_per_week":6', '"days_per_week":1')
    v = client.post("/api/v1/verify", json={"canonical": tampered, "signature": pub.json()["signature"]})
    assert v.json()["valid"] is False
    # business name must not appear in a public report
    assert "Test Bakery" not in pub.json()["canonical"]


def test_other_user_cannot_read_business(client, auth):
    bid = client.post("/api/v1/businesses", json=BODY, headers=auth).json()["id"]
    r = client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "Another-Pass-456"})
    other = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get(f"/api/v1/businesses/{bid}", headers=other).status_code == 404
    assert client.post(f"/api/v1/businesses/{bid}/analyse", json={}, headers=other).status_code == 404


def test_invalid_group_rejected(client, auth):
    bad = {**BODY, "group_id": "ZZ"}
    assert client.post("/api/v1/businesses", json=bad, headers=auth).status_code == 422


def test_unknown_assumption_rejected(client, auth):
    bid = client.post("/api/v1/businesses", json=BODY, headers=auth).json()["id"]
    r = client.post(f"/api/v1/businesses/{bid}/analyse", json={"assumptions": {"evil": 1}}, headers=auth)
    assert r.status_code == 422


def _msg(dev, secret, seq, ts, grid, ch=(1500, 300, 0, 0, 0, 0), v=230.0):
    sig = device_sign(secret, device_canonical(dev, seq, ts, v, 0.9, 50.0, grid, list(ch)))
    return {"d": dev, "seq": seq, "ts": ts, "v": v, "pf": 0.9, "f": 50.0, "grid": grid, "ch": list(ch), "sig": sig}


def test_ingest_signature_replay_and_outage_tracking(client, auth):
    bid = client.post("/api/v1/businesses", json=BODY, headers=auth).json()["id"]
    d = client.post(f"/api/v1/businesses/{bid}/devices", headers=auth).json()
    dev, sec = d["device_key"], d["secret_hex"]
    now = int(time.time())
    assert client.post("/api/v1/ingest", json=_msg(dev, sec, 1, now - 300, 1)).json()["status"] == "stored"
    assert client.post("/api/v1/ingest", json=_msg(dev, sec, 1, now - 300, 1)).json()["status"] == "duplicate"  # replay ignored
    bad = _msg(dev, sec, 2, now - 240, 1); bad["ch"][0] = 9999
    assert client.post("/api/v1/ingest", json=bad).status_code == 401  # tampered payload
    assert client.post("/api/v1/ingest", json=_msg(dev, "00" * 32, 3, now - 200, 1)).status_code == 401  # wrong secret
    assert client.post("/api/v1/ingest", json=_msg(dev, sec, 4, now + 99999, 1)).status_code == 422  # clock in future
    assert client.post("/api/v1/ingest", json=_msg(dev, sec, 5, now - 180, 0, ch=(0,) * 6, v=0.0)).json()["status"] == "stored"
    assert client.post("/api/v1/ingest", json=_msg(dev, sec, 6, now - 60, 1)).json()["status"] == "stored"
    out = client.get(f"/api/v1/businesses/{bid}/outages", headers=auth).json()
    meas = [o for o in out if o["source"] == "measured"]
    assert len(meas) == 1 and meas[0]["minutes"] == 2.0
    live = client.get(f"/api/v1/businesses/{bid}/live", headers=auth).json()
    assert live[0]["latest"]["watts"][0] == 1500


def test_unknown_device_rejected(client):
    assert client.post("/api/v1/ingest", json=_msg("node-nope", "11" * 32, 1, int(time.time()), 1)).status_code == 401


def test_audit_chain_intact(client, auth):
    from app.db import SessionLocal, User
    from app.security import verify_audit_chain
    with SessionLocal() as db:
        assert verify_audit_chain(db)["ok"]


def test_outage_alert_is_sent_once(client, auth, monkeypatch):
    from app import alerts
    sent = []
    monkeypatch.setattr(alerts, "TOKEN", ""); monkeypatch.setattr(alerts, "_last", {})
    orig = alerts.send
    monkeypatch.setattr("app.ingest.alerts.send", lambda key, text, cooldown=900: sent.append((key, text)) or True)
    bid = client.post("/api/v1/businesses", json=BODY, headers=auth).json()["id"]
    d = client.post(f"/api/v1/businesses/{bid}/devices", headers=auth).json()
    dev, sec = d["device_key"], d["secret_hex"]
    now = int(time.time())
    client.post("/api/v1/ingest", json=_msg(dev, sec, 1, now - 20, 1))
    client.post("/api/v1/ingest", json=_msg(dev, sec, 2, now - 10, 0, ch=(0,) * 6, v=0.0))
    client.post("/api/v1/ingest", json=_msg(dev, sec, 3, now - 5, 1, ch=(9000, 0, 0, 0, 0, 0)))
    texts = " ".join(t for _, t in sent)
    assert "outage" in texts and "restored" in texts and "above its" in texts
