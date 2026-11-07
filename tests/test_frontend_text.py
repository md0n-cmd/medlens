"""The plain-words box is built client-side; its fixed phrases must obey the same safety rules."""
import re
from pathlib import Path

from app.safety import is_safe, violations

HTML = (Path(__file__).resolve().parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")


def plain_phrases():
    block = HTML[HTML.index("const PLAIN = {"):HTML.index("};", HTML.index("const PLAIN = {"))]
    return re.findall(r':\s*"([^"]+)"', block)


def test_plain_words_phrases_exist():
    phrases = plain_phrases()
    assert len(phrases) >= 15
    assert "This is a reading aid, not a diagnosis. Only your doctor can say what these results mean for you." in phrases


def test_plain_words_phrases_pass_safety_filter():
    for p in plain_phrases():
        assert is_safe(p), (p, violations(p))


def test_doctor_questions_are_questions():
    for p in plain_phrases():
        if p.endswith("?"):
            assert not re.search(r"\byou should\b", p, re.I)


def test_verify_summary_leads_with_oversteps():
    start = HTML.index("function verifySummary(v)")
    body = HTML[start:HTML.index("function renderVerify", start)]
    assert body.index("only a doctor can make") < body.index("claims\")} checked")


def test_verify_summary_counts_passages():
    start = HTML.index("function verifySummary(v)")
    body = HTML[start:HTML.index("function renderVerify", start)]
    assert "OVERSTEP_PASSAGES" in body and "passages go" in body


def test_ai_pane_markdown_renderer_keeps_every_sentence_and_badge():
    """Runs the page's renderAiMarkdown in Node on a real Verify result (skipped without Node)."""
    import json
    import shutil
    import subprocess
    import pytest
    from app.verify.check import verify
    if not shutil.which("node"):
        pytest.skip("node not installed")
    root = Path(__file__).resolve().parent.parent
    ai = (root / "samples" / "ai" / "heldout" / "04_thyroid_urine__chatbot_heldout.txt").read_text()
    r = json.loads(verify((root / "samples" / "04_thyroid_urine.txt").read_text(), ai).model_dump_json())
    r["ai_text"] = ai
    script = r"""
const fs=require('fs');const h=fs.readFileSync(process.argv[1],'utf8');const js=h.split('<script>')[1].split('</script>')[0];
const src=js.slice(js.indexOf('const VLABEL'), js.indexOf('let vres'))+js.slice(js.indexOf('function mdLineFlags'), js.indexOf('function outlineReport'));
const esc=(s)=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
eval(src);const v=JSON.parse(fs.readFileSync(0,'utf8'));const html=renderAiMarkdown(v.ai_text,v.sentences);
const ids=new Set([...html.matchAll(/data-sid="(s\d+)"/g)].map(m=>m[1]));
const exp=v.sentences.filter(s=>s.label!=='NEUTRAL'&&!(s.passage_id&&s.passage_size>1&&s.passage_id!==s.id)).length;
console.log(JSON.stringify({missing:v.sentences.filter(s=>!ids.has(s.id)).length,badges:(html.match(/class="vbadge /g)||[]).length,
  expected:exp,tables:(html.match(/<table/g)||[]).length,raw:/\*\*|\|\s*---/.test(html.replace(/<[^>]+>/g,' '))}));"""
    out = subprocess.run(["node", "-e", script, str(root / "frontend" / "index.html")], input=json.dumps(r),
                         capture_output=True, text=True, check=True).stdout
    res = json.loads(out)
    assert res["missing"] == 0 and res["badges"] == res["expected"] and res["tables"] == 3 and not res["raw"]
