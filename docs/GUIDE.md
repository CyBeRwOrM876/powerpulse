# PowerPulse build guide (team ZedWatts)

This guide takes you from an empty laptop to a working system: decision engine, secure backend, dashboard, device simulator, and the ESP32 measurement node. Do the steps in order. Each step ends with a check that tells you whether to move on.

You can build it two ways. **Option A:** use the code in this repository as it is, and use GitHub Copilot in VS Code to modify and extend it. **Option B:** ask Claude for any file you want changed. The repository already contains working code for everything except the physical wiring.

---------------------------------------------------------------------

## 0. What is tested and what is not

| Part | Status |
|---|---|
| Schedule lookup, decision engine, quote checker, sensitivity | Tested (30 automated tests) |
| API, authentication, access control, signed reports, tamper test | Tested |
| Device signatures, replay protection, outage tracking, alerts | Tested |
| Dashboard and verification page | Run against the live API; click through it yourself |
| Simulator | Run against the live API |
| **ESP32 firmware and wiring** | **Not run on hardware.** Treat the bench test (Step 5) as mandatory |
| Mosquitto and TLS deployment | Configuration provided, not exercised here |

All default prices (batteries, solar, diesel, tariff) are **placeholders**. Replace them with current supplier quotations before you show any figure to a judge.

---------------------------------------------------------------------

## 1. Set up your tools

1. Install VS Code, Python 3.12, Git, Docker Desktop (optional until Step 3) and the Arduino IDE 2.x (for Step 5).
2. In VS Code install: Python, Pylance, GitHub Copilot, GitHub Copilot Chat, Docker, and "Arduino Community Edition" or use the Arduino IDE.
3. Create a GitHub repository, copy this folder into it, and push. The file `.github/copilot-instructions.md` tells Copilot how the project works. Check it is picked up: open Copilot Chat and ask "What does engine.py do and what rules apply to it?"
4. Agree roles: **Cybersecurity** owns `security.py`, `reports.py`, `ingest.py`, Mosquitto, deployment. **Data Science** owns `engine.py`, templates, calibration. **ICT** owns `main.py`, `frontend/`, firmware and wiring. Everyone reviews each other's pull requests.

Check: `git log` shows your first commit and Copilot Chat can describe the project.

---------------------------------------------------------------------

## 2. Run the backend and the dashboard (no hardware)

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
cd backend
python -m pytest -q                  # expect: 30 passed
uvicorn app.main:app --reload
```
Open http://localhost:8000 and http://localhost:8000/api/docs.

1. Create an account (password: 10 or more characters, mixed case, with a digit).
2. Business tab: choose a template (for example Bakery), pick the schedule, region and group. The 24 boxes show hours with grid power (black). Edit the equipment so it matches a real business.
3. Save, then open Analysis and click **Run analysis**.
4. Read the result: recommended strategy, ranked table, backup priority, suggested timetable for movable equipment, sensitivity, and the quotation checker.
5. Click **Generate signed report** and open the verification page. It should say "Signature valid".

Check: you can explain why the recommendation changes when you change the group (try another group in the same region).

**Copilot prompt ideas**
- "Add an endpoint that returns the weekly timetable as CSV for download."
- "Add a per-weekday operating pattern to Equipment and update `simulate()` and the tests."

---------------------------------------------------------------------

## 3. Simulate a node and the MQTT path

1. In the dashboard Live tab click **Register a node**. Copy the device id and secret (shown once).
2. Run the simulator, using the same region and group as your business:
```bash
python simulator/simulator.py --url http://localhost:8000 --device node-xxxx --secret <hex> \
  --region "Kitwe & Kalulushi" --group H --schedule ZESCO_14Oct2024 --speed 600
