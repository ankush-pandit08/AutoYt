"""Small, cached stock searches with local downloads and recorded creator credits."""
import hashlib
import json
import time
from urllib.parse import urlencode

from PIL import Image

from .audio import probe
from .discovery import words
from .store import write_json

FILLER = {"close", "up", "closeup", "concept", "background", "blinking", "lights", "room", "glowing"}


def relevant(query, hit):
    # Search APIs can return unrelated matches to generic words such as "close up".
    terms = words(query) - FILLER
    if "server" in terms or "servers" in terms:
        terms |= {"network", "networking", "data", "router", "rack"}
    if "lock" in terms:
        terms |= {"padlock", "keyhole", "encryption"}
    tags = ",".join(hit.get("tags", "").split(",")[:8])
    tag_words = words(tags) - FILLER
    tech = {"server", "servers", "cyber", "digital", "backup", "software", "computer", "data",
            "storage", "network", "technology", "cybersecurity"}
    anchors = tech | {"networking", "router", "rack", "security", "lock", "padlock", "encryption",
                      "chip", "keyboard", "laptop", "code", "circuit", "processor", "database",
                      "disk", "internet", "binary"}
    if terms & tech and not tag_words & anchors:
        return False
    if tag_words & {"heart", "love", "romance", "valentine"} and not terms & {"heart", "love", "romance"}:
        return False
    return bool(terms & tag_words)


def search(cfg, web, query, kind):
    specific = " ".join(w for w in query.split() if w.lower() not in FILLER)
    params = {"q": specific[:100], "per_page": 5, "safesearch": "true", "order": "popular"}
    if kind == "image":
        params.update(image_type="photo", orientation="vertical", min_width=720, min_height=1280)
    key = hashlib.sha256(json.dumps([kind, params], sort_keys=True).encode()).hexdigest()[:24]
    cache = cfg.cache_dir / f"pixabay-search-{key}.json"
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    if cache.is_file() and time.time() - cache.stat().st_mtime < 86400:
        return json.loads(cache.read_text(encoding="utf-8"))
    endpoint = "https://pixabay.com/api/videos/" if kind == "video" else "https://pixabay.com/api/"
    data = json.loads(web.get(endpoint + "?" + urlencode({**params, "key": cfg.pixabay_api_key})))
    # Only public search metadata is cached; never persist request URLs containing API keys.
    write_json(cache, data)
    return data


def acquire(cfg, web, query, used):
    for kind in ("video", "image"):
        for hit in search(cfg, web, query, kind).get("hits", []):
            if not relevant(query, hit):
                continue
            identity = f"{kind}:{int(hit['id'])}"
            if identity in used:
                continue
            if kind == "video":
                choices = [v for v in hit.get("videos", {}).values()
                           if v.get("width", 0) >= 720 and v.get("height", 0) >= 1280
                           and 0 < v.get("size", 0) < 80_000_000]
                if not choices:
                    continue
                file = min(choices, key=lambda v: v["width"] * v["height"])
                link, suffix = file["url"], ".mp4"
            else:
                link, suffix = hit.get("largeImageURL"), ".jpg"
                if not link:
                    continue
            path = cfg.cache_dir / f"pixabay-{kind}-{int(hit['id'])}{suffix}"
            if not path.is_file():
                temporary = path.with_suffix(".part")
                web.download(link, temporary)
                if kind == "video":
                    streams = probe(cfg, temporary)["streams"]
                    video = next(v for v in streams if v["codec_type"] == "video")
                    if video["width"] < 720 or video["height"] < 1280:
                        continue
                else:
                    with Image.open(temporary) as image:
                        image.verify()
                    with Image.open(temporary) as image:
                        if image.width < 720 or image.height < 1280:
                            continue
                temporary.replace(path)
            used.add(identity)
            return {"kind": "stock" if kind == "video" else "stock_image", "path": str(path.resolve()),
                    "tags": hit.get("tags", ""),
                    "query": query, "source_url": hit["pageURL"], "provider": "Pixabay",
                    "license": "Pixabay Content License https://pixabay.com/service/license-summary/",
                    "attribution": f"{kind.title()} by {hit['user']} on Pixabay: {hit['pageURL']}"}
    return None
