import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from easy_tv import gemini_agent
from easy_tv.gemini_agent import TVAgent
from easy_tv import tv_tools
from easy_tv.tv_tools import TVTools, parse_audio, parse_inputs, parse_media, parse_window


def response(*parts):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}}]}


def call(name, args=None, identifier="call-1"):
    return {"functionCall": {"name": name, "args": args or {}, "id": identifier},
            "thoughtSignature": "preserve-this-signature"}


class Client:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def generate(self, contents, timeout=15):
        self.requests.append(copy.deepcopy(contents))
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        return value


class Tools:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.image = None

    def call(self, name, args, cancel):
        TVTools._validate(name, args)
        self.calls.append((name, args))
        if self.fail == name:
            raise RuntimeError("App launch could not be verified.")
        if name == "get_tv_state":
            return {"ok": True, "state": {"foreground": {"package": "launcher"}, "lessons": ["cricket"]}}, None
        if name == "inspect_screen":
            return {"ok": True, "visible_elements": [{"text": "Play"}]}, self.image
        return {"ok": True, "changed": True, "message": name + " completed."}, None


class AgentTests(unittest.TestCase):
    def create(self, replies):
        client = Client(replies)
        tools = Tools()
        agent = TVAgent(Mock(), threading.Lock(), client_factory=lambda: client, tools=tools)
        return agent, client, tools

    def test_compound_request_runs_actions_in_order(self):
        agent, client, tools = self.create([
            response(call("open_app", {"app": "netflix"})),
            response(call("adjust_volume", {"direction": "down", "steps": 2})),
            response({"text": "Done."})])
        result = agent.submit("Open Netflix and make it quieter", wait=2)
        self.assertTrue(result["ok"])
        self.assertEqual([name for name, _ in tools.calls], ["get_tv_state", "open_app", "adjust_volume"])
        self.assertIn("open_app completed", result["message"])
        self.assertIn("adjust_volume completed", result["message"])

    def test_function_ids_and_thought_signatures_are_preserved(self):
        agent, client, _ = self.create([response(call("open_app", {"app": "netflix"}, "unique-id")), response({"text": "Done"})])
        agent.submit("Netflix please", wait=2)
        history = client.requests[1]
        self.assertEqual(history[1]["parts"][0]["thoughtSignature"], "preserve-this-signature")
        self.assertEqual(history[2]["parts"][0]["functionResponse"]["id"], "unique-id")

    def test_raw_shell_tool_is_never_executed(self):
        agent, _, tools = self.create([response(call("adb_shell", {"command": "reboot"})), response({"text": "Done"})])
        result = agent.submit("Do something", wait=2)
        self.assertFalse(result["ok"])
        self.assertEqual([name for name, _ in tools.calls], ["get_tv_state"])
        self.assertIn("Unsupported TV tool", result["message"])

    def test_failure_skips_remaining_actions_in_same_batch(self):
        agent, _, tools = self.create([
            response(call("open_app", {"app": "netflix"}), call("press_button", {"button": "select"}, "second")),
            response({"text": "Everything succeeded"})])
        tools.fail = "open_app"
        result = agent.submit("Open Netflix and select something", wait=2)
        self.assertFalse(result["ok"])
        self.assertNotIn("press_button", [name for name, _ in tools.calls])
        self.assertIn("could not be verified", result["message"])

    def test_no_action_is_not_reported_as_successful_execution(self):
        agent, _, _ = self.create([response({"text": "Opened Netflix."})])
        result = agent.submit("Netflix please", wait=2)
        self.assertIn("No TV changes were made", result["message"])

    def test_read_only_question_uses_observed_state(self):
        agent, _, _ = self.create([response({"text": "The launcher is open."})])
        result = agent.submit("What is open?", wait=2)
        self.assertEqual(result["message"], "The launcher is open.")

    def test_image_is_only_attached_after_inspection(self):
        agent, client, tools = self.create([response(call("inspect_screen")), response({"text": "What should I select?"})])
        tools.image = {"mimeType": "image/jpeg", "data": "test-image"}
        agent.submit("Look at the screen", wait=2)
        self.assertNotIn("test-image", json.dumps(client.requests[0]))
        self.assertIn("test-image", json.dumps(client.requests[1]))

    def test_action_limit_stops_further_execution(self):
        agent, _, tools = self.create([
            response(call("press_button", {"button": "down"})),
            response(call("press_button", {"button": "down"}))])
        agent.max_actions = 1
        result = agent.submit("Navigate", wait=2)
        self.assertFalse(result["ok"])
        self.assertEqual(sum(name == "press_button" for name, _ in tools.calls), 1)

    def test_cancellation_after_network_response_prevents_action(self):
        entered, release = threading.Event(), threading.Event()
        class WaitingClient:
            def generate(self, contents, timeout=15):
                entered.set()
                release.wait(2)
                return response(call("open_app", {"app": "netflix"}))
        tools = Tools()
        agent = TVAgent(Mock(), threading.Lock(), client_factory=WaitingClient, tools=tools)
        agent.submit("Netflix please", wait=0)
        self.assertTrue(entered.wait(1))
        agent.cancel()
        release.set()
        agent.last.thread.join(2)
        self.assertEqual([name for name, _ in tools.calls], ["get_tv_state"])
        self.assertIn("Cancelled", agent.status()["message"])

    def test_api_failure_preserves_completed_action_feedback(self):
        agent, _, _ = self.create([response(call("open_app", {"app": "netflix"})), RuntimeError("Gemini unavailable")])
        result = agent.submit("Open Netflix then lower volume", wait=2)
        self.assertFalse(result["ok"])
        self.assertIn("open_app completed", result["message"])
        self.assertIn("Gemini unavailable", result["message"])

    def test_too_many_calls_rejected_before_execution(self):
        agent, _, tools = self.create([response(*(call("press_button", {"button": "down"}, str(i)) for i in range(6)))])
        result = agent.submit("Navigate", wait=2)
        self.assertFalse(result["ok"])
        self.assertEqual(len(tools.calls), 1)


