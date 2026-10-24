"""Read the public India availability page's structured data, without private APIs."""
import hashlib
import json
import re
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def apollo_from_html(html):
    script = re.search(r'''<script\b[^>]*\bid=["']__NUXT_DATA__["'][^>]*>(.*?)</script>''', html, re.S | re.I)
    if not script:
        raise RuntimeError("The public availability page did not expose its structured title data.")
    values = json.loads(script[1])
    if not isinstance(values, list) or len(values) > 100000:
        raise RuntimeError("Unsupported availability-page data format.")
    memo = {}

    def decode(index, depth=0):
        if type(index) is not int or depth > 80 or index >= len(values):
            raise RuntimeError("Invalid availability-page reference.")
        if index < 0:
            return None
        if index in memo:
            return memo[index]
        value = values[index]
        if isinstance(value, dict):
            result = {}
            memo[index] = result
            result.update({key: decode(item, depth + 1) for key, item in value.items()})
            return result
        if isinstance(value, list):
            if value and isinstance(value[0], str):
                if value[0] in ("Reactive", "ShallowReactive", "Ref", "ShallowRef") and len(value) == 2:
                    return decode(value[1], depth + 1)
                if value[0] == "Date":
                    return value[1]
                raise RuntimeError("Unsupported tagged availability data.")
            result = []
            memo[index] = result
            result.extend(decode(item, depth + 1) for item in value)
            return result
        return value

    reference = next((v["apollo:default"] for v in values if isinstance(v, dict) and "apollo:default" in v), None)
    if reference is None:
        raise RuntimeError("No public title catalogue was included in the page.")
    result = decode(reference)
    if not isinstance(result, dict):
        raise RuntimeError("The public catalogue format changed.")
    return result


def provider_name(package):
    technical = package.get("technicalName", "").lower()
    name = package.get("clearName", "").lower()
    if technical in ("netflix", "netflixbasicwithads") or name in ("netflix", "netflix standard with ads"):
        return "netflix"
    if technical in ("amazonprimevideo", "amazonprimevideowithads") or name in ("amazon prime video", "amazon prime video with ads"):
        return "prime video"
    if technical in ("hotstar", "jiohotstar") or name in ("hotstar", "jiohotstar"):
        return "hotstar"
    if technical == "appletvplus" or name in ("apple tv", "apple tv+"):
        return "apple tv"
    # In particular, amazonappletvplus is a paid Prime add-on, not base Prime.
    return None


def parse_page(html, query, kind="any", region="IN"):
    from .content_search import provider_url
    store = apollo_from_html(html)

    def resolve(value):
        for _ in range(5):
            if not isinstance(value, dict) or "__ref" not in value:
                return value
            value = store.get(value["__ref"], {})
        return {}

    root = store.get("ROOT_QUERY", {})
    search = next((value for key, value in root.items() if key.startswith("searchTitles(")
                   and re.search(r'"country"\s*:\s*"' + re.escape(region) + r'"', key)), None)
    if not isinstance(search, dict) or not isinstance(search.get("edges"), list):
        raise RuntimeError("The public title search format changed.")
    matches = []
    for edge in search["edges"][:12]:
        entity = resolve(edge.get("node", {}))
        title_type = {"SHOW": "series", "MOVIE": "movie"}.get(entity.get("objectType"))
        if not title_type or kind != "any" and title_type != kind:
            continue
        content = next((v for k, v in entity.items() if k.startswith("content(")), {})
        name = content.get("title")
        if not isinstance(name, str) or not name:
            continue
        year = str(content.get("originalReleaseYear", ""))
        if not re.fullmatch(r"(?:19|20)\d{2}", year):
            year = ""
        path = content.get("fullPath", "")
        source = "https://www.justwatch.com" + path if isinstance(path, str) and path.startswith("/" + region.lower() + "/") else ""
        offers = []
        seen = set()
        for key, values in entity.items():
            if not key.startswith("offers(") or not isinstance(values, list):
                continue
            for reference in values:
                offer = resolve(reference)
                if offer.get("country", region) != region:
                    continue
                package = resolve(offer.get("package", {}))
                provider = provider_name(package)
                access = {"FLATRATE": "subscription", "FREE": "free", "ADS": "free", "RENT": "rent", "BUY": "buy"}.get(offer.get("monetizationType"))
                url = provider_url(provider, offer.get("preAffiliatedStandardWebURL") or offer.get("standardWebURL"))
                identity = (provider, access, url)
                if not provider or not access or not url or identity in seen:
                    continue
                seen.add(identity)
                offers.append({"provider": provider, "access": access, "url": url,
                               "evidence": "JustWatch " + region + ": " + package.get("clearName", provider) + ", " + access,
                               "updated_at": offer.get("updatedAt")})
        identifier = hashlib.sha256((name.lower() + "|" + year + "|" + title_type).encode()).hexdigest()[:16]
        matches.append({"id": identifier, "title": name[:160], "year": year, "type": title_type,
                        "imdb_id": "", "offers": offers, "source_url": source,
                        "popularity": content.get("scoring", {}).get("tmdbPopularity", 0) or 0})
    sources = [{"url": item["source_url"], "title": item["title"] + " — JustWatch " + region} for item in matches if item["source_url"]]
    search_url = "https://www.justwatch.com/" + region.lower() + "/search?" + urlencode({"q": query})
    sources.append({"url": search_url, "title": "JustWatch " + region + " search"})
    exact = [m for m in matches if m["title"].casefold() == query.casefold()]
    included = [m for m in exact if any(o["access"] in ("subscription", "free") for o in m["offers"])]
    recommended = included[0]["id"] if len(included) == 1 else None
    return {"query": query, "region": region, "matches": matches, "sources": sources,
            "source_name": "JustWatch public " + region + " availability pages",
            "recommended_id": recommended,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "stremio": "Fallback after subscription services. Availability depends on configured add-ons."}


def imdb_from_detail(html, title):
    store = apollo_from_html(html)
    expected_type = "SHOW" if title["type"] == "series" else "MOVIE"
    for entity in store.values():
        if not isinstance(entity, dict) or entity.get("objectType") != expected_type:
            continue
        content = next((value for key, value in entity.items() if key.startswith("content(")), {})
        if content.get("title", "").casefold() != title["title"].casefold():
            continue
        if str(content.get("originalReleaseYear", "")) != title.get("year", ""):
            continue
        imdb = content.get("externalIds", {}).get("imdbId", "")
        if isinstance(imdb, str) and re.fullmatch(r"tt\d{7,10}", imdb):
            return imdb
    return ""


def enrich_stremio_id(title, region="IN"):
    source = title.get("source_url", "")
    if not source.startswith("https://www.justwatch.com/" + region.lower() + "/"):
        return ""
    try:
        with urlopen(Request(source, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as response:
            if not response.geturl().startswith("https://www.justwatch.com/" + region.lower() + "/"):
                return ""
            return imdb_from_detail(response.read(3_000_000).decode("utf-8"), title)
    except (OSError, ValueError, RuntimeError):
        return ""


def search_public(query, kind="any", region="IN"):
    url = "https://www.justwatch.com/" + region.lower() + "/search?" + urlencode({"q": query})
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=20) as response:
            if not response.geturl().startswith("https://www.justwatch.com/"):
                raise RuntimeError("The availability page redirected outside its expected site.")
            html = response.read(3_000_000).decode("utf-8")
        return parse_page(html, query, kind, region)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise RuntimeError("The public regional availability lookup is unavailable. Try again shortly.") from None
