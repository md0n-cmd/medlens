from app.models import Tag
from app.pipeline import analyze


def test_llm_output_is_labelled_and_safety_filtered():
    text = "Findings suggest cholecystitis."
    r = analyze(text, explain_unknown=lambda term, ctx: "Inflammation of the gallbladder.")
    item = next(e for e in r.explanations if e.origin == "llm")
    assert item.tag == Tag.GENERAL and "AI-generated, unverified" in item.title and "AI-generated, unverified" in item.text

    r = analyze(text, explain_unknown=lambda term, ctx: "You have gallbladder disease; take antibiotics.")
    item = next(e for e in r.explanations if e.origin == "llm")
    assert item.safety_filtered


def test_llm_failure_never_breaks_pipeline():
    def boom(term, ctx):
        raise RuntimeError("network down")
    r = analyze("Findings suggest cholecystitis.", explain_unknown=boom)
    assert any("LLM fallback failed" in w for w in r.warnings)
    assert any(e.title.startswith("Unrecognized term") for e in r.explanations)
