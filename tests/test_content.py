from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pytest
import soundfile as sf
from pydantic import ValidationError

from autoyt.ai import LLM, validate_grounding
from autoyt.audio import Speech, check_audio
from autoyt.media import choose_media
from autoyt.models import Script
from autoyt.research import extract, independent_sources, verify_claims
from autoyt.video import subtitles


def test_valid_claims(research_data):
    verify_claims(research_data.claims, research_data.sources)


def test_english_kokoro_will_not_read_an_old_hindi_script(cfg, tmp_path):
    cfg.tts_engine, cfg.narration_language, cfg.tts_voice = "kokoro", "en", "am_michael"
    with pytest.raises(ValueError, match="saved script contains Hindi"):
        Speech(cfg).segment("यह पुरानी Hindi narration है।", tmp_path / "voice.wav")


def test_fabricated_quote(research_data):
    research_data.claims[0].evidence[0].quote = "This is an invented sentence that never appeared in the retrieved source."
    with pytest.raises(ValueError, match="quotation"):
        verify_claims(research_data.claims, research_data.sources)


def test_no_primary(research_data):
    for s in research_data.sources:
        s.primary = False
    with pytest.raises(ValueError, match="primary"):
        independent_sources(research_data.sources)


def test_syndication(research_data):
    for s in research_data.sources:
        s.text = research_data.sources[0].text
    with pytest.raises(ValueError):
        independent_sources(research_data.sources)


def test_numerical_claim(research_data):
    research_data.claims[0].claim += " in 2027"
    with pytest.raises(ValueError, match="Numeric"):
        verify_claims(research_data.claims, research_data.sources)


def test_claim_needs_independent_evidence(research_data):
    research_data.sources[1].publisher = "a"
    with pytest.raises(ValueError, match="independent"):
        verify_claims(research_data.claims, research_data.sources)


def test_script_schema(script):
    assert Script.model_validate_json(script.model_dump_json()) == script


def test_concise_complete_short_does_not_need_padding(script):
    data = script.model_dump()
    for scene in data["scenes"]:
        scene["narration"] = " ".join(scene["narration"].split()[:12]).rstrip(".,") + "."
    data["script"] = " ".join(s["narration"] for s in data["scenes"])
    data["hook"] = data["scenes"][0]["narration"]
    assert len(Script.model_validate(data).scenes) == 6


@pytest.mark.parametrize("edit", ["missing", "inconsistent", "short", "placeholder"])
def test_bad_script(script, edit):
    data = script.model_dump()
    if edit == "missing":
        del data["title"]
    elif edit == "inconsistent":
        data["script"] = "different narration"
    elif edit == "short":
        data["scenes"] = data["scenes"][:2]
    else:
        data["description"] = "This is a placeholder for a description."
    with pytest.raises(ValidationError):
        Script.model_validate(data)


def test_script_unsupported_number(script, research_data):
    script.script += " It costs 999 dollars."
    with pytest.raises(ValueError, match="numerical"):
        validate_grounding(script, research_data)


