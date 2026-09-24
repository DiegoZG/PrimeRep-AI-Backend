from datetime import date
from io import BytesIO
import uuid

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app.core.database import SessionLocal
from app.core.profile_service import MAX_AVATAR_UPLOAD_BYTES
from app.main import app
from app.models.onboarding_profile import OnboardingProfile
from app.models.user_avatar import UserAvatar
from app.models.workout_week_plan import WorkoutWeekPlan
from conftest import LEGAL_ACCEPTANCE


client = TestClient(app)


def signup(onboarding=None):
    result = client.post("/v1/auth/signup", json={
        "email": f"profile-{uuid.uuid4().hex}@example.com",
        "password": "StrongPass123", "preferred_name": "Original", "last_name": "Name",
        "legalAcceptance": LEGAL_ACCEPTANCE, "onboarding": onboarding,
    })
    assert result.status_code == 201, result.text
    headers = {"Authorization": f"Bearer {result.json()['access_token']}"}
    user = client.get("/v1/users/me", headers=headers).json()
    return user["id"], headers


def photo(format="PNG", size=(900, 600), color="red"):
    image = Image.new("RGB", size, color)
    output = BytesIO()
    exif = Image.Exif()
    exif[270] = "private metadata"
    exif[274] = 6
    image.save(output, format=format, exif=exif)
    return output.getvalue()


def test_profile_patch_is_partial_canonical_and_does_not_rebuild_plans():
    user_id, headers = signup({"weight": 180, "weightUnit": "LB", "age": 30, "gender": "Male", "fitnessGoal": "get-stronger"})
    with SessionLocal() as db:
        db.add(WorkoutWeekPlan(user_id=user_id, week_start_date=date(2026, 9, 21), days_per_week=3, plan_json={"workouts": [{"id": "stable"}]}))
        db.commit()
    before = client.get("/v1/users/me/profile", headers=headers).json()
    updated = client.patch("/v1/users/me/profile", headers=headers, json={"preferred_name": "  Diego  ", "last_name": "  ", "weight": 80, "weight_unit": "KG"})
    assert updated.status_code == 200, updated.text
    result = updated.json()
    assert result["id"] == user_id
    assert result["preferred_name"] == "Diego"
    assert result["last_name"] is None
    assert (result["weight"], result["weight_unit"], result["age"], result["gender"]) == (80, "KG", 30, "Male")
    assert result["email"] == before["email"]
    assert result["updated_at"] >= before["updated_at"]
    assert client.get("/v1/users/me", headers=headers).json()["preferred_name"] == "Diego"
    onboard = client.get("/v1/onboarding/me", headers=headers).json()["data"]
    assert onboard["preferredName"] == "Diego"
    assert onboard["weight"] == 80
    assert onboard["weightUnit"] == "KG"
    with SessionLocal() as db:
        plan = db.query(WorkoutWeekPlan).filter_by(user_id=user_id).one()
        assert plan.plan_json == {"workouts": [{"id": "stable"}]}


def test_legacy_onboarding_cannot_restore_individually_edited_body_fields():
    _, headers = signup({"weight": 180, "weightUnit": "LB", "age": 30, "gender": "Male"})
    assert client.patch("/v1/users/me/profile", headers=headers, json={"weight": 80, "weight_unit": "KG", "age": None, "preferred_name": "New Name"}).status_code == 200
    stale = {"preferredName": "Old", "email": "wrong@example.com", "weight": 190, "weightUnit": "LB", "age": 31, "gender": "Female", "fitnessGoal": "build-muscle"}
    response = client.post("/v1/onboarding/me", headers=headers, json={"data": stale})
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["preferredName"] == "New Name"
    assert body["email"] != "wrong@example.com"
    assert (body["weight"], body["weightUnit"], body["age"]) == (80, "KG", None)
    assert body["gender"] == "Female"
    assert body["fitnessGoal"] == "build-muscle"
    assert "profile_managed_fields" not in response.json()


def test_optional_fields_clear_and_name_only_edits_keep_onboarding_body_editable():
    _, headers = signup({"age": 30, "gender": "Male", "weight": 180, "weightUnit": "LB"})
    assert client.patch("/v1/users/me/profile", headers=headers, json={"preferred_name": "New"}).status_code == 200
    assert client.post("/v1/onboarding/me", headers=headers, json={"data": {"age": 31}}).status_code == 200
    assert client.get("/v1/users/me/profile", headers=headers).json()["age"] == 31
    result = client.patch("/v1/users/me/profile", headers=headers, json={"age": None, "gender": None, "weight": None, "last_name": None})
    assert result.status_code == 200
    assert all(result.json()[field] is None for field in ("age", "gender", "weight", "last_name"))


def test_weight_conversion_and_unit_only_patch_preserve_physical_weight():
    _, headers = signup({"weight": 220.46226218487757, "weightUnit": "LB"})
    displayed = client.get("/v1/users/me/profile?weight_unit=KG", headers=headers)
    assert displayed.json()["weight"] == pytest.approx(100)
    updated = client.patch("/v1/users/me/profile", headers=headers, json={"weight_unit": "KG"})
    assert updated.json()["weight"] == pytest.approx(100)
    assert updated.json()["weight_unit"] == "KG"
    assert client.get("/v1/users/me/profile?weight_unit=LB", headers=headers).json()["weight"] == pytest.approx(220.46226218487757)
    for payload in ({"weight": 700, "weight_unit": "LB"}, {"weight": 317.514659, "weight_unit": "KG"}):
        assert client.patch("/v1/users/me/profile", headers=headers, json=payload).status_code == 200
    for payload in ({"weight": 701, "weight_unit": "LB"}, {"weight": 318, "weight_unit": "KG"}):
        assert client.patch("/v1/users/me/profile", headers=headers, json=payload).status_code == 422


