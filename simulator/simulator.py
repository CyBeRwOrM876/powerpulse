#!/usr/bin/env python3
"""Hardware-free stand-in for the ESP32 node. Sends signed telemetry exactly like the firmware does.

Usage:
  python simulator/simulator.py --url http://localhost:8000 --device node-xxxx --secret <hex> \
      --region "Kitwe & Kalulushi" --group H --schedule ZESCO_14Oct2024 --speed 60

--speed N runs N simulated seconds per real second so a whole day of outages can be shown in minutes.
Optional --mqtt-host sends over MQTT instead of HTTP.
"""
import argparse, csv, hashlib, hmac, json, math, random, sys, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CSV = Path(__file__).resolve().parent.parent / "backend" / "data" / "zesco_rationing_schedules_clean.csv"


def canonical(d, seq, ts, v, pf, f, grid, ch):
    return f"{d}|{seq}|{ts}|{v:.1f}|{pf:.2f}|{f:.1f}|{grid}|{','.join(str(int(round(w))) for w in ch)}"


def sign(secret_hex, text):
    return hmac.new(bytes.fromhex(secret_hex), text.encode(), hashlib.sha256).hexdigest()


def windows(schedule, region, group):
    out = []
    with open(CSV, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["schedule_id"] == schedule and r["region"] == region and group.upper() in {p.strip().upper() for p in r["group_id"].replace(",", "&").split("&")}:
                s = int(r["on_start"][:2]) + int(r["on_start"][3:]) / 60
                e = int(r["on_end"][:2]) + int(r["on_end"][3:]) / 60
                out.append((s, e))
    return out


def grid_on(hour_f, wins):
    for s, e in wins:
        if e <= s:
            e += 24
        for sh in (0, 24):
            if s <= hour_f + sh < e:
                return True
    return False


def post(url, msg):
    req = urllib.request.Request(url.rstrip("/") + "/api/v1/ingest", data=json.dumps(msg).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())["status"]
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode()[:120]}"
    except Exception as e:
        return f"error: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--device", required=True)
    ap.add_argument("--secret", required=True)
    ap.add_argument("--region", required=True)
    ap.add_argument("--group", required=True)
    ap.add_argument("--schedule", default="ZESCO_14Oct2024")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--interval", type=float, default=2.0, help="real seconds between messages")
    ap.add_argument("--unscheduled", type=float, default=0.03, help="chance per simulated hour of an extra outage while grid is on")
    ap.add_argument("--hours", type=float, default=0, help="stop after this many simulated hours (0 = run forever)")
    ap.add_argument("--mqtt-host"); ap.add_argument("--mqtt-user"); ap.add_argument("--mqtt-pass")
    a = ap.parse_args()
    wins = windows(a.schedule, a.region, a.group)
    if not wins:
        sys.exit("No windows for that schedule, region and group")
    tz = ZoneInfo("Africa/Lusaka")
    client = None
    if a.mqtt_host:
        import paho.mqtt.client as mqtt
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if a.mqtt_user: client.username_pw_set(a.mqtt_user, a.mqtt_pass)
        client.connect(a.mqtt_host, 1883); client.loop_start()
    t_sim = time.time() - 300  # start slightly in the past so backlog-style timestamps are valid
    seq = int(time.time()) % 10**9
    extra_until = 0.0
    sim_end = t_sim + a.hours * 3600 if a.hours else None
    print(f"Simulating {a.device}: {len(wins)} window(s), speed x{a.speed}. Ctrl+C to stop.")
    while True:
        now_real = time.time()
        if t_sim > now_real + 200:          # never run ahead of the server clock: backend rejects future timestamps
            t_sim = now_real + 200
        dt = datetime.fromtimestamp(t_sim, tz)
        hf = dt.hour + dt.minute / 60
        on = grid_on(hf, wins)
        if on and t_sim > extra_until and random.random() < a.unscheduled * a.interval * a.speed / 3600:
            extra_until = t_sim + random.uniform(600, 2400)  # 10 to 40 minute unscheduled outage
        if t_sim < extra_until:
            on = False
        v = random.gauss(231, 3) if on else 0.0
        busy = 1.0 if 6 <= hf < 18 else 0.35
        ch = [0] * 6
        if on:
            ch = [max(0, int(random.gauss(4800 * busy, 200))), max(0, int(random.gauss(1100 * busy, 80))),
                  max(0, int(random.gauss(380, 25))), max(0, int(random.gauss(450 * busy, 40))), 0, 0]
        pf, f = (round(random.uniform(0.86, 0.95), 2), round(random.gauss(50, 0.05), 1)) if on else (0.0, 0.0)
        seq += 1
        msg = {"d": a.device, "seq": seq, "ts": int(t_sim), "v": round(v, 1), "pf": pf, "f": f, "grid": 1 if on else 0, "ch": ch}
        msg["sig"] = sign(a.secret, canonical(a.device, seq, msg["ts"], msg["v"], pf, f, msg["grid"], ch))
        if client:
            client.publish(f"powerpulse/{a.device}/telemetry", json.dumps(msg), qos=1); status = "mqtt"
        else:
            status = post(a.url, msg)
        print(f"{dt:%a %H:%M} grid={'ON ' if on else 'OFF'} load={sum(ch):5d} W  -> {status}")
        t_sim += a.interval * a.speed
        if sim_end and t_sim > sim_end:
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
