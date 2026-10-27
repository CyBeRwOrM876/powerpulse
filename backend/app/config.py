"""Runtime settings. Everything sensitive comes from environment variables."""
import os
import secrets
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("PP_DATA_DIR", BASE / "data"))
DATA.mkdir(parents=True, exist_ok=True)


def _persisted_secret(name: str) -> str:
    """Dev convenience: create a random secret once and reuse it across restarts."""
    p = DATA / name
    if p.exists():
        return p.read_text().strip()
    val = secrets.token_hex(32)
    p.write_text(val)
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return val


class Settings:
    env: str = os.getenv("PP_ENV", "dev")
    db_url: str = os.getenv("PP_DB_URL", f"sqlite:///{DATA / 'powerpulse.db'}")
    schedule_csv: Path = Path(os.getenv("PP_SCHEDULE_CSV", DATA / "zesco_rationing_schedules_clean.csv"))
    tz: str = os.getenv("PP_TZ", "Africa/Lusaka")
    jwt_minutes: int = int(os.getenv("PP_JWT_MINUTES", "120"))
    cors_origins: list[str] = [o for o in os.getenv("PP_CORS", "").split(",") if o]
    mqtt_host: str = os.getenv("PP_MQTT_HOST", "")
    mqtt_port: int = int(os.getenv("PP_MQTT_PORT", "8883"))
    mqtt_user: str = os.getenv("PP_MQTT_USER", "")
    mqtt_pass: str = os.getenv("PP_MQTT_PASS", "")
    mqtt_tls: bool = os.getenv("PP_MQTT_TLS", "1") == "1"
    mqtt_ca: str = os.getenv("PP_MQTT_CA", "")
    max_clock_skew_s: int = 300
    max_backlog_age_s: int = 7 * 86400

    @property
    def jwt_secret(self) -> str:
        s = os.getenv("PP_JWT_SECRET")
        if s:
            return s
        if self.env == "prod":
            raise RuntimeError("PP_JWT_SECRET must be set when PP_ENV=prod")
        return _persisted_secret(".jwt_secret")


settings = Settings()
