"""Exercise the real HTTP dispatcher without touching user stores or providers."""
import http.client
import threading
import unittest
from http.server import ThreadingHTTPServer
from backend.api.server import Handler


class ProbeHandler(Handler):
    def log_message(self, *args):
        pass

    def handle_api(self, *args):
        self.send_json({"dispatched": True})

    def _handle_create_chat(self):
        self.send_json({"dispatched": True})

    def _handle_patch_chat(self, *args):
        self.send_json({"dispatched": True})

    def _handle_delete_chat(self, *args):
        self.send_json({"dispatched": True})


class RequestSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()

    def request(self, method, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        path = "/api/chats" if method == "POST" else "/api/chats/test"
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            response.read()
            return response.status
        finally:
            conn.close()

    def test_native_json_and_same_origin_writes(self):
        for method in ("POST", "PATCH", "DELETE"):
            for origin in (None, f"http://127.0.0.1:{self.port}"):
                headers = {"Content-Type": "application/json; charset=utf-8"}
                if origin:
                    headers.update(Origin=origin, **{"Sec-Fetch-Site": "same-origin"})
                self.assertEqual(self.request(method, headers, "{}"), 200)
        self.assertEqual(self.request("DELETE"), 200)
        self.assertEqual(self.request("GET"), 200)

    def test_cross_site_writes_rejected(self):
        for method in ("POST", "PATCH", "DELETE"):
            for extra in ({"Origin": "https://untrusted.invalid"}, {"Origin": "null"},
                          {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
                self.assertEqual(self.request(method, {"Content-Type": "application/json", **extra}, "{}"), 403)

    def test_simple_form_and_text_requests_rejected(self):
        for method in ("POST", "PATCH", "DELETE"):
            for content_type in ("text/plain", "application/x-www-form-urlencoded", "application/json-invalid"):
                self.assertEqual(self.request(method, {"Content-Type": content_type}, "{}"), 415)

    def test_untrusted_host_rejected_for_reads_and_writes(self):
        for method in ("GET", "POST", "PATCH", "DELETE"):
            for host in (f"untrusted.invalid:{self.port}", "localhost:1", "localhost:bad",
                         f"localhost:{self.port}/path", f"user@localhost:{self.port}"):
                self.assertEqual(self.request(method, {"Host": host, "Content-Type": "application/json"}, "{}"), 403)

    def test_duplicate_host_rejected(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        try:
            conn.putrequest("GET", "/api/chats")
            conn.putheader("Host", f"localhost:{self.port}")
            conn.endheaders()
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 403)
        finally:
            conn.close()

    def test_ambiguous_write_headers_rejected(self):
        for name, values in (("Origin", ["http://localhost", "null"]),
                             ("Content-Length", ["2", "2"]),
                             ("Transfer-Encoding", ["chunked"])):
            conn = http.client.HTTPConnection("127.0.0.1", self.port)
            try:
                conn.putrequest("POST", "/api/chats")
                conn.putheader("Content-Type", "application/json")
                for value in values:
                    conn.putheader(name, value)
                conn.endheaders()
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 400)
            finally:
                conn.close()
