"""Grounded regional content discovery and provider-restricted TV deep links."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .config import home, load
from . import tv

PROVIDERS = ("netflix", "prime video", "hotstar", "apple tv")
DOMAINS = {
    "netflix": {"www.netflix.com", "netflix.com"},
    "prime video": {"www.primevideo.com", "primevideo.com", "app.primevideo.com", "watch.amazon.com", "www.amazon.in", "amazon.in"},
    "hotstar": {"www.hotstar.com", "hotstar.com", "www.jiohotstar.com", "jiohotstar.com"},
    "apple tv": {"tv.apple.com"},
}


def provider_url(provider, value):
    """Only accept official, credential-free content URLs for a known provider."""
    if not isinstance(provider, str) or provider not in DOMAINS or not isinstance(value, str) or len(value) > 1500:
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if (parts.scheme != "https" or parts.hostname not in DOMAINS[provider]
            or parts.username or parts.password or port not in (None, 443)
            or any(c in value for c in "\r\n\x00")):
        return None
    path = parts.path
    if provider == "netflix":
        match = re.search(r"/(?:title|watch)/(\d{5,12})(?:/|$)", path)
        if not match:
            return None
        return "https://www.netflix.com/title/" + match[1]
    if provider == "apple tv" and not re.search(r"/(?:show|movie|episode)/[^/]+/umc\.cmc\.[a-z0-9]+", path):
        return None
    if provider == "hotstar" and not re.search(r"/(?:movies|shows|tv|sports)/[^/]+/", path):
        return None
    if provider == "prime video" and not ("/detail" in path or "/dp/" in path or "/gp/video/" in path):
        return None
    return urlunsplit(("https", parts.hostname, path, parts.query, ""))


def native_url(provider, offer, title, autoplay=True):
    if provider == "stremio":
        identifier = title.get("imdb_id", "")
        if re.fullmatch(r"tt\d{7,10}", identifier):
            kind = title["type"]
            video = "/" + identifier if kind == "movie" else ""
            return f"stremio:///detail/{kind}/{identifier}{video}" + ("?autoPlay=true" if autoplay and kind == "movie" else "")
        return "stremio:///search?" + urlencode({"search": title["title"]})
    value = provider_url(provider, offer.get("url"))
    if not value:
        raise ValueError("No verified official content link is available for this app.")
    parts = urlsplit(value)
    if provider == "netflix" and autoplay and title["type"] == "movie":
        return value.replace("/title/", "/watch/", 1)
    if provider == "prime video" and parts.hostname not in ("app.primevideo.com", "watch.amazon.com"):
        asin = re.search(r"(?:/detail/|/dp/|/gp/video/detail/)([A-Z0-9]{10})(?:/|$)", parts.path)
        if not asin:
            asin = re.search(r"/([A-Z0-9]{10})(?:/|$)", parts.path)
        if asin:
            return "https://app.primevideo.com/detail?" + urlencode({"asin": asin[1]})
        raise ValueError("Prime Video did not supply a compatible title link. Search inside the app instead.")
    if provider == "hotstar":
        return urlunsplit(("https", "www.hotstar.com", parts.path, parts.query, ""))
    return value


def parse_json_text(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def validate_search(response, query):
    candidates = response.get("candidates", [])
    if not candidates:
        raise RuntimeError("The availability search returned no results.")
    candidate = candidates[0]
    grounding = candidate.get("groundingMetadata", {})
    chunks = grounding.get("groundingChunks", [])
    sources = [{"url": c["web"]["uri"], "title": c["web"].get("title", "")}
               for c in chunks if isinstance(c, dict) and isinstance(c.get("web"), dict) and c["web"].get("uri")]
    if not sources or not grounding.get("webSearchQueries"):
        raise RuntimeError("No live search evidence was returned. I cannot confirm current streaming availability.")
    text = " ".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []) if not p.get("thought"))
    try:
        result = parse_json_text(text)
    except (ValueError, TypeError):
        raise RuntimeError("The streaming search did not return a readable title list. Try the title and year.") from None
    if not isinstance(result, dict) or not isinstance(result.get("matches"), list):
        raise RuntimeError("The streaming search did not return a valid title list.")
    matches = []
    for item in result["matches"][:5]:
        if not isinstance(item, dict) or item.get("type") not in ("movie", "series"):
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            continue
        year = str(item.get("year", ""))[:4]
        if not re.fullmatch(r"(?:19|20)\d{2}", year):
            year = ""
        imdb = item.get("imdb_id", "")
        if not isinstance(imdb, str) or not re.fullmatch(r"tt\d{7,10}", imdb):
            imdb = ""
        offers = []
        raw_offers = item.get("offers", [])
        for offer in (raw_offers if isinstance(raw_offers, list) else [])[:12]:
            if not isinstance(offer, dict):
                continue
            provider, access = offer.get("provider"), offer.get("access")
            url = provider_url(provider, offer.get("url"))
            if not url or access not in ("subscription", "free", "rent", "buy", "unknown"):
                continue
            offers.append({"provider": provider, "access": access, "url": url,
                           "evidence": str(offer.get("evidence", ""))[:240]})
        title_id = hashlib.sha256((title.lower() + "|" + year + "|" + item["type"]).encode()).hexdigest()[:16]
        matches.append({"id": title_id, "title": title.strip(), "year": year,
                        "type": item["type"], "imdb_id": imdb, "offers": offers})
    return {"query": query, "region": "IN", "matches": matches, "sources": sources[:12],
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "stremio": "Fallback after subscription services. Stream availability depends on configured add-ons."}


class ContentSearch:
    def __init__(self, root=None):
        self.root = Path(root) if root else home()
        self.settings = load(self.root)["content"]
        self.path = self.root / "content-cache.json"
        self.cache = {}
        if self.path.exists():
            try:
                self.cache = json.loads(self.path.read_text())
            except (OSError, ValueError):
                self.cache = {}
        self.last_open = None

    def _persist(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".json.tmp")
        with open(temporary, "w") as handle:
            os.chmod(temporary, 0o600)
            json.dump(self.cache, handle)
        os.replace(temporary, self.path)

    def search(self, query, kind="any"):
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 120 or kind not in ("any", "movie", "series"):
            raise ValueError("Search using a title of 2 to 120 characters.")
        key = kind + ":" + " ".join(query.lower().split())
        cached = self.cache.get(key)
        if cached and cached["result"].get("region") == self.settings["region"] and time.time() - cached.get("at", 0) < self.settings["cache_seconds"]:
            return {**cached["result"], "from_cache": True}
        from .justwatch_public import search_public
        result = search_public(query.strip(), kind, self.settings["region"])
        self.cache[key] = {"at": time.time(), "result": result}
        # Keep a small local cache without storing prompts, API keys, or images.
        self.cache = dict(sorted(self.cache.items(), key=lambda pair: pair[1].get("at", 0))[-50:])
        self._persist()
        return result

    def title(self, title_id):
        if not isinstance(title_id, str) or not re.fullmatch(r"[a-f0-9]{16}", title_id):
            raise ValueError("Choose a title ID returned by search_content.")
        for cached in reversed(list(self.cache.values())):
            if cached["result"].get("region") != self.settings["region"] or time.time() - cached.get("at", 0) > self.settings["cache_seconds"]:
                continue
            for title in cached.get("result", {}).get("matches", []):
                if title.get("id") == title_id:
                    return title
        raise ValueError("The title search expired. Search for it again first.")

    def choose(self, title_id, provider="auto"):
        title = self.title(title_id)
        if provider not in (*self.settings["providers"], "auto", *([self.settings["fallback"]] if self.settings["fallback"] else [])):
            raise ValueError("Unsupported content provider.")
        eligible = [o for o in title["offers"] if o["access"] in ("subscription", "free") and o["provider"] in self.settings["providers"]]
        eligible.sort(key=lambda offer: self.settings["providers"].index(offer["provider"]))
        if provider == "auto":
            provider = eligible[0]["provider"] if eligible else self.settings["fallback"]
        if provider is None:
            raise ValueError("No included offer found; fallback is disabled.")
        if provider == "stremio":
            if not title.get("imdb_id"):
                from .justwatch_public import enrich_stremio_id
                title["imdb_id"] = enrich_stremio_id(title, self.settings["region"])
                if title["imdb_id"]:
                    self._persist()
            return title, provider, None
        offer = next((o for o in eligible if o["provider"] == provider), None)
        if not offer:
            raise ValueError("No included subscription offer was verified for that provider. Use Stremio as fallback; paid offers are not selected automatically.")
        return title, provider, offer
