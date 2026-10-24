"""Record the physical TV remote and replay named lessons through ADB."""
import base64
import io
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

from . import tv
from .config import home, load

MAX_STEPS = int(load()["teach"]["max_steps"])
MAX_RECORD_SECONDS = load()["teach"]["max_record_seconds"]
MAX_REPLAY_SECONDS = load()["teach"]["max_replay_seconds"]
EVENT = re.compile(r"\[\s*([\d.]+)\]\s+(/dev/input/event\d+):\s+EV_KEY\s+(\S+)\s+(\S+)")
BUTTONS = {
    "KEY_UP": "DPAD_UP", "KEY_DOWN": "DPAD_DOWN", "KEY_LEFT": "DPAD_LEFT",
    "KEY_RIGHT": "DPAD_RIGHT", "KEY_ENTER": "DPAD_CENTER", "KEY_KPENTER": "DPAD_CENTER",
    "KEY_SELECT": "DPAD_CENTER", "KEY_OK": "DPAD_CENTER", "BTN_DPAD_UP": "DPAD_UP",
    "BTN_DPAD_DOWN": "DPAD_DOWN", "BTN_DPAD_LEFT": "DPAD_LEFT", "BTN_DPAD_RIGHT": "DPAD_RIGHT",
    "KEY_HOMEPAGE": "HOME", "KEY_HOME": "HOME", "KEY_BACK": "BACK", "KEY_ESC": "BACK",
    "KEY_MENU": "MENU", "KEY_VOLUMEUP": "VOLUME_UP", "KEY_VOLUMEDOWN": "VOLUME_DOWN",
    "KEY_MUTE": "VOLUME_MUTE", "KEY_PLAYPAUSE": "MEDIA_PLAY_PAUSE", "KEY_PLAY": "MEDIA_PLAY",
    "KEY_PAUSECD": "MEDIA_PAUSE", "KEY_STOPCD": "MEDIA_STOP", "KEY_NEXTSONG": "MEDIA_NEXT",
    "KEY_PREVIOUSSONG": "MEDIA_PREVIOUS", "KEY_REWIND": "MEDIA_REWIND",
    "KEY_FASTFORWARD": "MEDIA_FAST_FORWARD", "KEY_CHANNELUP": "CHANNEL_UP",
    "KEY_CHANNELDOWN": "CHANNEL_DOWN", "KEY_INFO": "INFO", "KEY_EPG": "GUIDE",
    "KEY_TV": "TV_INPUT", "KEY_SUBTITLE": "CAPTIONS", "BTN_A": "DPAD_CENTER",
    **{f"KEY_{n}": str(n) for n in range(10)},
}
ALLOWED_KEYS = frozenset(BUTTONS.values())
QUIET_KEYS = {"VOLUME_UP", "VOLUME_DOWN", "VOLUME_MUTE", "MEDIA_PLAY_PAUSE", "MEDIA_PLAY",
              "MEDIA_PAUSE", "MEDIA_STOP", "MEDIA_NEXT", "MEDIA_PREVIOUS", "MEDIA_REWIND",
              "MEDIA_FAST_FORWARD"}


def normal(text):
    return " ".join(text.lower().strip().rstrip(".!?").split())


def lesson_name(text):
    name = normal(text)
    if not re.fullmatch(r"[a-z][a-z0-9 ]{0,31}", name):
        raise ValueError("Choose a short name using letters and numbers, such as Cricket.")
    if name.split()[0] in {"teach", "record", "learn", "save", "replace", "forget", "cancel", "lessons", "help", "recording", "recordings", "progress"}:
        raise ValueError("That name is a teach-mode command. Choose another name.")
    try:
        tv.plan(name)
    except ValueError:
        return name
    raise ValueError("That name already controls the TV. Choose a different lesson name.")


