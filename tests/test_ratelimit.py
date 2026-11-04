from fastapi.testclient import TestClient

from app.main import app
from app.ratelimit import RateLimiter, limiter


def test_limiter_window():
    rl = RateLimiter(limit=3, window=60)
    assert [rl.allow("a")[0] for _ in range(4)] == [True, True, True, False]
    assert rl.allow("b")[0]                    # per-IP buckets


def test_api_returns_429_with_friendly_message(monkeypatch):
    monkeypatch.setattr(limiter, "limit", 2)
    limiter.reset()
    c = TestClient(app)
    body = {"text": "Hemoglobin  11.2  g/dL  13-17"}
    assert c.post("/api/analyze", json=body).status_code == 200
    assert c.post("/api/analyze", json=body).status_code == 200
    r = c.post("/api/analyze", json=body)
    assert r.status_code == 429 and "wait a minute" in r.json()["detail"] and r.headers["Retry-After"]
    assert c.get("/api/health").status_code == 200                  # only POST analyze/extract are limited
    limiter.reset()


def test_llm_fallback_off_by_default():
    assert TestClient(app).get("/api/health").json()["llm_fallback"] is False
