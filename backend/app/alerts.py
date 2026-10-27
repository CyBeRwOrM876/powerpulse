"""Optional Telegram alerts for outages and sustained circuit overloads. Silent when not configured."""
from __future__ import annotations
import json
import logging
import os
import threading
import time
import urllib.request

log = logging.getLogger("powerpulse.alerts")
TOKEN = os.getenv("PP_TELEGRAM_TOKEN", "")
CHAT = os.getenv("PP_TELEGRAM_CHAT", "")
_last: dict[str, float] = {}
COOLDOWN_S = 900


def _post(text: str):
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                                     data=json.dumps({"chat_id": CHAT, "text": text}).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # never log the URL: it contains the token
        log.warning("Telegram send failed: %s", type(e).__name__)


def send(key: str, text: str, cooldown: int = COOLDOWN_S) -> bool:
    """Returns True if the alert was (or would be) sent. Rate-limited per key."""
    now = time.time()
    if now - _last.get(key, 0) < cooldown:
        return False
    _last[key] = now
    if TOKEN and CHAT:
        threading.Thread(target=_post, args=(text,), daemon=True).start()
    return True