AUDIO = """- STREAM_MUSIC:
   Muted: false
   Min: 0
   Max: 100
   Current: 2 (speaker): 44, 8 (headphone): 99, 40000000 (default): 30
   Devices: speaker
- STREAM_ALARM:
   Muted: true
   Max: 100
"""


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.teacher = Mock()
        self.teacher.lessons = {}
        self.tools = TVTools(self.teacher, threading.Lock())
        self.cancel = threading.Event()

    def test_audio_uses_active_device_not_default(self):
        self.assertEqual(parse_audio(AUDIO), {"muted": False, "min": 0, "max": 100,
                                             "device": "speaker", "current": 44, "percent": 44})

    def test_window_extracts_actual_foreground(self):
        self.assertEqual(parse_window("mCurrentFocus=Window{123 u0 com.example/.Main}"),
                         {"package": "com.example", "activity": ".Main"})

    def test_media_title_and_playback(self):
        state = parse_media("Media button session is tv.app/session\nstate=PlaybackState {state=3, position=1200, speed=1}\nmetadata:size=5, description=My Show, null, null")
        self.assertEqual(state["title"], "My Show")
        self.assertEqual(state["state"], "playing")

    def test_apple_session_without_media_button_owner_is_read(self):
        state = parse_media("""Media button session is null
  package=com.apple.atve.androidtv.appletv
  active=true
  state=PlaybackState {state=3, position=0, speed=1}
  metadata:size=1, description=null, null, null
""")
        self.assertEqual(state["package"], "com.apple.atve.androidtv.appletv")
        self.assertEqual(state["state"], "playing")
        self.assertNotIn("title", state)

    def test_inputs_match_active_token_and_cec_name(self):
        state = parse_inputs("""sessionStateMap:
android.os.Binder@123: SessionState
  inputId: com.droidlogic.tvinput/.services.Hdmi2InputService/HW6
mainSessionToken: android.os.Binder@123
CEC: display_name: PlayStation 5 power_status: 0 physical_address: 0x2000 port_id: 2
""")
        self.assertEqual(state["active_hdmi_port"], 2)
        self.assertEqual(state["devices"][0]["name"], "PlayStation 5")

    def test_rejects_unbounded_and_injected_arguments(self):
        for name, args in (("press_button", {"button": "down", "count": 100}),
                           ("press_button", {"button": "down; reboot"}),
                           ("set_volume", {"percent": True}),
                           ("set_volume", {"percent": 101}),
                           ("open_app", {"app": "netflix", "shell": "reboot"}),
                           ("exec", {"command": "reboot"})):
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                TVTools._validate(name, args)

    def test_absolute_volume_is_verified(self):
        with patch.object(tv_tools.tv, "execute", side_effect=[AUDIO, "", AUDIO.replace("speaker): 44", "speaker): 30")]) as execute:
            result, _ = self.tools.call("set_volume", {"percent": 30}, self.cancel)
            self.assertEqual(result["volume"]["percent"], 30)
            self.assertIn(["media", "volume", "--show", "--stream", "3", "--set", "30"], [c.args[0] for c in execute.call_args_list])

    def test_unmute_does_not_toggle_already_unmuted_tv(self):
        with patch.object(tv_tools.tv, "execute", side_effect=[AUDIO, AUDIO]) as execute:
            result, _ = self.tools.call("set_mute", {"muted": False}, self.cancel)
            self.assertEqual(execute.call_count, 2)
            self.assertEqual(result["message"], "Unmuted the TV.")

    def test_failed_verification_is_not_success(self):
        with patch.object(tv_tools.tv, "execute", side_effect=[AUDIO, "", AUDIO]):
            with self.assertRaisesRegex(RuntimeError, "did not reach"):
                self.tools.call("set_volume", {"percent": 30}, self.cancel)


class ClientTests(unittest.TestCase):
    def test_missing_key_reports_setup_step(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "not configured"):
                    gemini_agent.configuration(root)

    def test_api_errors_redact_key(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "config.json").write_text(json.dumps({"gemini": {"model": "gemini-test"}}))
            Path(root, "gemini-key").write_text("secret-test-key")
            client = gemini_agent.GeminiClient(root)
            error = HTTPError("https://example.com", 403, "Forbidden", {},
                              io.BytesIO(json.dumps({"error": {"message": "Invalid secret-test-key"}}).encode()))
            with patch.object(gemini_agent, "urlopen", side_effect=error):
                with self.assertRaises(RuntimeError) as raised:
                    client.generate([])
            self.assertNotIn("secret-test-key", str(raised.exception))
            self.assertIn("[redacted]", str(raised.exception))

    def test_transient_model_network_error_can_retry_without_tv_actions(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "config.json").write_text(json.dumps({"gemini": {"model": "gemini-test"}}))
            Path(root, "gemini-key").write_text("secret-test-key")
            client = gemini_agent.GeminiClient(root)
            result = Mock()
            result.__enter__ = Mock(return_value=result)
            result.__exit__ = Mock(return_value=False)
            result.read.return_value = json.dumps(response({"text": "Ready"})).encode()
            with patch.object(gemini_agent, "urlopen", side_effect=[TimeoutError(), result]) as network:
                with patch.object(gemini_agent.time, "sleep"):
                    self.assertTrue(client.generate([]).get("candidates"))
                self.assertEqual(network.call_count, 2)


if __name__ == "__main__":
    unittest.main()
