#!/usr/bin/env python3
"""Configured Android TV actions, shared by all easy-tv clients."""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from urllib.parse import quote
from .config import home, load, validate_address

ROOT = home()
APPS = load()["apps"]
KEYS = {
    "home": "HOME", "back": "BACK", "up": "DPAD_UP", "down": "DPAD_DOWN",
    "left": "DPAD_LEFT", "right": "DPAD_RIGHT", "select": "DPAD_CENTER",
    "volume up": "VOLUME_UP", "volume down": "VOLUME_DOWN", "mute": "VOLUME_MUTE",
    "play": "MEDIA_PLAY", "pause": "MEDIA_PAUSE", "play pause": "MEDIA_PLAY_PAUSE",
    "next": "MEDIA_NEXT", "previous": "MEDIA_PREVIOUS", "rewind": "MEDIA_REWIND",
    "fast forward": "MEDIA_FAST_FORWARD", "off": "SLEEP", "on": "WAKEUP",
}
ALIASES = {
    "go home": "home", "go back": "back", "ok": "select", "enter": "select",
    "louder": "volume up", "quieter": "volume down", "turn off": "off",
    "turn on": "on", "power off": "off", "power on": "on",
    "input": "inputs", "sources": "inputs", "go to inputs": "inputs",
    "prime": "prime video",
    "amazon prime": "prime video", "jiohotstar": "hotstar", "jio hotstar": "hotstar",
}

APP_NAMES = {name: load()["app_names"].get(name, name) for name in APPS}


def feedback(remote, output=""):
    """Describe the command sent, without guessing playback or mute state."""
    if remote[:2] == ["getprop", "ro.product.model"]:
        return "Connected to " + (output or "the TV") + "."
    if remote[:2] == ["input", "keyevent"]:
        key = remote[2].removeprefix("KEYCODE_")
        count = len(remote) - 2
        if key in ("VOLUME_UP", "VOLUME_DOWN"):
            verb = "Raised" if key == "VOLUME_UP" else "Lowered"
            return f"{verb} volume by {count} " + ("step." if count == 1 else "steps.")
        messages = {"VOLUME_MUTE": "Toggled mute.", "SLEEP": "Sent the TV sleep command.",
                    "WAKEUP": "Sent the TV wake command.", "HOME": "Opened Home.",
                    "BACK": "Went back.", "DPAD_CENTER": "Selected.",
                    "MEDIA_PLAY": "Sent Play.", "MEDIA_PAUSE": "Sent Pause.",
                    "MEDIA_PLAY_PAUSE": "Toggled playback.", "MEDIA_NEXT": "Sent Next.",
                    "MEDIA_PREVIOUS": "Sent Previous.", "MEDIA_REWIND": "Sent Rewind.",
                    "MEDIA_FAST_FORWARD": "Sent Fast Forward."}
        if key.startswith("DPAD_") and key != "DPAD_CENTER":
            return "Moved " + key[5:].lower() + "."
        return messages.get(key, "Command sent.")
    settings = load()
    if settings["tv"]["input_popup_action"] and settings["tv"]["input_popup_action"] in remote:
        return "Opened Inputs."
    if "android.settings.SETTINGS" in remote:
        return "Opened TV settings."
    if remote[0] == "monkey":
        package = remote[remote.index("-p") + 1]
        app = next((APP_NAMES[name] for name, value in APPS.items() if value == package), "app")
        return "Opened " + app + "."
    if "android.intent.action.VIEW" in remote:
        for number, identifier in settings["tv"]["input_ids"].items():
            if quote(identifier, safe="") in remote[-1]:
                return "Switched to " + settings["tv"]["input_labels"].get(number, "HDMI " + number) + "."
    return "Command sent."


def plan(text):
    command = re.sub(r"[.!?]+$", "", " ".join(text.lower().strip().split()))
    command = re.sub(r"\bplay\s+station\b", "playstation", command)
    command = re.sub(r"\byou\s+tube\b", "youtube", command)
    command = re.sub(r"\bnet\s+flix\b", "netflix", command)
    command = re.sub(r"^(?:please )?(?:switch to |open |launch )", "", command)
    command = re.sub(r"^tv ", "", command)
    aliases = {**ALIASES, **{name: "hdmi " + str(port) for name, port in load()["tv"]["input_aliases"].items()}}
    command = aliases.get(command, command)
    words = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
             "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
    command = " ".join(words.get(word, word) for word in command.split())
    if command in KEYS:
        return ["input", "keyevent", "KEYCODE_" + KEYS[command]]
    match = re.fullmatch(r"volume (up|down) (\d{1,2})(?: times| steps)?", command)
    if match:
        count = int(match[2])
        if not 1 <= count <= 10:
            raise ValueError("Use between 1 and 10 volume steps.")
        return ["input", "keyevent"] + ["KEYCODE_VOLUME_" + match[1].upper()] * count
    if command == "inputs":
        action = load()["tv"]["input_popup_action"]
        return ["am", "start", "-a", action] if action else ["input", "keyevent", "KEYCODE_TV_INPUT"]
    match = re.fullmatch(r"hdmi\s*(\d{1,2})", command)
    if match:
        input_id = load()["tv"]["input_ids"].get(str(int(match[1])))
        if input_id is None:
            raise ValueError("Configure the Android TV input ID for that HDMI port first.")
        return ["am", "start", "-a", "android.intent.action.VIEW", "-d",
                "content://android.media.tv/passthrough/" + quote(input_id, safe="")]
    if command == "settings":
        return ["am", "start", "-a", "android.settings.SETTINGS"]
    if command in APPS:
        if not APPS[command]:
            raise ValueError("This app is not installed on your TV.")
        return ["monkey", "-p", APPS[command], "-c", "android.intent.category.LEANBACK_LAUNCHER", "1"]
    if command == "status":
        return ["getprop", "ro.product.model"]
    raise ValueError("Unknown command. Try inputs, home, YouTube, Netflix, PlayStation, volume up, pause, or off.")


def run(args, timeout=12):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("TV connection timed out. Check that the TV is awake and on the same network.") from None
    except OSError as exc:
        raise RuntimeError(str(exc)) from None
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or "ADB command failed.")
    return result.stdout.strip()


def execute(remote):
    config = load()["tv"]
    address = config["address"]
    validate_address(address)
    adb = config["adb"]
    try:
        connected = run([adb, "-s", address, "get-state"], timeout=4) == "device"
    except RuntimeError:
        connected = False
    if not connected:
        run([adb, "connect", address], timeout=10)
        if run([adb, "-s", address, "get-state"], timeout=4) != "device":
            raise RuntimeError("TV is unavailable. Check debugging, the IP address, and the TV authorization prompt.")
    # ADB forwards this string to Android's shell; quote every argument there too.
    # Never retry an action: that could double a volume step or toggle mute twice.
    output = run([adb, "-s", address, "shell", shlex.join(remote)])
    if any(marker in output for marker in ("Error:", "Exception", "monkey aborted", "No activities found")):
        raise RuntimeError(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="*", help="TV command, or read it from stdin")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    text = " ".join(args.command) if args.command else sys.stdin.read().strip()
    try:
        remote = plan(text)
        if args.dry_run:
            print(shlex.join(remote))
        else:
            output = execute(remote)
            print(feedback(remote, output))
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
