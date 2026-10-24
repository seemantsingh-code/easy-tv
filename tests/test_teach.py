import json
import base64
from pathlib import Path
import queue
import tempfile
import threading
import unittest

from easy_tv import teach


HOME = {"package": "launcher", "focus": {"resource-id": "launcher:id/settings", "text": "Settings"}}


class Stream:
    def __init__(self):
        self.queue = queue.Queue()

    def __iter__(self):
        while True:
            item = self.queue.get(timeout=5)
            if item is None:
                return
            yield item

    def close(self):
        pass


class Process:
    def __init__(self):
        self.stdout = Stream()
        self.dead = False

    def poll(self):
        return 0 if self.dead else None

    def terminate(self):
        self.dead = True
        self.stdout.queue.put(None)

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminate()


class Adapter:
    def __init__(self):
        self.pressed = []
        self.home_count = 0
        self.screen = HOME
        self.process = None

    def reset_home(self):
        self.home_count += 1

    def snapshot(self):
        return self.screen

    def events(self):
        self.process = Process()
        return self.process

    def press(self, key, long=False):
        self.pressed.append((key, long))


def event(time, key, value="DOWN", device=4):
    return f"[ {time:.6f}] /dev/input/event{device}: EV_KEY {key} {value}"


class TeachTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.adapter = Adapter()
        self.manager = teach.TeachManager(self.temp.name, self.adapter)

    def tearDown(self):
        self.manager.close()
        if self.manager.worker:
            self.manager.worker.join(timeout=2)
        self.temp.cleanup()

    def record(self):
        self.manager.start()
        self.manager.ingest(event(100, "KEY_DOWN"))
        self.manager.ingest(event(100.1, "KEY_DOWN", "UP"))
        self.manager.ingest(event(100.4, "KEY_ENTER"))
        self.manager.ingest(event(100.5, "KEY_ENTER", "UP"))

    def recipe(self, steps):
        return {"start": HOME, "steps": steps}

    def test_physical_press_mapping_and_delays(self):
        self.record()
        self.assertEqual([s["key"] for s in self.manager.steps], ["DPAD_DOWN", "DPAD_CENTER"])
        self.assertEqual(self.manager.steps[1]["wait"], 0.4)

    def test_press_release_is_not_two_actions(self):
        self.record()
        self.assertEqual(len(self.manager.steps), 2)

    def test_long_press(self):
        self.manager.start()
        self.manager.ingest(event(100, "KEY_ENTER"))
        self.manager.ingest(event(101, "KEY_ENTER", "UP"))
        self.assertTrue(self.manager.steps[0]["long"])

    def test_duplicate_device_forwarding_is_ignored(self):
        self.manager.start()
        self.manager.ingest(event(100, "KEY_RIGHT", device=4))
        self.manager.ingest(event(100.01, "KEY_RIGHT", device=3))
        self.assertEqual(len(self.manager.steps), 1)

    def test_human_thinking_delay_is_bounded(self):
        self.manager.start()
        self.manager.ingest(event(100, "KEY_RIGHT"))
        self.manager.ingest(event(170, "KEY_LEFT"))
        self.assertEqual(self.manager.steps[-1]["wait"], 20)

    def test_save_persists_and_reload_keeps_lesson(self):
        self.record()
        message = self.manager.save("Cricket")
        self.assertIn("Saved cricket", message)
        loaded = teach.TeachManager(self.temp.name, self.adapter)
        self.assertEqual(len(loaded.lessons["cricket"]["steps"]), 2)
        self.assertIn("screen", loaded.lessons["cricket"]["steps"][-1])

    def test_custom_names_cannot_override_builtin_commands(self):
        self.record()
        for name in ("PlayStation", "YouTube", "status", "../../oops", "teach", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.manager.save(name)

    def test_existing_lesson_is_not_overwritten(self):
        self.record()
        self.manager.save("Cricket")
        self.manager.start()
        self.manager.ingest(event(100, "KEY_RIGHT"))
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.manager.save("Cricket")

    def test_replace_requires_explicit_command(self):
        self.record()
        self.manager.save("Cricket")
        self.manager.handle("Replace Cricket")
        self.manager.ingest(event(100, "KEY_LEFT"))
        self.manager.handle("Save")
        self.assertEqual(self.manager.lessons["cricket"]["steps"][0]["key"], "DPAD_LEFT")

    def test_cancel_replacement_keeps_original(self):
        self.record()
        self.manager.save("Cricket")
        self.manager.handle("Replace Cricket")
        self.manager.cancel()
        self.assertEqual(len(self.manager.lessons["cricket"]["steps"]), 2)

    def test_cancel_discards_without_writing(self):
        self.record()
        self.manager.cancel()
        self.assertEqual(self.manager.state, "idle")
        self.assertFalse((Path(self.temp.name) / "lessons.json").exists())

    def test_unknown_button_stops_and_cannot_save_partial_lesson(self):
        self.record()
        self.manager.ingest(event(101, "KEY_VOICECOMMAND"))
        self.assertEqual(self.manager.state, "draft")
        with self.assertRaisesRegex(ValueError, "cannot be replayed"):
            self.manager.save("Cricket")

    def test_empty_recording_cannot_save(self):
        self.manager.start()
        with self.assertRaisesRegex(ValueError, "No remote presses"):
            self.manager.save("Cricket")

    def test_normal_voice_commands_blocked_while_recording(self):
        self.manager.start()
        with self.assertRaisesRegex(ValueError, "Teach mode is active"):
            self.manager.handle("YouTube")

    def test_replay_resets_home_and_sends_keys_once(self):
        self.manager.lessons["cricket"] = self.recipe([
            {"key": "DPAD_DOWN", "wait": 0}, {"key": "DPAD_CENTER", "wait": 0, "screen": HOME}])
        self.assertIn("Running cricket", self.manager.handle("Cricket"))
        self.manager.worker.join(timeout=2)
        self.assertEqual(self.adapter.pressed, [("DPAD_DOWN", False), ("DPAD_CENTER", False)])
        self.assertEqual(self.adapter.home_count, 1)
        self.assertEqual(self.manager.handle("status"), "Finished cricket.")

    def test_wrong_start_screen_sends_no_recorded_keys(self):
        self.adapter.screen = {"package": "wrong.app"}
        self.manager.screen_timeout = 0
        self.manager.lessons["cricket"] = self.recipe([{"key": "DPAD_CENTER", "wait": 0}])
        self.manager.play("cricket")
        self.manager.worker.join(timeout=2)
        self.assertFalse(self.adapter.pressed)
        self.assertIn("screen changed", self.manager.status())

    def test_failed_checkpoint_stops_before_next_key(self):
        self.manager.screen_timeout = 0
        self.manager.lessons["cricket"] = self.recipe([
            {"key": "DPAD_RIGHT", "wait": 0, "screen": {"package": "expected.app"}},
            {"key": "DPAD_CENTER", "wait": 0}])
        self.manager.play("cricket")
        self.manager.worker.join(timeout=2)
        self.assertEqual(self.adapter.pressed, [("DPAD_RIGHT", False)])
        self.assertIn("screen changed", self.manager.status())

    def test_cancel_replay_interrupts_wait(self):
        self.manager.lessons["cricket"] = self.recipe([{"key": "DPAD_CENTER", "wait": 20}])
        self.manager.play("cricket")
        self.manager.cancel()
        self.manager.worker.join(timeout=2)
        self.assertFalse(self.adapter.pressed)
        self.assertIn("Cancelled", self.manager.status())

    def test_tampered_recipe_cannot_run_shell_commands(self):
        self.manager.lessons["cricket"] = self.recipe([{"key": "HOME;reboot", "wait": 0}])
        with self.assertRaises(ValueError):
            self.manager.play("cricket")
        self.assertFalse(self.adapter.pressed)

    def test_lesson_list(self):
        self.assertIn("No lessons", self.manager.handle("lessons"))
        self.manager.lessons["cricket"] = self.recipe([{"key": "DPAD_DOWN", "wait": 0}])
        self.assertEqual(self.manager.handle("lessons"), "Lessons: cricket.")

    def test_record_alias_starts_teach_mode(self):
        self.assertIn("Recording from Home", self.manager.handle("Record"))
        self.assertEqual(self.manager.state, "recording")

    def test_learn_alias_can_include_lesson_name(self):
        self.manager.handle("Learn Cricket")
        self.manager.ingest(event(100, "KEY_ENTER"))
        self.assertIn("Saved cricket", self.manager.handle("Save"))

    def test_stable_screen_match(self):
        self.assertTrue(teach.screen_matches(HOME, HOME))
        self.assertFalse(teach.screen_matches(HOME, {"package": "launcher", "focus": {"text": "Buy"}}))

    def test_visual_check_detects_changed_launcher(self):
        left = {**HOME, "visual": base64.b64encode(bytes(64 * 36)).decode()}
        right = {**HOME, "visual": base64.b64encode(bytes([200]) * (64 * 36)).decode()}
        self.assertTrue(teach.screen_matches(left, left))
        self.assertFalse(teach.screen_matches(left, right))
        self.assertFalse(teach.screen_matches(left, HOME))

    def test_hierarchy_extracts_focused_element(self):
        xml = '''<?xml version="1.0"?><hierarchy><node package="test.app" focused="false">
        <node package="test.app" focused="true" resource-id="test:id/play" text="Play"
        class="Button" bounds="[1,1][5,5]" /></node></hierarchy>'''
        screen = teach.screen_from_xml(xml)
        self.assertEqual(screen["package"], "test.app")
        self.assertEqual(screen["focus"]["text"], "Play")


if __name__ == "__main__":
    unittest.main()
