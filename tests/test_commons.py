import json

import pytest
from PIL import Image

from autoyt.commons import CC0, PUBLIC_DOMAIN, acquire, canonical_license
from autoyt.licensing import audit
from autoyt.media import choose_media


def response(license_name="CC0", license_url=CC0, description="Computer motherboard processor"):
    return {"query": {"pages": {"1": {"pageid": 1, "title": "File:Computer motherboard.jpg",
        "imageinfo": [{"width": 1200, "height": 1200, "mime": "image/jpeg",
                       "thumburl": "https://upload.wikimedia.org/image.jpg",
                       "descriptionurl": "https://commons.wikimedia.org/wiki/File:Computer_motherboard.jpg",
                       "extmetadata": {"LicenseShortName": {"value": license_name},
                                       "LicenseUrl": {"value": license_url},
                                       "ImageDescription": {"value": description},
                                       "Artist": {"value": "<b>Example creator</b>"},
                                       "Copyrighted": {"value": "False"}}}]}}}}


class Web:
    def __init__(self, data):
        self.data, self.calls, self.downloads = data, 0, 0

    def get(self, url):
        self.calls += 1
        return json.dumps(self.data)

    def download(self, url, path, max_bytes):
        self.downloads += 1
        Image.new("RGB", (1080, 1080), "blue").save(path, format="JPEG")


@pytest.mark.parametrize("license_name,url", [("CC0", CC0), ("Public domain", PUBLIC_DOMAIN)])
def test_public_images_record_rights_cache_and_prevent_reuse(cfg, license_name, url):
    web, used = Web(response(license_name, url)), set()
    asset = acquire(cfg, web, "computer motherboard", used)
    assert audit([asset])["assets"][0]["provider"] == "Wikimedia Commons"
    assert "Example creator" in asset["attribution"] and "<b>" not in asset["attribution"]
    assert acquire(cfg, web, "computer motherboard", used) is None
    assert web.calls == 1 and web.downloads == 1


@pytest.mark.parametrize("license_name,url", [("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/"),
                                            ("CC0", "https://example.com/not-a-license")])
def test_unsupported_or_unclear_image_rights_are_skipped(cfg, license_name, url):
    web = Web(response(license_name, url))
    assert acquire(cfg, web, "computer motherboard", set()) is None
    assert web.downloads == 0


def test_unrelated_public_image_is_not_downloaded(cfg):
    web = Web(response())
    assert acquire(cfg, web, "smartphone camera", set()) is None
    assert web.downloads == 0


def test_real_commons_localized_license_urls_are_canonicalized():
    assert canonical_license("http://creativecommons.org/publicdomain/zero/1.0/deed.en") == CC0
    assert canonical_license("https://creativecommons.org.evil.example/publicdomain/zero/1.0/") == ""


def test_public_images_are_used_without_stock_api_keys(cfg, script):
    cfg.enable_public_images = True
    for scene in script.scenes:
        scene.visual_query = "computer motherboard"
    web = Web(response())
    assets = choose_media(cfg, web, script)
    assert assets[0]["provider"] == "Wikimedia Commons"
    assert all(a["kind"] == "graphics" for a in assets[1:])
    assert web.downloads == 1


def test_public_image_failure_falls_back_without_repeated_requests(cfg, script):
    cfg.enable_public_images = True
    class FailingWeb:
        calls = 0

        def get(self, url):
            self.calls += 1
            raise RuntimeError("Unavailable")

    web = FailingWeb()
    assert all(a["kind"] == "graphics" for a in choose_media(cfg, web, script))
    assert web.calls == 1