@pytest.mark.parametrize("payload", [
    {"preferred_name": " "}, {"preferred_name": None}, {"preferred_name": "x" * 51},
    {"last_name": "x" * 51}, {"email": "other@example.com"}, {"age": 12},
    {"age": 121}, {"age": 30.5}, {"age": True}, {"gender": "unsupported"},
    {"weight": -1, "weight_unit": "LB"}, {"weight": 70}, {"weight": True, "weight_unit": "LB"},
    {"weight_unit": None}, {"weight_unit": "stones"}, {"weight": "nan", "weight_unit": "KG"},
])
def test_invalid_profile_patch_rejected_without_changes(payload):
    _, headers = signup()
    before = client.get("/v1/users/me/profile", headers=headers).json()
    assert client.patch("/v1/users/me/profile", headers=headers, json=payload).status_code == 422
    assert client.get("/v1/users/me/profile", headers=headers).json() == before


def test_first_profile_save_for_skipped_onboarding_and_cross_account_isolation():
    owner_id, headers = signup()
    _, other_headers = signup()
    result = client.patch("/v1/users/me/profile", headers=headers, json={"age": 35, "gender": "female", "weight": 140, "weight_unit": "LB"})
    assert result.status_code == 200
    assert result.json()["gender"] == "Female"
    assert client.get("/v1/users/me/profile", headers=other_headers).json()["age"] is None
    assert client.get("/v1/users/me", headers=headers).json()["has_completed_onboarding"] is False
    with SessionLocal() as db:
        assert db.get(OnboardingProfile, owner_id).data["age"] == 35


def test_avatar_normalized_private_replace_remove_and_account_delete_cascade():
    owner_id, headers = signup()
    _, other_headers = signup()
    first = client.put("/v1/users/me/avatar", headers={**headers, "Content-Type": "image/png"}, content=photo())
    assert first.status_code == 200, first.text
    version = first.json()["avatar_version"]
    assert version
    assert client.get("/v1/users/me/avatar", headers=other_headers).status_code == 404
    assert client.get("/v1/users/me/avatar").status_code == 401
    downloaded = client.get("/v1/users/me/avatar", headers=headers)
    assert downloaded.headers["cache-control"] == "private, no-store"
    assert downloaded.headers["content-type"] == "image/jpeg"
    assert len(downloaded.content) <= 256 * 1024
    with Image.open(BytesIO(downloaded.content)) as normalized:
        assert normalized.format == "JPEG"
        assert max(normalized.size) <= 512
        assert normalized.height > normalized.width
        assert not normalized.getexif()
    replaced = client.put("/v1/users/me/avatar", headers={**headers, "Content-Type": "image/jpeg"}, content=photo("JPEG", color="blue"))
    assert replaced.json()["avatar_version"] != version
    assert client.delete("/v1/users/me/avatar", headers=headers).json()["avatar_version"] is None
    assert client.delete("/v1/users/me/avatar", headers=headers).status_code == 200
    assert client.get("/v1/users/me/avatar", headers=headers).status_code == 404
    assert client.put("/v1/users/me/avatar", headers={**headers, "Content-Type": "image/png"}, content=photo()).status_code == 200
    assert client.delete("/v1/users/me", headers=headers).status_code == 204
    with SessionLocal() as db:
        assert db.get(UserAvatar, owner_id) is None


def test_failed_avatar_uploads_retain_last_good_photo(monkeypatch):
    _, headers = signup()
    uploaded = client.put("/v1/users/me/avatar", headers={**headers, "Content-Type": "image/png"}, content=photo())
    assert uploaded.status_code == 200
    version = uploaded.json()["avatar_version"]
    good = client.get("/v1/users/me/avatar", headers=headers).content
    for mime, content, status in [
        ("image/png", b"not a photo", 422),
        ("image/gif", b"GIF89a", 415),
        ("image/jpeg", photo("PNG"), 422),
        ("image/png", b"x" * (MAX_AVATAR_UPLOAD_BYTES + 1), 413),
    ]:
        response = client.put("/v1/users/me/avatar", headers={**headers, "Content-Type": mime}, content=content)
        assert response.status_code == status, response.text
    monkeypatch.setattr("app.core.profile_service.MAX_AVATAR_PIXELS", 100)
    assert client.put("/v1/users/me/avatar", headers={**headers, "Content-Type": "image/png"}, content=photo()).status_code == 422
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    assert client.put("/v1/users/me/avatar", headers={**headers, "Content-Type": "image/png"}, content=photo()).status_code == 422
    assert client.get("/v1/users/me/profile", headers=headers).json()["avatar_version"] == version
    assert client.get("/v1/users/me/avatar", headers=headers).content == good


def test_profile_and_mutations_require_authentication():
    assert client.get("/v1/users/me/profile").status_code == 401
    assert client.patch("/v1/users/me/profile", json={"preferred_name": "Test"}).status_code == 401
    assert client.put("/v1/users/me/avatar", content=b"x", headers={"Content-Type": "image/png"}).status_code == 401
    assert client.delete("/v1/users/me/avatar").status_code == 401
