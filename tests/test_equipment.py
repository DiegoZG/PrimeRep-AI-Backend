"""
Minimal smoke tests for /v1/equipment endpoint.

Note: These tests assume migrations have been run and seed data exists in the dev DB.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_get_equipment_returns_200_and_items():
    """Test that GET /v1/equipment returns 200 and a non-empty items list."""
    response = client.get("/v1/equipment")
    assert response.status_code == 200
    
    data = response.json()
    assert "items" in data
    assert isinstance(data["items"], list)
    assert len(data["items"]) > 0


def test_equipment_items_have_required_fields():
    """Test that equipment items have all required fields."""
    response = client.get("/v1/equipment")
    assert response.status_code == 200
    
    data = response.json()
    items = data["items"]
    
    # Check at least one item has required fields
    assert len(items) > 0
    item = items[0]
    assert "id" in item
    assert "name" in item
    assert "category" in item
    assert "category_order" in item
    assert "sort_order" in item
    assert "is_active" in item


def test_all_equipment_is_active():
    """Test that all returned equipment has is_active=True."""
    response = client.get("/v1/equipment")
    assert response.status_code == 200
    
    data = response.json()
    items = data["items"]
    
    assert all(item["is_active"] is True for item in items)


def test_known_equipment_id_exists():
    """Test that a known equipment ID exists in the response."""
    response = client.get("/v1/equipment")
    assert response.status_code == 200
    
    data = response.json()
    items = data["items"]
    item_ids = [item["id"] for item in items]
    
    assert "ab_crunch_machine" in item_ids

