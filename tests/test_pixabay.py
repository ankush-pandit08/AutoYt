import json
import os

from PIL import Image

from autoyt.media import choose_media
from autoyt.pixabay import acquire, relevant, search


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
                                         "tags": "technology, computer, software",
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


def test_generic_query_terms_do_not_accept_unrelated_stock():
    query = "cyber security digital lock concept close up"
    assert not relevant(query, {"tags": "bacon, pan, hot, belly bacon, meal, food, close up"})
    assert not relevant(query, {"tags": "bee, honey bee, insect, pollen, close up"})
    assert not relevant(query, {"tags": "firenze, italy, black white, europe, travel, tourism, street, house, lock, security"})
    assert not relevant(query, {"tags": "heart, lock, padlock, fence, love"})
    assert relevant(query, {"tags": "cyber, security, digital, encryption"})


def test_specific_queries_need_more_than_one_generic_tag():
    assert not relevant("digital computer processor", {"tags": "digital, abstract, wallpaper"})
    assert relevant("computer processor", {"tags": "computer, processor, chip"})
    assert relevant("server", {"tags": "network, rack"})
    assert not relevant("close up concept", {"tags": "computer, processor"})


def test_each_scene_uses_its_own_search_instead_of_reusing_an_earlier_query(cfg, script, monkeypatch):
    cfg.pixabay_api_key = "private-test-key"
    queries = ["computer processor", "network router", "laptop keyboard", "phone camera",
               "computer memory", "server storage"]
    for scene, query in zip(script.scenes, queries):
        scene.visual_query = query
    called = []

    def acquire(cfg, web, query, used):
        called.append(query)
        index = len(called)
        return {"kind": "stock_image", "path": str(cfg.cache_dir / f"image-{index}.jpg"),
                "source_url": f"https://pixabay.com/photos/image-{index}/", "query": query,
                "license": "Pixabay Content License https://pixabay.com/service/license-summary/",
                "attribution": "Image by Creator on Pixabay"}

    monkeypatch.setattr("autoyt.media.pixabay_asset", acquire)
    assets = choose_media(cfg, None, script)
    assert called == queries
    assert len({a["path"] for a in assets}) == 6


def test_same_asset_is_never_used_twice_in_one_video(cfg, script, monkeypatch):
    cfg.pixabay_api_key = "private-test-key"
    asset = {"kind": "stock_image", "path": str(cfg.cache_dir / "repeated.jpg"),
             "source_url": "https://pixabay.com/photos/repeated-12/",
             "license": "Pixabay Content License https://pixabay.com/service/license-summary/",
             "attribution": "Image by Creator on Pixabay"}
    monkeypatch.setattr("autoyt.media.pixabay_asset", lambda *args: dict(asset))
    assets = choose_media(cfg, None, script)
    assert assets[0]["kind"] == "stock_image"
    assert all(a["kind"] == "graphics" for a in assets[1:])


def test_search_budget_does_not_substitute_a_different_scene_query(cfg, script, monkeypatch):
    cfg.pixabay_api_key = "private-test-key"
    script.scenes += [script.scenes[0].model_copy(deep=True)]
    for i, scene in enumerate(script.scenes):
        scene.visual_query = f"computer component {i}"
    called = []
    monkeypatch.setattr("autoyt.media.pixabay_asset", lambda cfg, web, query, used: called.append(query))
    assets = choose_media(cfg, None, script)
    assert called == [s.visual_query for s in script.scenes[:6]]
    assert assets[-1]["kind"] == "graphics"
