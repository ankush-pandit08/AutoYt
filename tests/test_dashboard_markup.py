import re
from html.parser import HTMLParser
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src/autoyt/web"


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_dashboard_controls_have_unique_ids_and_every_script_target_exists():
    parser = Elements()
    parser.feed((WEB / "index.html").read_text(encoding="utf-8"))
    ids = [attrs["id"] for _, attrs in parser.elements if "id" in attrs]
    assert len(ids) == len(set(ids))
    targets = set(re.findall(r"\$\('([^']+)'\)", (WEB / "app.js").read_text(encoding="utf-8")))
    assert targets <= set(ids), targets-set(ids)
    labels = {attrs["for"] for tag, attrs in parser.elements if tag == "label" and "for" in attrs}
    for tag, attrs in parser.elements:
        if tag in {"input", "select", "textarea"} and attrs.get("type") != "checkbox":
            assert attrs.get("id") in labels or "aria-label" in attrs


def test_dashboard_has_accessible_dialogs_and_no_inline_scripts_or_handlers():
    parser = Elements()
    parser.feed((WEB / "index.html").read_text(encoding="utf-8"))
    for tag, attrs in parser.elements:
        assert not any(name.startswith("on") for name in attrs)
        if tag == "dialog":
            assert "aria-labelledby" in attrs
        if tag == "script":
            assert attrs.get("src") and attrs.get("type") == "module"