def test_generation_retry_budget(cfg, monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(200, json={"message": {"content": "not JSON"}},
                              request=httpx.Request("POST", "http://localhost"))
    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(ValueError, match="three"):
        LLM(cfg).structured(Script, "test", {})
    assert len(calls) == 3


@pytest.mark.parametrize("silent", [True, False])
def test_audio_validation(tmp_path, silent):
    path = tmp_path/"audio.wav"
    values = np.zeros(24000) if silent else .2*np.sin(np.arange(24000)*.05)
    sf.write(path, values, 24000)
    if silent:
        with pytest.raises(ValueError, match="silent"):
            check_audio(path)
    else:
        assert check_audio(path)["duration"] == 1


def test_audio_clipping(tmp_path):
    path = tmp_path/"clipped.wav"
    sf.write(path, np.ones(1000), 1000)
    with pytest.raises(ValueError, match="clipping"):
        check_audio(path)


def test_missing_piper_model(cfg, tmp_path):
    cfg.tts_engine = "piper"
    with pytest.raises(RuntimeError, match="model"):
        Speech(cfg).segment("Test", tmp_path/"voice.wav")


def test_graphics_fallback(cfg, script):
    assert all(a["kind"] == "graphics" for a in choose_media(cfg, None, script))


def test_caption_boundaries(cfg, tmp_path):
    result = subtitles(cfg, [{"text": "A processor executes software instructions clearly.", "start": 0, "end": 4}], tmp_path)
    assert abs(result[-1]["end"]-4) < .001
    ass = (tmp_path/"subtitles.ass").read_text(encoding="utf-8-sig")
    assert "\\fad(" in ass
    assert all(len(phrase["text"].split()) <= 3 for phrase in result)
    style = next(line for line in ass.splitlines() if line.startswith("Style: Default,")).split(",")
    assert style[7] == "-1"  # Bold text.
    assert style[15] == "1"  # Subtle outline; the keyword has a separate blue rectangle.
    assert "\\1c&HFF503D&\\p1" in ass
    assert " ".join(phrase["text"] for phrase in result) == "A processor executes software instructions clearly."
    assert "A PROCESSOR EXECUTES" not in ass
    assert "00:00:04,000" in (tmp_path/"subtitles.srt").read_text()


def test_extraction_and_date_conflict(cfg):
    date = datetime.now(timezone.utc).date().isoformat()
    body = "Google announced software for computer processors. " * 30
    html = f'<html><head><title>Google software update</title><meta property="article:published_time" content="{date}"/></head><body><article><h1>Google software update</h1><p>{body}</p></article></body></html>'
    doc = extract("https://blog.google/test", html, cfg)
    assert doc.primary and "processors" in doc.text
    with pytest.raises(ValueError, match="Conflicting"):
        extract("https://blog.google/test", html, cfg, datetime.now(timezone.utc)-timedelta(days=4))


@pytest.mark.parametrize("age_days,accepted", [(3, True), (6.9, True), (7.1, False), (-1, False)])
def test_source_seven_day_window(cfg, monkeypatch, age_days, accepted):
    from types import SimpleNamespace

    date = datetime.now(timezone.utc)-timedelta(days=age_days)
    doc = SimpleNamespace(title="Google software update", date=date.isoformat(),
                          text="Google introduced new software controls. " * 30)
    monkeypatch.setattr("autoyt.research.trafilatura.bare_extraction", lambda *args, **kwargs: doc)
    if accepted:
        assert extract("https://blog.google/test", "html", cfg).published_at == date
    else:
        with pytest.raises(ValueError, match="Stale or future"):
            extract("https://blog.google/test", "html", cfg)


def test_updated_vendor_advisory_preserves_publication_date(cfg, monkeypatch):
    from types import SimpleNamespace
    current = datetime.now(timezone.utc)
    heading = current.strftime("%B %d, %Y")
    text = f"Latest Advisories\n{heading}: Cloud Backup Service Update\n" + "Updated security guidance. "*30
    doc = SimpleNamespace(title="Trust Center | Advisories", date="2025-05-28", text=text)
    monkeypatch.setattr("autoyt.research.trafilatura.bare_extraction", lambda *args, **kwargs: doc)
    cfg.primary_domains.append("vendor.example")
    source = extract("https://vendor.example/advisories", "html", cfg)
    assert source.published_at.year == 2025
    assert source.updated_at.date() == current.date()
    assert source.date_basis == "dated_advisory_heading"
    with pytest.raises(ValueError, match="Stale"):
        extract("https://other.example/advisories", "html", cfg)


def test_source_access_failure_is_saved_without_raw_exception(cfg, tmp_path):
    import json
    from types import SimpleNamespace

    from autoyt.research import research
    request = httpx.Request("GET", "https://news.example/article")
    response = httpx.Response(403, request=request)
    def blocked(url):
        raise httpx.HTTPStatusError("PRIVATE_API_SECRET", request=request, response=response)
    topic = SimpleNamespace(url=str(request.url), published_at=datetime.now(timezone.utc),
                            related_urls=[], title="Google processor software update")
    with pytest.raises(ValueError, match="0 official and 0 independent"):
        research(cfg, SimpleNamespace(article=blocked), None, topic, [], tmp_path)
    records = json.loads((tmp_path/"source_retrievals.json").read_text())
    assert records == [{"url": str(request.url), "result": "Source returned HTTP 403"}]
    assert "PRIVATE_API_SECRET" not in json.dumps(records)


def test_stale_official_source_is_reported_and_still_rejected(cfg, tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from autoyt.research import research
    topic = SimpleNamespace(url="https://blog.google/article", published_at=datetime.now(timezone.utc),
                            related_urls=[], title="Google processor software update")
    def stale(*args):
        raise ValueError("Stale or future source date")
    monkeypatch.setattr("autoyt.research.extract", stale)
    with pytest.raises(ValueError, match="Evidence incomplete"):
        research(cfg, SimpleNamespace(article=lambda url: "html"), None, topic, [], tmp_path)
    records = json.loads((tmp_path/"source_retrievals.json").read_text())
    assert records[0]["result"] == "Stale or future source date"
