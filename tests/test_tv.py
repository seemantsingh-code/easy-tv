import subprocess
import unittest
from unittest.mock import patch
from easy_tv import tv


class TVTests(unittest.TestCase):
    def setUp(self):
        settings = tv.load()
        settings["tv"]["address"] = "192.0.2.10:5555"
        patcher = patch.object(tv, "load", return_value=settings)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_voice_commands(self):
        self.assertEqual(tv.plan("Open YouTube.")[2], "com.google.android.youtube.tv")
        self.assertEqual(tv.plan("Switch to PlayStation 5")[0], "am")
        self.assertEqual(tv.plan("Switch to play Station"), tv.plan("ps5"))
        self.assertEqual(tv.plan("open you tube"), tv.plan("youtube"))
        self.assertIn("Hdmi1InputService%2FHW5", tv.plan("ps5")[-1])
        self.assertEqual(tv.plan("turn off")[-1], "KEYCODE_SLEEP")
        self.assertEqual(tv.plan("pause")[-1], "KEYCODE_MEDIA_PAUSE")

    def test_rejects_arbitrary_commands(self):
        for command in ("youtube; reboot", "$(reboot)", "volume up 99", "install malware", ""):
            with self.assertRaises(ValueError):
                tv.plan(command)

    def test_short_commands_stay_supported(self):
        for command in ("PlayStation", "YouTube", "Netflix", "inputs", "louder", "quieter",
                        "pause", "play", "home", "back", "off"):
            with self.subTest(command=command):
                self.assertTrue(tv.plan(command))

    def test_clear_feedback(self):
        self.assertEqual(tv.feedback(tv.plan("PlayStation")), "Switched to PlayStation.")
        self.assertEqual(tv.feedback(tv.plan("YouTube")), "Opened YouTube.")
        self.assertEqual(tv.feedback(tv.plan("inputs")), "Opened Inputs.")
        self.assertEqual(tv.feedback(tv.plan("volume down three steps")), "Lowered volume by 3 steps.")
        self.assertEqual(tv.feedback(tv.plan("select")), "Selected.")
        self.assertEqual(tv.feedback(tv.plan("mute")), "Toggled mute.")

    def test_reconnect_then_send_once(self):
        with patch.object(tv, "run", side_effect=[RuntimeError("offline"), "connected", "device", ""]) as run:
            tv.execute(tv.plan("mute"))
            self.assertEqual(run.call_count, 4)
            self.assertEqual(run.call_args.args[0][-1], "input keyevent KEYCODE_VOLUME_MUTE")

    def test_never_replays_failed_action(self):
        with patch.object(tv, "run", side_effect=["device", RuntimeError("timeout")]) as run:
            with self.assertRaises(RuntimeError):
                tv.execute(tv.plan("mute"))
            self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
