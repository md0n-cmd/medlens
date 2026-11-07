from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json()["status"] == "ok"


def test_samples_listed_and_synthetic():
    s = client.get("/api/samples").json()
    assert len(s) == 10
    assert all("SYNTHETIC SAMPLE" in x["text"] for x in s)


def test_analyze_endpoint():
    r = client.post("/api/analyze", json={"text": "Hemoglobin  11.2  L  g/dL  13.0 - 17.0"})
    assert r.status_code == 200
    body = r.json()
    assert body["values"][0]["status"] == "below_range"
    assert body["disclaimer"].startswith("MedLens provides informational assistance only")
    assert body["summary"]["out_of_range"] == 1


def test_analyze_rejects_empty():
    assert client.post("/api/analyze", json={"text": "   "}).status_code == 400


def test_frontend_served():
    r = client.get("/")
    assert r.status_code == 200 and "MedLens" in r.text


def test_head_root_for_uptime_checks():
    r = client.head("/")
    assert r.status_code == 200 and r.content == b""
