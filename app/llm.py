"""Optional LLM fallback for terms NOT in the glossary. OFF by default.

Enabled only when USE_LLM=1 and ANTHROPIC_API_KEY are set (see app/main.py).
Output is labelled "AI-generated, unverified" by the pipeline and still passes
app/safety.py. Only the unknown term and its single source line are sent, never
the whole report.
"""
from __future__ import annotations

from typing import Optional

MODEL = "claude-opus-5-5"
SYSTEM = (
    "You define medical terms for patients in plain language (8th-grade reading level). "
    "Reply with ONE or TWO sentences describing what the term generally means or measures. "
    "Never say what it means for the reader personally, never diagnose, never suggest treatment, "
    "drugs or doses. If the term is unclear or not medical, reply exactly: UNKNOWN"
)

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic  # optional dependency: pip install anthropic
        _client = anthropic.Anthropic()
    return _client


def explain_unknown_term(term: str, context_line: str) -> Optional[str]:
    response = _get_client().beta.messages.create(
        model=MODEL,
        max_tokens=1024,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        messages=[{"role": "user", "content": f"Term: {term}\nLine where it appears in a report: {context_line[:300]}"}],
    )
    if response.stop_reason == "refusal":
        return None
    text = " ".join(b.text for b in response.content if b.type == "text").strip()
    if not text or text.upper().startswith("UNKNOWN"):
        return None
    return text[:400]
