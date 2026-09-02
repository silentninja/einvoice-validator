from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_ui_page_is_served():
    response = client.get("/ui")

    assert response.status_code == 200
    assert "Validate invoice and credit note documents" in response.text
    assert "/api/v1/validate" in response.text
