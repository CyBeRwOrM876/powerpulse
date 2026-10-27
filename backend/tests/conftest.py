import os, sys, tempfile
from pathlib import Path
tmp = tempfile.mkdtemp()
os.environ["PP_DATA_DIR"] = tmp
os.environ["PP_DB_URL"] = f"sqlite:///{tmp}/test.db"
os.environ["PP_SCHEDULE_CSV"] = str(Path(__file__).resolve().parent.parent / "data" / "zesco_rationing_schedules_clean.csv")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
from fastapi.testclient import TestClient

@pytest.fixture(scope="session")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c

@pytest.fixture(scope="session")
def auth(client):
    r = client.post("/api/v1/auth/register", json={"email": "owner@example.com", "password": "Strong-Pass-123"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}
