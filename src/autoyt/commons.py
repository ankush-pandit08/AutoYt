"""Bounded, cached Wikimedia Commons photos with explicit reusable-rights metadata."""
import hashlib
import json
import re
import time
from html import unescape
from urllib.parse import urlencode, urlsplit

from PIL import Image

from .discovery import words
from .pixabay import FILLER
from .store import write_json

CC0 = "https://creativecommons.org/publicdomain/zero/1.0/"
PUBLIC_DOMAIN = "https://creativecommons.org/publicdomain/mark/1.0/"


def plain(value):
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", value)).split())


def canonical_license(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("creativecommons.org", "www.creativecommons.org"):
        return ""
    path = re.sub(r"/deed(?:\.[a-zA-Z_-]+)?/?$", "/", parsed.path)
    return "https://creativecommons.org" + path.rstrip("/") + "/"


def search(cfg, web, query):
    specific = " ".join(w for w in query.split() if w.lower() not in FILLER)[:120]
    key = hashlib.sha256(specific.encode()).hexdigest()[:24]
    cache = cfg.cache_dir / f"commons-search-{key}.json"
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    if cache.is_file() and time.time()-cache.stat().st_mtime < 86400:
        return json.loads(cache.read_text(encoding="utf-8"))
    params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
              "gsrsearch": specific + " filetype:bitmap", "gsrlimit": 6, "prop": "imageinfo",
              "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1080,
              "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|ImageDescription|Copyrighted|AttributionRequired"}
    data = json.loads(web.get("https://commons.wikimedia.org/w/api.php?" + urlencode(params)))
    if "error" in data:
        raise ValueError("Public image search unavailable")
    write_json(cache, data)
    return data


def acquire(cfg, web, query, used):
    terms = words(query) - FILLER - {"technology", "concept", "illustration", "photo"}
    if not terms:
        return None
    pages = search(cfg, web, query).get("query", {}).get("pages", {})
    for page in sorted(pages.values(), key=lambda p: p.get("index", 999)):
        identity = str(page.get("pageid", ""))
        if not identity or identity in used or not page.get("imageinfo"):
            continue
        info = page["imageinfo"][0]
        metadata = info.get("extmetadata", {})
        def value(name):
            return plain(str(metadata.get(name, {}).get("value", "")))

        short, license_url = value("LicenseShortName"), canonical_license(value("LicenseUrl"))
        if short.lower() in ("cc0", "cc0 1.0") and license_url == CC0:
            license_name = "CC0-1.0"
        elif (short.lower() == "public domain" and license_url == PUBLIC_DOMAIN
              and value("Copyrighted").lower() == "false"):
            license_name = "Public domain"
        else:
            continue
        if value("AttributionRequired").lower() == "true":
            continue
        description = page.get("title", "") + " " + value("ImageDescription")
        if len(terms & words(description)) < min(2, len(terms)):
            continue
        if info.get("mime") not in ("image/jpeg", "image/png") or min(info.get("width", 0), info.get("height", 0)) < 720:
            continue
        source, link = info.get("descriptionurl", ""), info.get("thumburl") or info.get("url", "")
        if (urlsplit(source).hostname != "commons.wikimedia.org" or urlsplit(source).scheme != "https"
                or urlsplit(link).hostname not in ("upload.wikimedia.org", "thumb.wikimedia.org")
                or urlsplit(link).scheme != "https"):
            continue
        suffix = ".png" if info["mime"] == "image/png" else ".jpg"
        path = cfg.cache_dir / f"commons-{identity}{suffix}"
        if not path.is_file():
            temporary = path.with_suffix(".part")
            web.download(link, temporary, max_bytes=12_000_000)
            with Image.open(temporary) as image:
                image.verify()
            with Image.open(temporary) as image:
                if image.width < 720 or image.height < 540:
                    temporary.unlink(missing_ok=True)
                    continue
            temporary.replace(path)
        used.add(identity)
        return {"kind": "stock_image", "provider": "Wikimedia Commons", "path": str(path.resolve()),
                "query": query, "source_url": source, "license": license_name, "license_url": license_url,
                "free_to_use": True, "permitted_use": "Illustrative edited image in a technology explainer",
                "attribution": f"{value('Artist') or 'Wikimedia Commons contributor'} | {source}",
                "tags": plain(description)}
    return None