def screen_from_xml(xml):
    start = xml.find("<?xml")
    if start < 0:
        start = xml.find("<hierarchy")
    if start < 0:
        raise RuntimeError("The TV did not expose a screen hierarchy.")
    end = xml.rfind("</hierarchy>")
    root = ET.fromstring(xml[start:end + len("</hierarchy>")])
    nodes = list(root.iter("node"))
    package = next((n.get("package") for n in nodes if n.get("package")), "")
    if not package:
        raise RuntimeError("Cannot identify the current TV app.")
    focused = next((n for n in nodes if n.get("focused") == "true" and n.get("password") != "true"), None)
    screen = {"package": package}
    if focused is not None:
        screen["focus"] = {key: focused.get(key, "")[:160]
                           for key in ("resource-id", "class", "text", "content-desc", "bounds")}
    return screen


def screen_matches(expected, actual):
    if expected.get("package") != actual.get("package"):
        return False
    if "visual" in expected:
        try:
            left = base64.b64decode(expected["visual"], validate=True)
            right = base64.b64decode(actual.get("visual", ""), validate=True)
        except (ValueError, TypeError):
            return False
        if len(left) != 64 * 36 or len(right) != len(left):
            return False
        differences = [abs(a - b) for a, b in zip(left, right)]
        if sum(differences) / len(left) > 0.65 or sum(d > 18 for d in differences) / len(left) > 0.0045:
            return False
    focus = expected.get("focus")
    if not focus:
        return True
    current = actual.get("focus", {})
    # Dynamic text is deliberately checked: a changed recommendation should stop a
    # lesson instead of selecting different content under the same tile position.
    return all(current.get(key, "") == value for key, value in focus.items() if value)


