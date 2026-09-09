"""Modelist: same-origin website and persistent community API, Python stdlib only."""
import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
MODEL_IDS = {"chatgpt", "claude", "gemini", "deepseek", "qwen", "kimi", "midjourney", "flux", "stable-diffusion", "kling", "runway", "suno"}
SCENARIOS = {"日常问答", "内容写作", "编程开发", "学习研究", "图像设计", "视频创作", "音乐制作", "其他场景"}
COOKIE = "modelist_session"


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, db_path):
        self.db_path = str(db_path)
        self.limits = defaultdict(deque)
        self.limits_lock = threading.Lock()
        self.public_origin = os.environ.get("PUBLIC_ORIGIN", "").rstrip("/")
        self.secure_cookie = self.public_origin.startswith("https://")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    nickname TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id),
                    csrf TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY, model_id TEXT NOT NULL,
                    user_id TEXT NOT NULL REFERENCES users(id), rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                    content TEXT NOT NULL, scenario TEXT NOT NULL, version TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(model_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS reviews_model ON reviews(model_id);
            """)
        super().__init__(address, Handler)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def rate_limit(self, ip, group, maximum, window):
        now = time.time()
        with self.limits_lock:
            # Bound bookkeeping as well as request frequency.
            if len(self.limits) > 10000:
                self.limits = defaultdict(deque, {k: v for k, v in self.limits.items() if v and now - v[-1] < 900})
            bucket = self.limits[(ip, group)]
            while bucket and now - bucket[0] >= window:
                bucket.popleft()
            if len(bucket) >= maximum:
                raise APIError(429, "操作太频繁，请稍后再试。")
            bucket.append(now)


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()


class Handler(BaseHTTPRequestHandler):
    server_version = "Modelist"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        # Never log request bodies, passwords or cookies.
        pass

    def send(self, status, value, cookie=None, html=False):
        body = value if html else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise APIError(415, "请使用 JSON 请求。")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise APIError(413, "请求内容过大或为空。")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeDecodeError):
            raise APIError(400, "请求格式不正确。")

    def same_origin(self):
        origin = self.headers.get("Origin", "")
        expected = self.server.public_origin or "http://" + self.headers.get("Host", "")
        if origin != expected or self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise APIError(403, "请求来源无效，请刷新本页后重试。")

    def session(self):
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
            token = jar[COOKIE].value if COOKIE in jar else ""
        except Exception:
            token = ""
        if not token:
            return None
        hashed = hashlib.sha256(token.encode()).hexdigest()
        with self.server.db() as db:
            row = db.execute("SELECT s.*, u.nickname, u.username FROM sessions s LEFT JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expires>?", (hashed, time.time())).fetchone()
        return dict(row) if row else None

    def new_session(self, user_id=None, old=None):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.server.db() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            if old:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (old["token_hash"],))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, csrf, time.time() + 604800))
        cookie = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800"
        if self.server.secure_cookie:
            cookie += "; Secure"
        return csrf, cookie

    @staticmethod
    def public_user(session):
        if not session or not session.get("user_id"):
            return None
        return {"id": session["user_id"], "nickname": session["nickname"], "username": session["username"]}

    def authorize(self, require_user=True):
        self.same_origin()
        session = self.session()
        if not session or (require_user and not session["user_id"]):
            raise APIError(401, "请先登录，再发表点评。")
        if not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
            raise APIError(403, "登录状态已更新，请刷新页面后重试。")
        return session

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        try:
            self.route(method, urlsplit(self.path).path)
        except APIError as err:
            self.send(err.status, {"error": err.message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception as err:
            print(f"API error: {type(err).__name__}", flush=True)
            self.send(500, {"error": "服务器暂时无法完成请求，请稍后重试。"})

    def route(self, method, path):
        if method == "GET" and path in {"/", "/index.html"}:
            return self.send(200, (ROOT / "index.html").read_bytes(), html=True)
        if method == "GET" and path == "/api/session":
            session = self.session()
            if session:
                return self.send(200, {"user": self.public_user(session), "csrf": session["csrf"]})
            self.server.rate_limit(self.client_address[0], "sessions", 100, 60)
            csrf, cookie = self.new_session()
            return self.send(200, {"user": None, "csrf": csrf}, cookie)
        if method == "GET" and path == "/api/reviews":
            with self.server.db() as db:
                rows = db.execute("SELECT r.*, u.nickname FROM reviews r JOIN users u ON r.user_id=u.id ORDER BY updated_at DESC").fetchall()
            return self.send(200, {"reviews": [dict(row) for row in rows]})
        if method == "POST" and path in {"/api/register", "/api/login"}:
            old = self.authorize(require_user=False)
            self.server.rate_limit(self.client_address[0], "auth", 30, 900)
            data = self.body()
            username = self.string(data, "username", 3, 24).lower()
            password = self.string(data, "password", 8, 128, strip=False)
            if not re.fullmatch(r"[a-z0-9_-]{3,24}", username):
                raise APIError(400, "账号需为 3–24 位字母、数字、下划线或短横线。")
            with self.server.db() as db:
                row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
                if path == "/api/register":
                    nickname = self.string(data, "nickname", 2, 24)
                    if row:
                        raise APIError(409, "这个账号已被注册，请换一个或直接登录。")
                    user_id, salt = secrets.token_hex(16), secrets.token_hex(16)
                    try:
                        db.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)", (user_id, username, nickname, salt, password_hash(password, salt)))
                    except sqlite3.IntegrityError:
                        raise APIError(409, "这个账号已被注册。")
                else:
                    expected = password_hash(password, row["salt"] if row else "0" * 32)
                    if not row or not hmac.compare_digest(expected, row["password_hash"]):
                        raise APIError(401, "账号或密码不正确。")
                    user_id, nickname = row["id"], row["nickname"]
            csrf, cookie = self.new_session(user_id, old)
            return self.send(200, {"user": {"id": user_id, "username": username, "nickname": nickname}, "csrf": csrf}, cookie)
        if method == "POST" and path == "/api/logout":
            session = self.authorize()
            csrf, cookie = self.new_session(old=session)
            return self.send(200, {"user": None, "csrf": csrf}, cookie)
        if method == "POST" and path == "/api/reviews":
            session = self.authorize()
            self.server.rate_limit(self.client_address[0], "write", 30, 60)
            data = self.body()
            model_id = data.get("model_id")
            if not isinstance(model_id, str) or model_id not in MODEL_IDS:
                raise APIError(400, "未找到这个模型。")
            rating = data.get("rating")
            if type(rating) is not int or not 1 <= rating <= 5:
                raise APIError(400, "请选择 1–5 星评分。")
            content = self.string(data, "content", 10, 2000)
            version = self.string(data, "version", 0, 80)
            scenario = self.string(data, "scenario", 1, 24)
            if scenario not in SCENARIOS:
                raise APIError(400, "请选择有效的使用场景。")
            now = time.time()
            with self.server.db() as db:
                db.execute("""INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(model_id, user_id) DO UPDATE SET rating=excluded.rating, content=excluded.content,
                    scenario=excluded.scenario, version=excluded.version, updated_at=excluded.updated_at""",
                    (secrets.token_hex(16), model_id, session["user_id"], rating, content, scenario, version, now, now))
            return self.send(200, {"ok": True})
        if method == "DELETE" and path.startswith("/api/reviews/"):
            session = self.authorize()
            model_id = path.rsplit("/", 1)[-1]
            with self.server.db() as db:
                cursor = db.execute("DELETE FROM reviews WHERE model_id=? AND user_id=?", (model_id, session["user_id"]))
                if cursor.rowcount == 0:
                    raise APIError(404, "没有找到你发表的点评。")
            return self.send(200, {"ok": True})
        raise APIError(404, "页面或接口不存在。")

    @staticmethod
    def string(data, key, minimum, maximum, strip=True):
        value = data.get(key, "")
        if not isinstance(value, str):
            raise APIError(400, "输入类型不正确。")
        value = value.strip() if strip else value
        if not minimum <= len(value) <= maximum:
            labels = {"content": "点评", "password": "密码", "nickname": "昵称", "username": "账号", "version": "版本", "scenario": "场景"}
            raise APIError(400, f"{labels.get(key, key)}长度需为 {minimum}–{maximum} 个字符。")
        return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start the Modelist website and public review API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--db", default=str(ROOT / "data" / "modelist.sqlite3"))
    args = parser.parse_args()
    app = AppServer((args.host, args.port), args.db)
    print(f"Modelist running: http://{args.host}:{app.server_port} (Ctrl+C to stop)", flush=True)
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.server_close()
