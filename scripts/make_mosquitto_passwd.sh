#!/usr/bin/env bash
# Creates mosquitto/passwd with the two accounts. Usage: ./scripts/make_mosquitto_passwd.sh <backend-password> <node-password>
set -euo pipefail
cd "$(dirname "$0")/.."
: > mosquitto/passwd
docker run --rm -v "$PWD/mosquitto:/m" eclipse-mosquitto:2 sh -c "mosquitto_passwd -b -c /m/passwd backend '$1' && mosquitto_passwd -b /m/passwd node '$2'"
echo "Wrote mosquitto/passwd. Put the same backend password in .env as PP_MQTT_PASS."
