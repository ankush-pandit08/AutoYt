import json
import os

from PIL import Image

from autoyt.media import choose_media
from autoyt.pixabay import acquire, search


def test_search_cache_is_reused_for_24_hours_without_saving_key(cfg):
    cfg.pixabay_api_key = "private-test-key"

    class Web:
        calls = 0

        def get(self, url):
            self.calls += 1
            assert "safesearch=true" in url and "key=private-test-key" in url
            return '{"hits":[]}'

    web = Web()
    search(cfg, web, "technology", "image")
    search(cfg, web, "technology", "image")
    assert web.calls == 1
    cached = next(cfg.cache_dir.glob("pixabay-search-*.json"))
    assert cfg.pixabay_api_key not in cached.read_text()
    os.utime(cached, (0, 0))
    search(cfg, web, "technology", "image")
    assert web.calls == 2


def test_image_fallback_records_license_and_does_not_reuse_asset(cfg):
    cfg.pixabay_api_key = "private-test-key"
    cfg.cache_dir.mkdir()

    class Web:
        downloads = 0

        def get(self, url):
            if "/videos/" in url:
                return '{"hits":[]}'
            return json.dumps({"hits": [{"id": 12, "pageURL": "https://pixabay.com/photos/id-12/",
                                         "user": "Creator", "largeImageURL": "https://cdn.pixabay.com/12.jpg"}]})

        def download(self, url, path):
            self.downloads += 1
            Image.new("RGB", (720, 1280), "blue").save(path, format="JPEG")

    web, used = Web(), set()
    asset = acquire(cfg, web, "technology", used)
    assert asset["kind"] == "stock_image"
    assert asset["source_url"] == "https://pixabay.com/photos/id-12/"
    assert "Pixabay Content License" in asset["license"] and "Creator" in asset["attribution"]
    assert acquire(cfg, web, "technology", used) is None
    assert web.downloads == 1


def test_bad_provider_key_stops_requests_and_does_not_leak_into_logs(cfg, script, monkeypatch, caplog):
    cfg.pixabay_api_key = "secret-should-not-be-logged"
    calls = []

    def failure(*args):
        calls.append(1)
        raise RuntimeError("https://pixabay.com/api/?key=" + cfg.pixabay_api_key)

    monkeypatch.setattr("autoyt.media.pixabay_asset", failure)
    assert all(a["kind"] == "graphics" for a in choose_media(cfg, object(), script))
    assert calls == [1]
    assert cfg.pixabay_api_key not in caplog.text