```
`--speed 600` plays ten simulated minutes per real second, so a full day of scheduled and random unscheduled outages appears in about 2.5 minutes.
3. Refresh the Live tab. You should see circuit watts, a 24-hour chart, and outages with start, end and minutes.
4. After about 24 simulated hours, tick **Use measured data when available** in Analysis. The engine now uses the measured grid availability per hour and the measured mean power of each circuit that is mapped to equipment (CT channel field).
5. To test the MQTT route: `cp .env.example .env`, create the broker passwords with `scripts/make_mosquitto_passwd.sh <backend-pass> <node-pass>`, put the backend password in `.env`, then `docker compose up --build`. Run the simulator with `--mqtt-host localhost --mqtt-user node --mqtt-pass <node-pass>`.

Check: outages recorded by the simulator match the outage log, and the reliability panel shows hours where the schedule said "on" but power was off.

**Attack tests to run (for the security demonstration)**
- Change one watt value in a captured message and resend it: expect 401.
- Resend a message with the same sequence number: expect `duplicate`.
- Use a secret from another node: expect 401.
- Edit a number in a signed report and paste it into the verification endpoint: expect `valid: false`.

---------------------------------------------------------------------

## 4. Build the hardware

### 4.1 Parts
- 1 x ESP32 DevKit (30-pin or 38-pin)
- 2 x ADS1115 16-bit ADC boards
- 6 x SCT-013-000 split-core current transformers (100 A : 50 mA, current output, no built-in burden)
- 6 x 18 ohm 1% metal-film resistors (burden), 2 x 10 kohm and 1 x 10 microfarad capacitor (shared 1.65 V bias)
- 6 x 3.5 mm audio jack breakouts (optional but makes clamps removable)
- 1 x PZEM-004T v3 with split-core CT (voltage, power factor, frequency; also gives whole-house reference power)
- 1 x power bank or 18650 UPS board that can power the ESP32 while it charges (pass-through), so the node keeps running during an outage
- Optional: DS3231 RTC (accurate time after a reboot with no internet), an enclosure, a 2 A fuse or small MCB for the PZEM voltage tap, ferrules and terminals

### 4.2 Safety (read before touching a board)
- Clamping a CT around **one insulated conductor** is non-invasive and safe. Never clamp both live and neutral together (you will read zero).
- The **PZEM-004T connects to live mains voltage**. Have a qualified electrician install it, with a fuse or small MCB, inside a closed enclosure. Do not work on a live board.
- Never leave a current-output CT connected to a circuit with the burden resistor missing: open-circuit, it can generate dangerous voltages. Fit the burden first, clamp second.
- Keep mains wiring and the ESP32 side physically separated. The PZEM serial side is opto-isolated; keep it that way.
- Do the build and calibration on a bench with a plug-in load (kettle or heater) before any installation.

### 4.3 Wiring
```
3.3V ---[10k]---+---[10k]--- GND         (bias node = 1.65 V)    10 uF from bias node to GND
                |
   each CT:  one terminal -> bias node
             other terminal -> ADS input AINx
             18 ohm burden resistor across the two CT terminals

