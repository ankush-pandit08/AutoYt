import hashlib
import json
import logging

from .audio import probe
from .discovery import words
from .pixabay import acquire as pixabay_asset

log = logging.getLogger(__name__)


def choose_media(cfg, web, script):
    """Local assets require an explicit license manifest. Generic stock is labelled in the video."""
    local = []
    if cfg.local_media_manifest.is_file():
        local = json.loads(cfg.local_media_manifest.read_text(encoding="utf-8"))
    result = []
    pixabay_queries, pixabay_used = set(), set()
    pixabay_failed = False
    for scene in script.scenes:
        asset = {"kind": "graphics", "license": "Original AutoYT graphics", "query": scene.visual_query,
                 "attribution": "Original explanatory graphics", "path": None, "source_url": None}
        for entry in local:
            if not entry.get("license") or not entry.get("source_url") or not entry.get("permitted_use"):
                continue
            path = (cfg.local_media_manifest.parent / entry["file"]).resolve()
            if not path.is_relative_to(cfg.local_media_manifest.parent.resolve()):
                continue
            if path.is_file() and len(words(scene.visual_query) & words(" ".join(entry.get("tags", [])))) >= 2:
                try:
                    stream = next(s for s in probe(cfg, path)["streams"] if s["codec_type"] == "video")
                    if stream["width"] < 720:
                        continue
                    asset.update(entry, kind="stock", path=str(path))
                    break
                except Exception:
                    continue
        if asset["path"] is None and cfg.pexels_api_key:
            try:
                from urllib.parse import urlencode
                data = json.loads(web.get("https://api.pexels.com/v1/videos/search?" + urlencode(
                    {"query": scene.visual_query, "per_page": 4, "orientation": "portrait"}),
                    headers={"Authorization": cfg.pexels_api_key}))
                for video in data.get("videos", []):
                    files = [f for f in video["video_files"] if f.get("width", 0) >= 720
                             and f.get("file_type") == "video/mp4"]
                    if not files:
                        continue
                    file = min(files, key=lambda f: f["width"] * f["height"])
                    key = hashlib.sha256(file["link"].encode()).hexdigest()[:24]
                    path = cfg.cache_dir / f"pexels-{key}.mp4"
                    if not path.is_file():
                        tmp = path.with_suffix(".part")
                        web.download(file["link"], tmp)
                        probe(cfg, tmp)
                        tmp.replace(path)
                    probe(cfg, path)
                    asset.update(kind="stock", path=str(path.resolve()), source_url=video["url"],
                                 license="Pexels License https://www.pexels.com/license/",
                                 attribution=f"Video by {video['user']['name']} on Pexels: {video['url']}")
                    break
            except Exception as error:
                log.warning("Pexels unavailable (%s); using original graphics", type(error).__name__)
        if asset["path"] is None and cfg.pixabay_api_key and not pixabay_failed:
            # At most two distinct search queries per run, cached for 24h; no mass downloading.
            query = scene.visual_query if scene.visual_query in pixabay_queries or len(pixabay_queries) < 2 else next(iter(pixabay_queries))
            pixabay_queries.add(query)
            try:
                selected = pixabay_asset(cfg, web, query, pixabay_used)
                if selected:
                    asset = selected
            except Exception as error:
                pixabay_failed = True
                log.warning("Pixabay unavailable (%s); using original graphics", type(error).__name__)
        result.append(asset)
    return result
