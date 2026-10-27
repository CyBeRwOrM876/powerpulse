# Instructions for GitHub Copilot (PowerPulse by team ZedWatts)

PowerPulse helps Zambian businesses choose between diesel, battery, solar-battery and shifting work hours under ZESCO load shedding, using published group schedules and measured data from an ESP32 CT-sensor node.

## Layout
- `backend/app/engine.py`: pure decision engine. No I/O. Every function must stay deterministic and covered by tests.
- `backend/app/schedule.py`: ZESCO schedule dataset lookups. Windows may cross midnight. Group ids may be composite (for example "F&G").
- `backend/app/security.py`, `reports.py`, `ingest.py`: auth, signing, telemetry. Treat as security-critical.
- `backend/app/main.py`: FastAPI routes. Use pydantic models with bounds for every input.
- `frontend/`: dependency-free JavaScript. No inline scripts (the CSP forbids them). Build DOM with `textContent`, never `innerHTML` with data.
- `firmware/powerpulse_node/`: ESP32 sketch. The canonical string it signs must match `device_canonical` in `security.py` exactly.
- `simulator/simulator.py`: hardware-free node.

## Rules
1. Run `cd backend && python -m pytest -q` before proposing a change is finished. Add a test for every behaviour change.
2. Never hard-code secrets. Settings come from environment variables (`config.py`).
3. Every route that touches a business must use the `owned_business` dependency. Return 404, not 403, for other people's objects.
4. Money values are ZMW and the default prices are placeholders. Do not present them as facts. Anything shown to the user must state assumptions.
5. Do not add forecasting or "AI" claims. The engine uses transparent arithmetic and a small exhaustive search.
6. Keep reports free of personal data (no business name, no meter number, no email).
7. Floats in signed payloads are rounded to 2 decimals before canonicalisation. Do not change this without updating the verification page and tests.
8. Style: Python 3.12, type hints, small functions, docstrings that say why. Comments in plain English.