ADS1115 #1 (addr 0x48, ADDR->GND): AIN0, AIN1, AIN2 = circuits 1, 2, 3
ADS1115 #2 (addr 0x49, ADDR->VDD): AIN0, AIN1, AIN2 = circuits 4, 5, 6
Both ADS: VDD 3.3 V, GND, SDA -> GPIO21, SCL -> GPIO22
PZEM-004T (TTL side): 5 V, GND, TX -> GPIO16 (RX2), RX -> GPIO17 (TX2)
ESP32 power: from the power bank, not from the mains-powered USB adapter
```
Why the ADS1115 and not the ESP32 ADC: the ESP32 ADC is non-linear near the rails and conflicts with Wi-Fi. At gain 1 (+/-4.096 V) one count is 0.125 mV, which is about 14 mA of primary current per count with an 18 ohm burden. A 100 A reading gives about 1.27 V peak on the 1.65 V bias, which stays inside the 0 to 3.3 V input range.

### 4.4 Why the node needs its own power and a backlog
During load shedding the Wi-Fi router is usually off as well. The node therefore (a) runs from a battery, (b) detects the outage when the PZEM stops answering, (c) writes records to flash, and (d) uploads them with their original timestamps when the network returns. That is how real outages reach the dashboard.

---------------------------------------------------------------------

## 5. Flash the firmware and run the bench test

1. Arduino IDE: install the ESP32 board package (core 2.0.x or 3.x). Library Manager: **Adafruit ADS1X15**, **PZEM004Tv30** (mandulaj), **PubSubClient**, and **RTClib** only if you fit a DS3231.
2. Copy `firmware/powerpulse_node/config.h.example` to `config.h`. Fill in Wi-Fi, broker host, user and password, the **device id and secret from the dashboard**, and the CA certificate (for Let's Encrypt, the ISRG Root X1 PEM). Never commit `config.h`; it is in `.gitignore`.
3. Select "ESP32 Dev Module", upload, open the Serial Monitor at 115200.
4. **Bench test** with a resistive load of known power (a 2 kW heater or kettle) on one clamped wire:
   - Type `show`. Compare the amps with a plug-in power meter.
   - If it differs, type `cal 0 8.7` (channel 0, true amps) to store a correction. Repeat per channel. The factor is saved in flash.
   - Unplug the load: the channel should read 0.00 A (noise floor 0.05 A).
   - Compare the sum of circuit apparent power with the PZEM total. Expect agreement within a few percent; investigate if not.
5. Power-cut test: pull the mains plug on the PZEM side while the node runs from the power bank. After three failed PZEM reads the node records `grid=0`. Restore mains and Wi-Fi: the dashboard should show an outage with the right start and end.

Known limits (state them in your pitch): per-circuit values are **apparent power** (Vrms x Irms), with true voltage, power factor and frequency from the PZEM; each CT is about 1% class, so calibration against a reference matters; each channel pair is sampled for 400 ms (20 cycles) in turn, so each circuit is refreshed about every 1.2 s.

If readings look wrong: check the burden resistor, the bias voltage (1.65 V), that the clamp surrounds a single conductor and faces the right way (arrow toward the load), the ADS addresses, and the PZEM wiring.

---------------------------------------------------------------------

## 6. Make the data useful for decisions

1. Map each circuit to equipment: in the equipment table set **CT channel** (0 to 5) for the equipment on that wire.
2. Leave the node running for at least 24 hours. Open Analysis, tick **Use measured data**, and run. The report will say that measured data was used.
3. Replace the placeholder prices in Analysis with supplier quotations (battery per kWh, solar per kWp, inverter per kW, generator per kW, diesel, tariff). Record the date and supplier in your notes.
4. Interview three to five owners (guide in the proposal, Appendix B). Use their real revenue per hour and spoilage figures.
5. Use the **quotation checker** on a real supplier quotation. The goal is to show where a question is worth asking, not to accuse anyone.

---------------------------------------------------------------------

## 7. Deploy

1. Rent a small VPS. Install Docker. Point a domain at it.
2. Put **Caddy** in front of the API for automatic HTTPS (a two-line Caddyfile: `yourdomain { reverse_proxy api:8000 }`). Add it to `docker-compose.yml`.
3. Mosquitto: enable the TLS listener in `mosquitto/mosquitto.conf`, mount your certificates, and **close port 1883** in the firewall. Set `PP_MQTT_TLS=1` and `PP_MQTT_PORT=8883`.
4. Environment: `PP_ENV=prod`, `PP_JWT_SECRET=$(openssl rand -hex 32)`, and a signing key provided through `PP_SIGNING_KEY_PEM` or a protected volume. Keep a backup of the signing key: losing it means old reports can no longer be verified with the new key.
5. Switch the database to PostgreSQL by setting `PP_DB_URL=postgresql+psycopg://...` and adding `psycopg[binary]` to `requirements.txt`.
6. Back up `/data` daily. Test restoring it once.

---------------------------------------------------------------------

## 8. Security checklist (Cybersecurity lead)

