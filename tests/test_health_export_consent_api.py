import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.settings import settings
from app.main import app
from app.models.health import HealthSource
from app.models.user import User
from conftest import LEGAL_ACCEPTANCE


client = TestClient(app)


def test_normalized_legacy_sources_require_fresh_fenced_export_opt_in(monkeypatch):
    monkeypatch.setattr(settings, "HEALTH_COLLECTION_ENABLED", True)
    response = client.post("/v1/auth/signup", json={
        "email": f"health-consent-{uuid.uuid4().hex}@example.com",
        "password": "StrongPass123",
        "preferred_name": "Health Consent",
        "legalAcceptance": LEGAL_ACCEPTANCE,
    })
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    user_id = client.get("/v1/users/me", headers=headers).json()["id"]
    try:
        connected_at = datetime.now(timezone.utc)
        with SessionLocal() as db:
            db.add_all([
                HealthSource(user_id=user_id, source="apple_health", connection_revision=3,
                             enabled=True, read_enabled=True, export_enabled=False, export_enabled_at=None,
                             connected_at=connected_at),
                HealthSource(user_id=user_id, source="health_connect", connection_revision=5,
                             enabled=False, read_enabled=False, export_enabled=False, export_enabled_at=None,
                             connected_at=connected_at),
            ])
            db.commit()

        state = client.get("/v1/users/me/health", headers=headers).json()
        assert state["sources"][0]["exportEnabled"] is False
        assert state["sources"][0]["exportEnabledAt"] is None
        assert state["sourceRevisions"] == {"apple_health": 3, "health_connect": 5}

        stale = client.post("/v1/users/me/health/sources", headers=headers, json={
            "source": "apple_health", "readEnabled": True, "exportEnabled": True,
            "expectedConnectionRevision": 2,
        })
        assert stale.status_code == 409
        current = client.post("/v1/users/me/health/sources", headers=headers, json={
            "source": "apple_health", "readEnabled": True, "exportEnabled": True,
            "expectedConnectionRevision": 3,
        })
        assert current.status_code == 200, current.text
        assert current.json()["connectionRevision"] == 4
        assert current.json()["exportEnabledAt"] is not None

        stale_hidden = client.post("/v1/users/me/health/sources", headers=headers, json={
            "source": "health_connect", "readEnabled": False, "exportEnabled": True,
            "expectedConnectionRevision": 4,
        })
        assert stale_hidden.status_code == 409
        current_hidden = client.post("/v1/users/me/health/sources", headers=headers, json={
            "source": "health_connect", "readEnabled": False, "exportEnabled": True,
            "expectedConnectionRevision": 5,
        })
        assert current_hidden.status_code == 200, current_hidden.text
        assert current_hidden.json()["exportEnabledAt"] is not None
    finally:
        with SessionLocal() as db:
            db.query(User).filter_by(id=user_id).delete(synchronize_session=False)
            db.commit()
