import pytest

from app.ratelimit import limiter


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    """Tests share one TestClient IP; keep the per-minute limit from leaking between tests."""
    limiter.reset()
    yield
    limiter.reset()
