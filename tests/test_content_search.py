import base64
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from easy_tv.content_search import ContentSearch, native_url, provider_url, validate_search
from easy_tv.tv_tools import TVTools
from easy_tv import tv_tools
from easy_tv.justwatch_public import parse_page, provider_name, imdb_from_detail


APPLE_URL = "https://tv.apple.com/in/show/silo/umc.cmc.3yksgc857px0k0rqe5zd4jice"


def grounded(offers=None):
    result = {"matches": [{"title": "Silo", "year": "2023", "type": "series", "imdb_id": "tt14688458",
                           "offers": offers if offers is not None else [{"provider": "apple tv", "access": "subscription", "url": APPLE_URL,
                               "evidence": "Official India Apple subscription page."}]}]}
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(result)}]},
                            "groundingMetadata": {"webSearchQueries": ["Silo India streaming"],
                                "groundingChunks": [{"web": {"uri": APPLE_URL, "title": "Apple TV"}}]}}]}


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.search = ContentSearch(self.temp.name)
        self.result = validate_search(grounded(), "Silo")
        self.search.cache["any:silo"] = {"at": time.time(), "result": self.result}
        self.identifier = self.result["matches"][0]["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_included_subscription_precedes_stremio(self):
        title, provider, offer = self.search.choose(self.identifier)
        self.assertEqual(provider, "apple tv")
        self.assertEqual(offer["url"], APPLE_URL)

    def test_rental_and_purchase_fall_back_to_stremio(self):
        for access in ("rent", "buy", "unknown"):
            self.result["matches"][0]["offers"][0]["access"] = access
            _, provider, offer = self.search.choose(self.identifier)
            self.assertEqual(provider, "stremio")
            self.assertIsNone(offer)

    def test_explicit_stremio_fallback_is_available(self):
        self.assertEqual(self.search.choose(self.identifier, "stremio")[1], "stremio")

    def test_provider_without_included_offer_is_refused(self):
        with self.assertRaisesRegex(ValueError, "No included"):
            self.search.choose(self.identifier, "netflix")

    def test_no_search_evidence_is_not_availability(self):
        value = grounded()
        value["candidates"][0].pop("groundingMetadata")
        with self.assertRaisesRegex(RuntimeError, "No live search evidence"):
            validate_search(value, "Silo")

    def test_expired_results_cannot_open(self):
        self.search.cache["any:silo"]["at"] = time.time() - 5 * 3600
        with self.assertRaisesRegex(ValueError, "expired"):
            self.search.title(self.identifier)

    def test_only_server_issued_title_ids_are_allowed(self):
        for value in ("Silo", "../../file", "tt14688458", "https://example.com"):
            with self.assertRaises(ValueError):
                self.search.title(value)

    def test_current_cache_avoids_extra_search_calls(self):
        with patch("easy_tv.content_search.urlopen") as network:
            result = self.search.search("Silo")
            self.assertTrue(result["from_cache"])
            network.assert_not_called()

    def test_malicious_provider_urls_are_discarded(self):
        for url in ("https://tv.apple.com.evil.example/show/silo/umc.cmc.test",
                    "https://user:password@tv.apple.com/in/show/silo/umc.cmc.test",
                    "http://tv.apple.com/in/show/silo/umc.cmc.test",
                    "https://127.0.0.1/show/silo/umc.cmc.test",
                    "file:///etc/passwd", "https://tv.apple.com:bad/show/silo/umc.cmc.test"):
            self.assertIsNone(provider_url("apple tv", url))

    def test_unofficial_offers_are_removed(self):
        value = grounded([{"provider": "netflix", "access": "subscription", "url": "https://evil.example/title/123456"}])
        self.assertEqual(validate_search(value, "Silo")["matches"][0]["offers"], [])

    def test_apple_link_is_content_page(self):
        title = self.result["matches"][0]
        self.assertEqual(native_url("apple tv", title["offers"][0], title), APPLE_URL)

    def test_stremio_series_does_not_guess_episode(self):
        title = self.result["matches"][0]
        self.assertEqual(native_url("stremio", None, title), "stremio:///detail/series/tt14688458")

    def test_stremio_movie_can_attempt_configured_autoplay(self):
        title = {"title": "Example", "type": "movie", "imdb_id": "tt1234567"}
        self.assertEqual(native_url("stremio", None, title), "stremio:///detail/movie/tt1234567/tt1234567?autoPlay=true")

    def test_stremio_without_imdb_uses_search(self):
        title = {"title": "Example Show", "type": "series", "imdb_id": ""}
        self.assertEqual(native_url("stremio", None, title), "stremio:///search?search=Example+Show")

    def test_netflix_movie_and_series_have_different_entry_points(self):
        offer = {"url": "https://www.netflix.com/title/12345678"}
        self.assertIn("/watch/", native_url("netflix", offer, {"type": "movie"}))
        self.assertIn("/title/", native_url("netflix", offer, {"type": "series"}))


class ContentToolTests(unittest.TestCase):
    def setUp(self):
        self.teacher = Mock()
        self.teacher.lessons = {}
        self.tools = TVTools(self.teacher, threading.Lock())
        self.temp = tempfile.TemporaryDirectory()
        self.tools.content = ContentSearch(self.temp.name)
        self.result = validate_search(grounded(), "Silo")
        self.title = self.result["matches"][0]
        self.identifier = self.title["id"]
        self.tools.content.cache["any:silo"] = {"at": time.time(), "result": self.result}
        self.cancel = threading.Event()

    def tearDown(self):
        self.temp.cleanup()

    def opened(self):
        self.tools.content.last_open = {"title_id": self.identifier, "provider": "apple tv", "at": time.monotonic()}

    def test_open_title_does_not_claim_playback(self):
        with patch.object(tv_tools.tv, "execute", side_effect=["Starting", "mCurrentFocus=Window{123 u0 com.apple.atve.androidtv.appletv/.MainActivity}"]):
            result, _ = self.tools.call("open_content", {"title_id": self.identifier}, self.cancel)
        self.assertFalse(result["playback_verified"])
        self.assertEqual(result["provider"], "apple tv")
        self.assertNotIn("Playing", result["message"])

    def test_netflix_uses_explicit_tv_entry_point_and_string_source(self):
        self.title["type"] = "movie"
        self.title["offers"] = [{"provider": "netflix", "access": "subscription",
                                  "url": "https://www.netflix.com/title/81199139"}]
        with patch.object(tv_tools.tv, "execute", side_effect=["Starting", "mCurrentFocus=Window{123 u0 com.netflix.ninja/.MainActivity}"]) as execute:
            result, _ = self.tools.call("open_content", {"title_id": self.identifier}, self.cancel)
        command = execute.call_args_list[0].args[0]
        self.assertIn("https://www.netflix.com/watch/81199139", command)
        self.assertEqual(command[command.index("--es"):command.index("--es") + 3], ["--es", "source", "30"])
        self.assertEqual(command[command.index("-n") + 1], "com.netflix.ninja/.MainActivity")
        self.assertIn("-W", command)
        self.assertFalse(result["playback_verified"])

    def test_actual_title_and_playing_state_are_required(self):
        self.opened()
        media = "Media button session is com.apple.atve.androidtv.appletv/session\nstate=PlaybackState {state=3, position=1200, speed=1}\nmetadata:size=5, description=Silo, null, null"
        with patch.object(tv_tools.tv, "execute", return_value=media):
            result, _ = self.tools.call("verify_content_playback", {"title_id": self.identifier}, self.cancel)
        self.assertTrue(result["confirmed"])
        self.assertTrue(result["goal_verified"])

    def test_trailer_is_not_the_requested_programme(self):
        self.opened()
        media = "Media button session is com.apple.atve.androidtv.appletv/session\nstate=PlaybackState {state=3, position=1200, speed=1}\nmetadata:size=5, description=Silo Trailer, null, null"
        with patch.object(tv_tools.tv, "execute", return_value=media):
            result, _ = self.tools.call("verify_content_playback", {"title_id": self.identifier}, self.cancel)
        self.assertFalse(result["confirmed"])

    def test_hidden_title_only_confirms_app_playback(self):
        self.opened()
        media = "Media button session is null\n  package=com.apple.atve.androidtv.appletv\n  active=true\n  state=PlaybackState {state=3, position=0, speed=1}\n  metadata:size=1, description=null, null, null"
        with patch.object(tv_tools.tv, "execute", return_value=media):
            result, _ = self.tools.call("verify_content_playback", {"title_id": self.identifier}, self.cancel)
        self.assertTrue(result["confirmed"])
        self.assertFalse(result["playback_verified"])
        self.assertEqual(result["title_verification"], "unavailable")
        self.assertEqual(result["message"], "Apple TV playback started.")

    def test_other_apps_playback_is_not_success(self):
        self.opened()
        media = "Media button session is in.startv.hotstar/session\nstate=PlaybackState {state=3, position=1200, speed=1}\nmetadata:size=5, description=Silo, null, null"
        with patch.object(tv_tools.tv, "execute", return_value=media):
            result, _ = self.tools.call("verify_content_playback", {"title_id": self.identifier}, self.cancel)
        self.assertFalse(result["confirmed"])

    def test_search_text_rejects_shell_payload(self):
        with patch.object(tv_tools.tv, "execute") as execute:
            with self.assertRaises(ValueError):
                self.tools.call("enter_search_text", {"text": "Silo; reboot"}, self.cancel)
            execute.assert_not_called()


def public_page(include_channel=True, include_imdb=False):
    store = {
        "ROOT_QUERY": {"searchTitles({\"country\":\"IN\"})": {"edges": [{"node": {"__ref": "Show:silo"}}]}},
        "Show:silo": {"objectType": "SHOW", "content({\"country\":\"IN\"})": {
            "title": "Silo", "originalReleaseYear": 2023, "fullPath": "/in/tv-show/silo",
            "scoring": {"tmdbPopularity": 170}},
            "offers({\"country\":\"IN\"})": [{"__ref": "Offer:apple"}, {"__ref": "Offer:channel"}]},
        "Offer:apple": {"country": "IN", "package": {"__ref": "Package:apple"},
                        "monetizationType": "FLATRATE", "standardWebURL": APPLE_URL},
        "Package:apple": {"clearName": "Apple TV", "technicalName": "appletvplus"},
        "Offer:channel": {"country": "IN", "package": {"__ref": "Package:channel"},
                          "monetizationType": "FLATRATE", "standardWebURL": "https://app.primevideo.com/detail?gti=example"},
        "Package:channel": {"clearName": "Apple TV Amazon Channel", "technicalName": "amazonappletvplus"},
    }
    if include_imdb:
        store["Show:silo"]['content({"country":"IN"})']["externalIds"] = {"imdbId": "tt14688458"}
    values = []
    def encode(value):
        index = len(values)
        values.append(None)
        if isinstance(value, dict):
            encoded = {key: encode(item) for key, item in value.items()}
        elif isinstance(value, list):
            encoded = [encode(item) for item in value]
        else:
            encoded = value
        values[index] = encoded
        return index
    encode({"apollo:default": store})
    return '<script type="application/json" id="__NUXT_DATA__">' + json.dumps(values) + '</script>'


class PublicPageTests(unittest.TestCase):
    def test_public_region_data_and_provider_are_resolved(self):
        result = parse_page(public_page(), "Silo", "series")
        self.assertEqual(result["matches"][0]["title"], "Silo")
        self.assertEqual(result["matches"][0]["offers"][0]["provider"], "apple tv")
        self.assertEqual(result["region"], "IN")
        self.assertEqual(result["recommended_id"], result["matches"][0]["id"])

    def test_prime_addon_is_not_base_prime(self):
        result = parse_page(public_page(), "Silo")
        self.assertEqual(len(result["matches"][0]["offers"]), 1)
        self.assertIsNone(provider_name({"technicalName": "amazonappletvplus", "clearName": "Apple TV Amazon Channel"}))

    def test_kind_filter_is_respected(self):
        self.assertEqual(parse_page(public_page(), "Silo", "movie")["matches"], [])

    def test_unsupported_page_is_not_guessed(self):
        with self.assertRaises(RuntimeError):
            parse_page("<html>No title data</html>", "Silo")

    def test_other_country_page_is_not_treated_as_india(self):
        with self.assertRaises(RuntimeError):
            parse_page(public_page().replace("IN", "US"), "Silo")

    def test_stremio_id_is_resolved_from_matching_title_and_year(self):
        title = {"title": "Silo", "year": "2023", "type": "series"}
        self.assertEqual(imdb_from_detail(public_page(include_imdb=True), title), "tt14688458")
        title["year"] = "2017"
        self.assertEqual(imdb_from_detail(public_page(include_imdb=True), title), "")


if __name__ == "__main__":
    unittest.main()
