"""Configuration is stored outside the package; shipped defaults contain no secrets."""
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
from importlib.resources import files

SUPPORTED_PROVIDERS = ("netflix", "prime video", "hotstar", "apple tv")


def home():
    value = os.environ.get("EASY_TV_HOME")
    if value:
        return Path(value).expanduser().resolve()
    return (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "easy-tv").resolve()


def merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def validate_address(address):
    if not isinstance(address, str) or not address or re.search(r"[\s\x00-\x1f]", address):
        raise ValueError("Set tv.address to the TV's hostname-or-IP:port.")
    if address.startswith("["):
        match = re.fullmatch(r"\[([^]]+)\]:(\d+)", address)
        if not match:
            raise ValueError("An IPv6 address must use [address]:port.")
        ipaddress.IPv6Address(match[1])
        port = int(match[2])
    else:
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9.-]*):(\d+)", address)
        if not match:
            raise ValueError("Set tv.address to the TV's hostname-or-IP:port.")
        host, port = match[1], int(match[2])
        if re.fullmatch(r"[0-9.]+", host):
            ipaddress.IPv4Address(host)
    if not 1 <= port <= 65535:
        raise ValueError("The ADB port must be between 1 and 65535.")
    return address


def load(root=None):
    root = Path(root) if root else home()
    defaults = json.loads(files("easy_tv").joinpath("resources/defaults.json").read_text())
    path = root / "config.json"
    user = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(user, dict):
        raise ValueError("config.json must contain a JSON object.")
    profile = user.get("profile", defaults["profile"])
    if profile not in ("xiaomi-android9", "generic-androidtv"):
        raise ValueError("Choose profile xiaomi-android9 or generic-androidtv, then override its settings in config.json.")
    profile_data = json.loads(files("easy_tv").joinpath("resources/profiles/" + profile + ".json").read_text())
    result = merge(merge(defaults, profile_data), user)
    # These are registries, not partial patches: users can remove apps/ports.
    for key in ("apps", "app_names"):
        if key in user:
            result[key] = copy.deepcopy(user[key])
    for key in ("input_ids", "input_aliases", "input_labels"):
        if key in user.get("tv", {}):
            result["tv"][key] = copy.deepcopy(user["tv"][key])
    validate(result)
    return result


def validate(settings):
    if settings.get("version") != 1:
        raise ValueError("Unsupported configuration version.")
    for key in ("tv", "server", "gemini", "content", "agent", "teach", "apps", "app_names", "netflix"):
        if not isinstance(settings.get(key), dict):
            raise ValueError(key + " must be an object.")
    tv = settings["tv"]
    for key in ("input_ids", "input_aliases", "input_labels"):
        if not isinstance(tv[key], dict):
            raise ValueError("tv." + key + " must be an object.")
    if type(tv["home_settle_seconds"]) not in (int, float) or not 0 <= tv["home_settle_seconds"] <= 30:
        raise ValueError("tv.home_settle_seconds must be between 0 and 30.")
    for key in ("input_popup_action", "launcher_package"):
        if tv[key] is not None and (not isinstance(tv[key], str) or not re.fullmatch(r"[A-Za-z0-9_.]+", tv[key])):
            raise ValueError("tv." + key + " must be an Android identifier or null.")
    if tv["address"]:
        validate_address(tv["address"])
    if not isinstance(tv["adb"], str) or not tv["adb"]:
        raise ValueError("tv.adb must be an executable name or path.")
    server = settings["server"]
    if type(server["port"]) is not int or not 1 <= server["port"] <= 65535:
        raise ValueError("server.port must be between 1 and 65535.")
    if not isinstance(server["listen"], str) or not server["listen"] or re.search(r"[\s\x00-\x1f]", server["listen"]):
        raise ValueError("server.listen must be a host/interface address.")
    if not isinstance(server["allowed_cidrs"], list) or not server["allowed_cidrs"]:
        raise ValueError("Set server.allowed_cidrs to the networks allowed to use the controller.")
    for value in server["allowed_cidrs"]:
        ipaddress.ip_network(value, strict=False)
    for app, package in settings["apps"].items():
        if not re.fullmatch(r"[a-z0-9 _-]{1,40}", app) or not isinstance(package, str) or not re.fullmatch(r"[A-Za-z0-9_.]+", package):
            raise ValueError("apps must map short names to Android package names.")
    for port, identifier in tv["input_ids"].items():
        if not re.fullmatch(r"\d{1,2}", port) or not isinstance(identifier, str) or not identifier:
            raise ValueError("tv.input_ids must map port numbers to Android TV input IDs.")
    for alias, port in tv["input_aliases"].items():
        if not isinstance(alias, str) or type(port) is not int or str(port) not in tv["input_ids"]:
            raise ValueError("Every input alias must point to a configured input ID.")
    content = settings["content"]
    if type(content["cache_seconds"]) not in (int, float) or not 0 < content["cache_seconds"] <= 86400:
        raise ValueError("content.cache_seconds must be positive and at most 86400.")
    if not re.fullmatch(r"[A-Z]{2}", content["region"]):
        raise ValueError("content.region must be a two-letter upper-case country code.")
    if not isinstance(content["providers"], list) or any(p not in SUPPORTED_PROVIDERS or p not in settings["apps"] for p in content["providers"]):
        raise ValueError("content.providers must use supported, configured provider apps.")
    if content["fallback"] not in (None, "stremio") or content["fallback"] == "stremio" and "stremio" not in settings["apps"]:
        raise ValueError("content.fallback must be null or a configured Stremio app.")
    for section, keys in (("agent", ("max_rounds", "max_actions", "max_seconds", "max_inspections", "response_wait_seconds")),
                          ("teach", ("max_steps", "max_record_seconds", "max_replay_seconds", "screen_timeout"))):
        for key in keys:
            value = settings[section][key]
            if type(value) not in (int, float) or not 0 < value <= 3600:
                raise ValueError(section + "." + key + " must be positive and at most 3600.")
            if key in ("max_rounds", "max_actions", "max_inspections", "max_steps") and type(value) is not int:
                raise ValueError(section + "." + key + " must be an integer.")
    for key in ("home_up_presses", "home_left_presses"):
        if type(tv[key]) is not int or not 0 <= tv[key] <= 30:
            raise ValueError("tv." + key + " must be between 0 and 30.")


def path_for(value, root=None):
    value = Path(value).expanduser()
    return value if value.is_absolute() else (Path(root) if root else home()) / value


def private_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
