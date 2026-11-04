from fastapi.testclient import TestClient

from app.glossary.search import search_terms
from app.main import app

client = TestClient(app)


def test_exact_first():
    r = search_terms("Hemoglobin")
    assert r[0]["id"] == "hemoglobin" and r[0]["match_type"] == "exact"


def test_alias_sgpt_to_alt():
    r = search_terms("sgpt")
    assert r[0]["id"] == "alanine_aminotransferase" and r[0]["match_type"] == "exact"


def test_prefix_and_substring_ranked_after_exact():
    types = [x["match_type"] for x in search_terms("bilirubin")]
    assert types[0] == "exact" and types == sorted(types, key=["exact", "prefix", "substring", "fuzzy"].index)


def test_typos_fuzzy():
    assert search_terms("hemoglobn")[0]["id"] == "hemoglobin"
    assert search_terms("bilirubine")[0]["id"] == "bilirubin"


def test_no_match_returns_empty():
    assert search_terms("zzqxfoo") == []


def test_max_ten():
    assert len(search_terms("c")) <= 10


def test_api_empty_query_400():
    assert client.get("/api/terms", params={"q": "  "}).status_code == 400


def test_api_long_query_trimmed():
    r = client.get("/api/terms", params={"q": "hemoglobin" + "x" * 500})
    assert r.status_code == 200 and isinstance(r.json(), list)


def test_api_search():
    assert client.get("/api/terms", params={"q": "TSH"}).json()[0]["id"] == "thyroid_stimulating_hormone"