class TVAdapter:
    def adb(self):
        config = load()["tv"]
        return [config["adb"], "-s", config["address"]]

    def reset_home(self):
        tv.execute(["input", "keyevent", "KEYCODE_HOME"])
        settings = load()["tv"]
        time.sleep(settings["home_settle_seconds"])
        keys = ["KEYCODE_DPAD_UP"] * settings["home_up_presses"] + ["KEYCODE_DPAD_LEFT"] * settings["home_left_presses"]
        if keys:
            tv.execute(["input", "keyevent"] + keys)

    def snapshot(self):
        tv.execute(["uiautomator", "dump", "--compressed", "/sdcard/tv-remote-teach.xml"])
        screen = screen_from_xml(tv.execute(["cat", "/sdcard/tv-remote-teach.xml"]))
        # FLauncher does not expose its tile focus through UIAutomator. Its static
        # screen can still be checked visually. Ignore the clock, which changes.
        if screen["package"] == load()["tv"]["launcher_package"]:
            from PIL import Image
            try:
                result = subprocess.run(self.adb() + ["exec-out", "screencap", "-p"],
                                        capture_output=True, timeout=8, check=True)
                with Image.open(io.BytesIO(result.stdout)) as image:
                    pixels = image.convert("L").resize((64, 36), Image.Resampling.BOX)
                    pixels.paste(0, (58, 0, 64, 5))
                    screen["visual"] = base64.b64encode(pixels.tobytes()).decode()
            except (OSError, subprocess.SubprocessError) as exc:
                raise RuntimeError("Cannot verify the launcher picture. Try Teach again.") from exc
        return screen

    def events(self):
        # Android's getevent buffers stdout through a pipe. A PTY makes each
        # physical press available immediately, including very short lessons.
        return subprocess.Popen(self.adb() + ["shell", "-tt", "getevent", "-ltq"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)

    def press(self, key, long=False):
        if key not in ALLOWED_KEYS:
            raise ValueError("Unsupported recorded button.")
        command = ["input", "keyevent"]
        if long:
            command.append("--longpress")
        tv.execute(command + ["KEYCODE_" + key])


class TeachManager:
    def __init__(self, root=None, adapter=None, action_lock=None):
        self.root = Path(root) if root else home()
        self.path = self.root / "lessons.json"
        self.adapter = adapter or TVAdapter()
        self.action_lock = action_lock or threading.Lock()
        self.lock = threading.RLock()
        self.observation_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.state = "idle"
        self.steps = []
        self.pending_name = None
        self.replacing = False
        self.process = None
        self.message = "No lesson is recording or running."
        self.last_event = None
        self.down = {}
        self.changed = time.monotonic()
        self.started = 0
        self.start_screen = None
        self.worker = None
        self.recording_error = None
        limits = load(root)["teach"]
        self.screen_timeout = limits["screen_timeout"]
        self.max_steps = limits["max_steps"]
        self.max_record_seconds = limits["max_record_seconds"]
        self.max_replay_seconds = limits["max_replay_seconds"]
        self.lessons = self._load()

    def _load(self):
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text())
        if data.get("version") != 1 or not isinstance(data.get("lessons"), dict):
            raise ValueError("The saved lessons file has an unsupported format.")
        for name, lesson in data["lessons"].items():
            lesson_name(name)
            self._validate(lesson)
        return data["lessons"]

    def _validate(self, lesson):
        if not isinstance(lesson, dict) or not isinstance(lesson.get("start"), dict):
            raise ValueError("Invalid saved lesson.")
        steps = lesson.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= self.max_steps:
            raise ValueError(f"A lesson must contain between 1 and {self.max_steps} button presses.")
        for step in steps:
            if (not isinstance(step, dict) or step.get("key") not in ALLOWED_KEYS
                    or not isinstance(step.get("wait"), (int, float))
                    or not 0 <= step["wait"] <= 20
                    or not isinstance(step.get("long", False), bool)):
                raise ValueError("Invalid button in saved lesson.")

    def _persist(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.path.with_suffix(".json.tmp")
        with open(temp, "w", encoding="utf-8") as output:
            os.chmod(temp, 0o600)
            json.dump({"version": 1, "lessons": self.lessons}, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, self.path)

    def handle(self, text):
        command = normal(text)
        for alias in ("record", "learn"):
            if command == alias or command.startswith(alias + " "):
                command = "teach" + command[len(alias):]
                break
        if command in ("recording", "progress", "teach status") or command == "status" and self.message != "No lesson is recording or running.":
            return self.status()
        if command == "teach" or command.startswith("teach "):
            name = lesson_name(command[6:]) if command.startswith("teach ") else None
            return self.start(name)
        if command.startswith("replace "):
            return self.start(lesson_name(command[8:]), replace=True)
        if command == "save" or command.startswith("save "):
            name = command[5:] if command.startswith("save ") else None
            return self.save(name)
        if command == "cancel":
            return self.cancel()
        if command in ("lessons", "recordings"):
            with self.lock:
                return "Lessons: " + ", ".join(sorted(self.lessons)) + "." if self.lessons else "No lessons yet. Say Teach to record one."
        with self.lock:
            if self.state in ("recording", "draft", "starting", "saving"):
                raise ValueError("Teach mode is active. Use the physical remote, then say Save and a name, or Cancel.")
            if command in self.lessons:
                return self.play(command)
        return None

    def status(self):
        with self.lock:
            if self.state == "recording":
                return f"Recording {len(self.steps)} button presses. Say Save and a name when finished."
            return self.message

    def start(self, name=None, replace=False):
        with self.lock:
            if self.state != "idle":
                raise ValueError("A lesson is already recording or running. Say Cancel first.")
            if name in self.lessons and not replace:
                raise ValueError("That lesson already exists. Say Replace followed by its name to teach it again.")
            if replace and name not in self.lessons:
                raise ValueError("That lesson does not exist yet. Say Teach to create one.")
            self.state = "starting"
            self.message = "Preparing Home for recording."
            self.stop_event = threading.Event()
            event = self.stop_event
        if not self.action_lock.acquire(timeout=1):
            self.state = "idle"
            raise RuntimeError("TV is busy. Try Teach again shortly.")
        try:
            self.adapter.reset_home()
            with self.observation_lock:
                initial = self.adapter.snapshot()
            if event.is_set():
                raise RuntimeError("Recording cancelled.")
            process = self.adapter.events()
            time.sleep(0.15)
            if process.poll() is not None:
                raise RuntimeError("The TV did not allow remote-button recording.")
            with self.lock:
                if event.is_set():
                    process.terminate()
                    raise RuntimeError("Recording cancelled.")
                self.steps = []
                self.down = {}
                self.last_event = None
                self.pending_name = name
                self.replacing = replace
                self.recording_error = None
                self.start_screen = initial
                self.process = process
                self.started = self.changed = time.monotonic()
                self.state = "recording"
                self.message = "Recording started from Home."
            threading.Thread(target=self._record, args=(process, event), daemon=True).start()
            threading.Thread(target=self._observe, args=(event,), daemon=True).start()
            suffix = f"Save {name}" if name else "Save followed by a name"
            return f"Recording from Home. Use your physical remote, then say {suffix}. Say Cancel to discard."
        except Exception:
            with self.lock:
                self.state = "idle"
            raise
        finally:
            self.action_lock.release()

    def ingest(self, line):
        match = EVENT.search(line)
        if not match:
            return
        stamp, device, raw, value = match.groups()
        stamp = float(stamp)
        key = BUTTONS.get(raw)
        with self.lock:
            if self.state != "recording":
                return
            if value in ("UP", "00000000"):
                pressed = self.down.pop((device, raw), None)
                if pressed is not None:
                    index, at = pressed
                    if stamp - at > 0.8 and len(self.steps) == index + 1:
                        self.steps[index]["long"] = True
                    self.changed = time.monotonic()
                return
            if value not in ("DOWN", "REPEAT", "00000001", "00000002"):
                return
            if key is None:
                self.recording_error = f"The {raw} button cannot be replayed. Say Cancel and teach again without that button."
                self._draft(f"Recording stopped: {raw} cannot be replayed. Say Cancel to retry with navigation buttons.")
                return
            if self.last_event and self.last_event[2] == key and self.last_event[1] != device and stamp - self.last_event[0] < 0.08:
                return
            if len(self.steps) >= self.max_steps:
                self._draft(f"Recording reached {self.max_steps} presses. Say Save and a name, or Cancel.")
                return
            wait = 0.25 if not self.last_event else max(0.08, min(20, stamp - self.last_event[0]))
            self.steps.append({"key": key, "wait": round(wait, 3), "long": False})
            self.down[(device, raw)] = (len(self.steps) - 1, stamp)
            self.last_event = (stamp, device, key)
            self.changed = time.monotonic()

    def _record(self, process, event):
        try:
            for line in process.stdout:
                if event.is_set():
                    break
                self.ingest(line)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            finally:
                process.stdout.close()
            with self.lock:
                if self.state == "recording" and self.process is process:
                    self._draft("The remote connection stopped. Say Save and a name to keep recorded steps, or Cancel.")

    def _draft(self, message):
        self.state = "draft"
        self.message = message
        self.stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def _observe(self, event):
        while not event.wait(0.25):
            with self.lock:
                if self.state != "recording":
                    return
                if time.monotonic() - self.started > self.max_record_seconds:
                    self._draft("Recording reached its time limit. Say Save and a name, or Cancel.")
                    return
                index = len(self.steps) - 1
                if (index < 0 or self.steps[index]["key"] in QUIET_KEYS
                        or "screen" in self.steps[index] or time.monotonic() - self.changed < 1.0):
                    continue
                changed = self.changed
            try:
                with self.observation_lock:
                    screen = self.adapter.snapshot()
                with self.lock:
                    if self.state == "recording" and self.changed == changed and len(self.steps) == index + 1:
                        self.steps[index]["screen"] = screen
            except (RuntimeError, ValueError, OSError, ET.ParseError):
                # Some video apps do not expose a hierarchy while playing. Timing
                # is retained, with checkpoints added wherever a screen is exposed.
                event.wait(1)

    def save(self, name=None):
        with self.lock:
            if self.state not in ("recording", "draft"):
                raise ValueError("Nothing is recording. Say Teach first.")
            if self.recording_error:
                raise ValueError(self.recording_error)
            name = lesson_name(name or self.pending_name or "")
            if name in self.lessons and not (self.replacing and name == self.pending_name):
                raise ValueError("That lesson already exists. Use a different name.")
            if not self.steps:
                raise ValueError("No remote presses recorded yet. Use the physical remote, then Save.")
            self.state = "saving"
            self.message = "Saving the recording."
            self.stop_event.set()
            if self.process and self.process.poll() is None:
                self.process.terminate()
        try:
            if self.steps[-1]["key"] not in QUIET_KEYS:
                with self.observation_lock:
                    final = self.adapter.snapshot()
                self.steps[-1]["screen"] = final
        except (RuntimeError, ValueError, OSError, ET.ParseError):
            pass
        with self.lock:
            lesson = {"start": self.start_screen, "steps": self.steps,
                      "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            self._validate(lesson)
            previous = self.lessons.get(name)
            self.lessons[name] = lesson
            try:
                self._persist()
            except OSError:
                if previous is None:
                    del self.lessons[name]
                else:
                    self.lessons[name] = previous
                self.state = "draft"
                raise
            self.state = "idle"
            count = len(self.steps)
            checks = sum("screen" in step for step in self.steps)
            self.message = f"Saved {name}: {count} presses, {checks} screen checks. Say {name} to run it."
            return self.message

    def cancel(self):
        with self.lock:
            if self.state == "idle":
                return "Nothing is recording or running."
            if self.state == "saving":
                return "Saving the lesson. Please wait a moment."
            self.stop_event.set()
            if self.process and self.process.poll() is None:
                self.process.terminate()
            if self.state == "playing":
                return "Stopping the lesson."
            self.state = "idle"
            self.steps = []
            self.message = "Recording discarded."
            return self.message

    def play(self, name):
        with self.lock:
            if self.state != "idle":
                raise ValueError("Another lesson is active. Say Cancel first.")
            lesson = self.lessons[name]
            self._validate(lesson)
            self.state = "playing"
            self.stop_event = threading.Event()
            self.message = f"Running {name}."
            self.worker = threading.Thread(target=self._replay, args=(name, lesson), daemon=True)
            self.worker.start()
            return f"Running {name}: {len(lesson['steps'])} presses. Say Status for progress or Cancel to stop."

    def _check(self, expected):
        end = time.monotonic() + self.screen_timeout
        while not self.stop_event.is_set():
            try:
                with self.observation_lock:
                    actual = self.adapter.snapshot()
            except (RuntimeError, OSError, ET.ParseError):
                if time.monotonic() >= end:
                    raise RuntimeError("Cannot verify the current screen. Stopped without sending more buttons.") from None
                self.stop_event.wait(0.3)
                continue
            if screen_matches(expected, actual):
                return
            if time.monotonic() >= end:
                raise RuntimeError("The screen changed. Stopped before sending more buttons; teach this lesson again.")
            self.stop_event.wait(0.3)
        raise RuntimeError("Cancelled.")

    def _replay(self, name, lesson):
        acquired = self.action_lock.acquire(timeout=1)
        try:
            if not acquired:
                raise RuntimeError("TV is busy. Try the lesson again.")
            started = time.monotonic()
            self.adapter.reset_home()
            self._check(lesson["start"])
            for index, step in enumerate(lesson["steps"]):
                if time.monotonic() - started > self.max_replay_seconds:
                    raise RuntimeError("Lesson exceeded five minutes and was stopped.")
                if self.stop_event.wait(step["wait"]):
                    raise RuntimeError("Cancelled.")
                with self.lock:
                    self.message = f"Running {name}: press {index + 1} of {len(lesson['steps'])}."
                self.adapter.press(step["key"], step.get("long", False))
                if "screen" in step:
                    self._check(step["screen"])
            self.message = f"Finished {name}."
        except (RuntimeError, ValueError, OSError, ET.ParseError) as exc:
            self.message = f"{name} stopped: {exc}"
        finally:
            if acquired:
                self.action_lock.release()
            with self.lock:
                self.state = "idle"

    def close(self):
        self.cancel()