- [ ] `PP_ENV=prod`, no default secrets, API docs disabled.
- [ ] HTTPS everywhere, HSTS set (done by the app in prod). MQTT only over TLS.
- [ ] Mosquitto ACL: nodes can write only to their own topics; only the backend reads.
- [ ] Each device has its own secret. Rotate a secret by deleting and re-registering the device.
- [ ] Signing key outside the repository, permissions 600, backed up.
- [ ] `GET /api/v1/audit/verify` returns `ok: true` (admin account created with `scripts/create_user.py`).
- [ ] Rate limits behind a reverse proxy use the real client IP (`--proxy-headers` is set). For several server processes move the limiter to Redis.
- [ ] No personal data in reports (check a sample).
- [ ] Dependency scan: `pip install pip-audit && pip-audit -r backend/requirements.txt`.
- [ ] Run OWASP ZAP baseline against the staging URL and fix findings.
- [ ] Data Protection Act, 2021: record what you collect, why, and how long you keep it. Offer deletion (the delete endpoint exists).
- [ ] Known gaps to disclose: device secrets are stored in the database in plain form (encrypt with a KMS in production); the audit chain is tamper-evident but not tamper-proof against someone with database write access and the ability to rebuild the chain.

---------------------------------------------------------------------

## 9. The 48-hour plan

| Hours | Cybersecurity | Data Science | ICT |
|---|---|---|---|
| Before | Broker and TLS plan, threat table, review `security.py` | Real prices, interviews, check engine on three real profiles | Order parts, bench-build node, run the stack |
| 0 to 8 | Auth, roles, audit chain review | Check engine outputs by hand for one profile | Dashboard flow, templates |
| 8 to 24 | Device signing, replay tests, attack demo script | Sensitivity, measured-data path | Node on the bench, live panel, timetable view |
| 24 to 36 | Report signing and verification page polish | Calibrate with real readings | Offline use, mobile layout |
| 36 to 44 | Tamper demonstration, ZAP scan, fix | Final numbers and assumptions list | Bug fixes, demo data |
| 44 to 48 | Rehearse pitch, prepare Q and A on security | Prepare Q and A on method and limits | Record a backup video of the demo |

**Demo script (5 minutes):** (1) a bakery's loss under its group schedule; (2) the ranking and the recommendation; (3) change the group or the diesel price and show the answer change; (4) live node with a real outage log; (5) generate a signed report and verify it; (6) alter one figure and show verification fail; (7) paste a supplier quotation into the checker.

**Questions judges may ask:** where the prices come from (quotations, dated); how loss per hour was estimated (owner figures, editable); why not forecast outages (no validated outage record, schedules are published); what the signature proves (integrity and origin, not truth of inputs); who pays (hypotheses: verified reports for lenders, installer subscriptions, programme partnerships).

---------------------------------------------------------------------

## 10. Copilot workflow that works

1. Open the file you want to change and the matching test file side by side.
2. Ask Copilot Chat for the change **and** the test: "Add X to engine.py and a test in test_schedule_engine.py that fails without it."
3. Run `python -m pytest -q`. Do not accept a change that you cannot explain.
4. Ask for a review before you commit: "Review this diff for security problems and for places where it breaks the rules in copilot-instructions.md."
5. Small commits, one pull request per feature, and a different team member reviews.

Good first tasks: weekly operating patterns per equipment; true-power channels using a voltage reference; USSD or SMS summary for feature-phone users; a market-level view for associations; PostgreSQL migration with Alembic; Playwright end-to-end tests for the dashboard.

---------------------------------------------------------------------

## 11. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401 Bad signature` from the node | Wrong secret, or number formatting differs. Print the canonical string on the serial port and compare with `device_canonical` |
| `422 Timestamp outside accepted window` | Node clock wrong (no NTP and no RTC). Fit a DS3231 |
| Node shows online but no outages | PZEM still powered by a UPS on the mains side; it must lose power with the grid |
| Channel reads half or double | Clamp around two conductors, wrong burden value, or calibration not run |
| Analysis says "No strategy keeps critical equipment running" | Critical load exceeds the battery or generator limits in Assumptions; raise `max_batt_kwh` or review the equipment list |
| Dashboard blank, console CSP error | An inline script or external resource was added; the policy allows only files served from this site |
