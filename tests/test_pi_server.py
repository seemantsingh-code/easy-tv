import http.client
import json
import threading
import unittest
from unittest.mock import Mock, patch
from easy_tv import pi_server


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.teacher = Mock()
        cls.teacher.handle.return_value = None
        cls.agent = Mock()
        cls.agent.busy.return_value = False
        cls.agent.has_status.return_value = False
        cls.agent.configured.return_value = False
        cls.server = pi_server.create_server(("127.0.0.1", 0), "test-token", teacher=cls.teacher, agent=cls.agent)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, auth="Bearer test-token", body=None, **headers):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers={"Authorization": auth, **headers})
        response = connection.getresponse()
        status, body = response.status, json.loads(response.read())
        connection.close()
        return status, body

    def test_allowlist_applies_to_loopback_too(self):
        import ipaddress
        previous = self.server.networks
        try:
            self.server.networks = [ipaddress.ip_network('192.0.2.0/24')]
            with patch.object(pi_server.tv, 'execute') as execute:
                self.assertEqual(self.request('POST', '/command/inputs')[0], 403)
                execute.assert_not_called()
        finally:
            self.server.networks = previous

    def test_oversized_legacy_command_is_rejected(self):
        with patch.object(pi_server.tv, 'execute') as execute:
            self.assertEqual(self.request('POST', '/command/' + 'x' * 257)[0], 400)
            execute.assert_not_called()

    def test_missing_token_cannot_control_tv(self):
        with patch.object(pi_server.tv, "execute") as execute:
            self.assertEqual(self.request("POST", "/command/inputs", auth="")[0], 401)
            execute.assert_not_called()

    def test_teach_commands_use_the_same_authenticated_endpoint(self):
        with patch.object(self.teacher, "handle", return_value="Recording from Home.") as handle:
            with patch.object(pi_server.tv, "execute") as execute:
                status, body = self.request("POST", "/command", body=json.dumps({"command": "Teach"}))
                self.assertEqual(status, 200)
                self.assertEqual(body["message"], "Recording from Home.")
                handle.assert_called_once_with("Teach")
                execute.assert_not_called()

    def test_unauthenticated_request_cannot_start_recording(self):
        with patch.object(self.teacher, "handle") as handle:
            self.assertEqual(self.request("POST", "/command/teach", auth="")[0], 401)
            handle.assert_not_called()

    def test_teach_error_is_returned_to_client(self):
        with patch.object(self.teacher, "handle", side_effect=ValueError("Nothing is recording.")):
            status, body = self.request("POST", "/command/save")
            self.assertEqual(status, 400)
            self.assertEqual(body["message"], "Nothing is recording.")

    def test_flexible_request_goes_to_gemini(self):
        with patch.object(self.agent, "configured", return_value=True):
            with patch.object(self.agent, "submit", return_value={"ok": True, "message": "Opened Netflix."}) as submit:
                status, body = self.request("POST", "/command", body=json.dumps({"command": "Put Netflix on please"}))
                self.assertEqual(status, 200)
                self.assertEqual(body["message"], "Opened Netflix.")
                submit.assert_called_once_with("Put Netflix on please")

    def test_ai_busy_prevents_conflicting_direct_command(self):
        with patch.object(self.agent, "busy", return_value=True):
            with patch.object(pi_server.tv, "execute") as execute:
                self.assertEqual(self.request("POST", "/command/inputs")[0], 409)
                execute.assert_not_called()

    def test_ai_status_is_read_without_new_model_call(self):
        with patch.object(self.agent, "has_status", return_value=True):
            with patch.object(self.agent, "status", return_value={"ok": True, "message": "Finished."}) as status:
                self.assertEqual(self.request("POST", "/command/status")[1]["message"], "Finished.")
                status.assert_called_once()

    def test_unknown_command_cannot_execute(self):
        with patch.object(pi_server.tv, "execute") as execute:
            self.assertEqual(self.request("POST", "/command/reboot%3Bwhoami")[0], 400)
            execute.assert_not_called()

    def test_get_cannot_control_tv(self):
        with patch.object(pi_server.tv, "execute") as execute:
            self.assertEqual(self.request("GET", "/command/inputs")[0], 404)
            execute.assert_not_called()

    def test_browser_origin_blocked(self):
        self.assertEqual(self.request("POST", "/command/inputs", Origin="http://example.com")[0], 403)

    def test_command_runs_once(self):
        with patch.object(pi_server.tv, "execute", return_value="") as execute:
            status, result = self.request("POST", "/command/volume%20up")
            self.assertEqual(status, 200)
            self.assertTrue(result["ok"])
            execute.assert_called_once_with(["input", "keyevent", "KEYCODE_VOLUME_UP"])

    def test_json_voice_command_runs_once(self):
        with patch.object(pi_server.tv, "execute", return_value="") as execute:
            status, result = self.request("POST", "/command", body=json.dumps({"command": "open YouTube"}))
            self.assertEqual(status, 200)
            self.assertTrue(result["ok"])
            execute.assert_called_once()
            self.assertIn("com.google.android.youtube.tv", execute.call_args.args[0])

    def test_empty_voice_command_is_reported(self):
        with patch.object(pi_server.tv, "execute") as execute:
            status, body = self.request("POST", "/command", body=json.dumps({"command": ""}))
            self.assertEqual(status, 400)
            self.assertIn("No command received", body["message"])
            execute.assert_not_called()

    def test_malformed_json_is_reported(self):
        self.assertEqual(self.request("POST", "/command", body="{")[0], 400)

    def test_offline_tv_returns_error(self):
        with patch.object(pi_server.tv, "execute", side_effect=RuntimeError("TV offline")):
            status, body = self.request("POST", "/command/inputs")
            self.assertEqual(status, 503)
            self.assertEqual(body["message"], "TV offline")

    def test_health_does_not_contact_tv(self):
        with patch.object(pi_server.tv, "execute") as execute:
            self.assertEqual(self.request("GET", "/health")[0], 200)
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
