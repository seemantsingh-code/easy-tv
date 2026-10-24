#!/usr/bin/env python3
"""Authenticated, client-independent LAN bridge."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
from urllib.parse import unquote, urlsplit

from .config import home, load, path_for
from . import tv
from .teach import TeachManager
from .gemini_agent import TVAgent

ROOT = home()
COMMAND_LOCK = threading.Lock()
AUDIT = logging.getLogger("easy-tv")


class Handler(BaseHTTPRequestHandler):
    server_version = "easy-tv/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, fmt, *args):
        # Never record command URLs or authentication headers.
        pass

    def reply(self, status, payload):
        AUDIT.info("client=%s method=%s status=%s command=%r", self.client_address[0],
                   self.command, status, getattr(self, "tv_command", None))
        if "error" in payload:
            payload.setdefault("message", payload["error"])
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def allowed(self):
        address = ipaddress.ip_address(self.client_address[0])
        if not (any(address in network for network in self.server.networks)):
            self.reply(403, {"error": "Use the home network."})
            return False
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + self.server.token).encode()):
            self.reply(401, {"error": "Authentication required."})
            return False
        if self.headers.get("Origin"):
            self.reply(403, {"error": "Browser requests are not supported."})
            return False
        return True

    def do_GET(self):
        if self.allowed():
            if self.path == "/health":
                self.reply(200, {"ok": True, "service": "easy-tv"})
            else:
                self.reply(404, {"error": "Not found."})

    def do_POST(self):
        if not self.allowed():
            return
        parsed = urlsplit(self.path)
        if parsed.query or len(self.path) > 1024:
            self.reply(404, {"error": "Unknown command route."})
            return
        if parsed.path == "/command":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    raise ValueError("Missing or oversized command body.")
                payload = json.loads(self.rfile.read(length))
                command = payload.get("command") if isinstance(payload, dict) else None
                if not isinstance(command, str) or not command.strip() or len(command) > 256:
                    raise ValueError("No command received. Enter a command such as inputs or open YouTube.")
            except (ValueError, UnicodeDecodeError) as exc:
                self.reply(400, {"error": str(exc)})
                return
        elif parsed.path.startswith("/command/"):
            command = unquote(parsed.path[len("/command/"):])
            if not command.strip() or len(command) > 256:
                self.reply(400, {"error": "Enter a command of 1 to 256 characters."})
                return
        else:
            self.reply(404, {"error": "Unknown command route."})
            return
        self.tv_command = command[:256]
        normalized = " ".join(command.lower().strip().rstrip(".!?").split())
        if normalized in ("ai status", "gemini status"):
            self.reply(200, {"ok": True, "message": self.server.agent.configuration_status()})
            return
        if normalized in ("status", "progress") and self.server.agent.has_status():
            self.reply(200, self.server.agent.status())
            return
        if normalized == "cancel" and self.server.agent.busy():
            self.reply(200, self.server.agent.cancel())
            return
        if self.server.agent.busy():
            self.reply(409, {"error": "Another request is running. Say Status or Cancel."})
            return
        try:
            taught = self.server.teacher.handle(command)
            if taught is not None:
                self.server.agent.clear_status()
                self.reply(200, {"ok": True, "message": taught})
                return
        except ValueError as exc:
            self.reply(400, {"error": str(exc)})
            return
        except (RuntimeError, OSError) as exc:
            self.reply(503, {"error": str(exc)})
            return
        try:
            remote = tv.plan(command)
        except ValueError as exc:
            if not self.server.agent.configured():
                self.reply(400, {"error": str(exc) + " Gemini is not configured yet for flexible requests."})
                return
            try:
                self.reply(200, self.server.agent.submit(command))
            except (RuntimeError, ValueError, OSError) as error:
                self.reply(503, {"error": str(error)})
            return
        if not COMMAND_LOCK.acquire(timeout=1):
            self.reply(409, {"error": "TV is busy. Try again shortly."})
            return
        try:
            self.server.agent.clear_status()
            result = tv.execute(remote)
            self.reply(200, {"ok": True, "message": tv.feedback(remote, result)})
        except (RuntimeError, ValueError, OSError) as exc:
            self.reply(503, {"error": str(exc)})
        finally:
            COMMAND_LOCK.release()


def create_server(address, token, lan=None, teacher=None, agent=None):
    server = ThreadingHTTPServer(address, Handler)
    server.daemon_threads = True
    server.token = token
    networks = [lan] if isinstance(lan, str) else (lan or load()["server"]["allowed_cidrs"])
    server.networks = [ipaddress.ip_network(value, strict=False) for value in networks]
    server.teacher = teacher or TeachManager(action_lock=COMMAND_LOCK)
    server.agent = agent or TVAgent(server.teacher, COMMAND_LOCK)
    return server


def serve():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = RotatingFileHandler(ROOT / "requests.log", maxBytes=65536, backupCount=1)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    AUDIT.addHandler(handler)
    AUDIT.setLevel(logging.INFO)
    config = load()["server"]
    token = path_for(config["token_file"]).read_text().strip()
    if len(token) < 32:
        raise SystemExit("Missing bridge authentication token.")
    server = create_server((config["listen"], config["port"]), token, config["allowed_cidrs"])
    print("easy-tv listening on %s:%s" % (config["listen"], config["port"]), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.agent.close()
        server.server_close()
        handler.close()
