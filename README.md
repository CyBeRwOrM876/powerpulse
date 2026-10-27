# PowerPulse by team ZedWatts

Secure energy-resilience decision system for Zambian businesses. Combines:

1. **Decision engine** that compares current practice, diesel, battery, solar plus battery and shifted working hours (and combinations) for a ZESCO group schedule, with payback, emissions, a critical-load priority order, a weekly timetable, sensitivity analysis and a quotation checker.
2. **Measurement node** (ESP32 + six SCT-013-000 CT clamps + two ADS1115 + PZEM-004T) that streams signed telemetry, detects real outages and feeds the engine with measured load and measured grid availability.
3. **Signed reports** (Ed25519 over canonical JSON) with a public verification page.
4. **Security**: JWT auth with roles, object-level authorisation, scrypt passwords, HMAC-signed device messages with replay protection, hash-chained audit log, rate limits, strict CSP.

```
CT clamps -> ADS1115 -> ESP32 --(MQTT/TLS or HTTPS, HMAC signed)--> API -> SQLite/PostgreSQL
PZEM-004T ----------->                                              |-> decision engine -> dashboard
                                                                    |-> signed reports -> /verify.html
```

## Quick start (no hardware)
```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
cp backend/data/zesco_rationing_schedules_clean.csv backend/data/   # already present
cd backend && python -m pytest -q                         # 30 tests
uvicorn app.main:app --reload                             # http://localhost:8000
```
Open http://localhost:8000, create an account, choose a template, save, run the analysis. Then register a node in the Live tab and run:
```bash
python simulator/simulator.py --device <id> --secret <hex> --region "Kitwe & Kalulushi" --group H --speed 600
```
Full build instructions, wiring, safety, deployment and the 48-hour plan are in **docs/GUIDE.md**.

## Honest status
Backend, engine, API, frontend and simulator are tested here (see `backend/tests`). The firmware compiles in principle but has **not** been run on hardware in this repository: follow the bench test in the guide. All default prices are placeholders.
