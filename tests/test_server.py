import http.cookiejar
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import AppServer


class Client:
    def __init__(self, base):
        self.base, self.csrf = base, ""
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, method="GET", body=None, headers=None):
        req = urllib.request.Request(self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "Origin": self.base, "X-CSRF-Token": self.csrf, **(headers or {})})
        try:
            response = self.opener.open(req)
        except urllib.error.HTTPError as err:
            response = err
        with response:
            content = response.read()
            data = json.loads(content) if response.headers.get_content_type() == "application/json" else content
            if isinstance(data, dict) and "csrf" in data:
                self.csrf = data["csrf"]
            return response.code, data

    def register(self, username):
        self.request("/api/session")
        return self.request("/api/register", "POST", {"username": username, "nickname": username + "昵称", "password": "Example-Password-123"})


class CommunityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.start()

    def start(self):
        self.server = AppServer(("127.0.0.1", 0), self.db)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.alice, self.bob, self.anon = Client(self.base), Client(self.base), Client(self.base)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def tearDown(self):
        self.stop()
        self.temp.cleanup()

    def payload(self, **changes):
        return {"model_id": "claude", "rating": 5, "content": "用来整理文档和修改代码，整体使用体验很好。", "scenario": "编程开发", "version": "测试版本", **changes}

    def test_public_visibility_edit_ownership_and_restart(self):
        self.assertEqual(self.alice.register("alice")[0], 200)
        self.assertEqual(self.bob.register("bob")[0], 200)
        self.assertEqual(self.alice.request("/api/reviews", "POST", self.payload())[0], 200)
        status, data = self.anon.request("/api/reviews")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["reviews"]), 1)
        self.assertEqual(data["reviews"][0]["rating"], 5)
        self.assertNotIn("password_hash", data["reviews"][0])
        self.assertNotIn("username", data["reviews"][0])
        self.assertEqual(self.bob.request("/api/reviews/claude", "DELETE")[0], 404)
        self.assertEqual(self.alice.request("/api/reviews", "POST", self.payload(rating=3))[0], 200)
        self.assertEqual(len(self.anon.request("/api/reviews")[1]["reviews"]), 1)
        self.assertEqual(self.bob.request("/api/reviews", "POST", self.payload(rating=4))[0], 200)
        self.assertEqual(len(self.anon.request("/api/reviews")[1]["reviews"]), 2)
        self.stop()
        self.start()
        rows = self.anon.request("/api/reviews")[1]["reviews"]
        self.assertEqual(sorted(r["rating"] for r in rows), [3, 4])
        self.alice.request("/api/session")
        self.assertEqual(self.alice.request("/api/login", "POST", {"username": "alice", "password": "Example-Password-123"})[0], 200)
        self.assertEqual(self.alice.request("/api/reviews/claude", "DELETE")[0], 200)
        self.assertEqual(len(self.anon.request("/api/reviews")[1]["reviews"]), 1)
        self.assertEqual(self.alice.request("/api/logout", "POST", {})[0], 200)
        self.assertEqual(self.alice.request("/api/reviews", "POST", self.payload())[0], 401)

    def test_auth_validation_csrf_and_server_file_privacy(self):
        self.assertEqual(self.anon.request("/api/reviews", "POST", self.payload())[0], 401)
        self.assertEqual(self.alice.register("alice")[0], 200)
        self.assertEqual(self.bob.register("alice")[0], 409)
        self.assertEqual(self.bob.request("/api/login", "POST", {"username": "alice", "password": "wrong-password"})[0], 401)
        for invalid in [0, 6, 2.5, "5", True, None]:
            with self.subTest(rating=invalid):
                self.assertEqual(self.alice.request("/api/reviews", "POST", self.payload(rating=invalid))[0], 400)
        for changes in [{"model_id": "unknown"}, {"model_id": []}, {"content": "short"}, {"content": "x" * 2001}, {"scenario": "spam"}, {"version": "x" * 81}]:
            self.assertEqual(self.alice.request("/api/reviews", "POST", self.payload(**changes))[0], 400)
        self.assertEqual(self.alice.request("/api/reviews", "POST", self.payload(), {"X-CSRF-Token": "wrong"})[0], 403)
        self.assertEqual(self.alice.request("/api/reviews", "POST", self.payload(), {"Origin": "https://evil.example"})[0], 403)
        for path in ["/server.py", "/data/modelist.sqlite3", "/.git/config", "/../server.py"]:
            self.assertEqual(self.anon.request(path)[0], 404)
        self.assertEqual(self.anon.request("/")[0], 200)
        with self.server.db() as db:
            user = dict(db.execute("SELECT * FROM users").fetchone())
            self.assertNotEqual(user["password_hash"], "Example-Password-123")
            self.assertEqual(len(user["salt"]), 32)
        self.assertEqual(self.anon.request("/api/reviews")[1]["reviews"], [])


if __name__ == "__main__":
    unittest.main()
