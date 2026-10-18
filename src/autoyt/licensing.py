"""Allow only original graphics and explicitly documented free media licenses."""
from urllib.parse import urlsplit

from .store import now

LICENSES = {
    "Pixabay": ("pixabay.com", "https://pixabay.com/service/license-summary/"),
    "Pexels": ("pexels.com", "https://www.pexels.com/license/"),
}


def check_asset(asset):
    if asset.get("kind") == "graphics" and asset.get("license") == "Original AutoYT graphics":
        return "Original"
    if asset.get("kind") not in ("stock", "stock_image"):
        raise ValueError("Media has no approved free license")
    source = urlsplit(asset.get("source_url") or "")
    if source.scheme != "https" or not asset.get("attribution"):
        raise ValueError("Media requires an HTTPS source and creator attribution")
    for provider, (domain, license_url) in LICENSES.items():
        if source.hostname in (domain, "www." + domain) and license_url in asset.get("license", ""):
            return provider
    if (asset.get("free_to_use") is True and asset.get("license") in ("CC0-1.0", "CC-BY-4.0")
            and asset.get("permitted_use") and asset.get("license_url") == {
                "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
                "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
            }[asset["license"]]):
        return "Local free license"
    raise ValueError("Media license is not in the approved free-license policy")


def audit(assets):
    if not assets:
        raise ValueError("Media license records are missing")
    records = [{"provider": check_asset(asset), "license": asset["license"],
                "source_url": asset.get("source_url"), "attribution": asset.get("attribution")}
               for asset in assets]
    return {"passed": True, "checked_at": now(), "assets": records,
            "scope": "Recorded license policy check; third-party rights and license terms still apply."}
