"""End-to-end tests: the real router between a fake Decypharr and a fake qBittorrent."""
import json
import os
import re
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def start(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class FakeDecypharr(BaseHTTPRequestHandler):
    """Accepts adds unless the magnet contains 'blocked'; holds torrent 'aaa'."""
    adds = []

    def log_message(self, *a):
        pass

    def _reply(self, status, body, ctype="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/v2/torrents/info":
            items = [{"hash": "aaa", "name": "debrid item", "category": "radarr", "state": "downloading"}] + FakeDecypharr.extra
            return self._reply(200, json.dumps(items).encode(), "application/json")
        return self._reply(200, b"v2.7")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/v2/torrents/add":
            if b"badauth" in body:
                return self._reply(401, b"unauthorized: invalid credentials")
            if b"blocked" in body:
                return self._reply(451, b'{"error":"torrent blocked for legal reasons"}', "application/json")
            FakeDecypharr.adds.append(body)
            return self._reply(200, b"Ok.")
        FakeDecypharr.posts.append((path, body))
        return self._reply(200, b"Ok.")


FakeDecypharr.posts = []
FakeDecypharr.extra = []


class FakeQBit(BaseHTTPRequestHandler):
    torrents = {"bbb": {"hash": "bbb", "name": "fallback item", "category": "radarr-fallback"}}
    categories = {"radarr": {"name": "radarr", "savePath": "/data/torrents/movies"}}
    adds = []
    posts = []

    def log_message(self, *a):
        pass

    def _reply(self, status, body, ctype="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/api/v2/torrents/info":
            cat = dict(urllib.parse.parse_qsl(url.query)).get("category")
            items = [t for t in FakeQBit.torrents.values() if cat is None or t["category"] == cat]
            return self._reply(200, json.dumps(items).encode(), "application/json")
        if url.path == "/api/v2/torrents/categories":
            return self._reply(200, json.dumps(FakeQBit.categories).encode(), "application/json")
        return self._reply(200, b"v5.0")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/v2/auth/login":
            return self._reply(200, b"Ok.")
        if path == "/api/v2/torrents/add":
            FakeQBit.adds.append((self.headers.get("Content-Type"), body))
            if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
                form = dict(urllib.parse.parse_qsl(body.decode()))
                m = re.search(r"btih:([0-9a-z]+)", form.get("urls", ""))
                if m:
                    FakeQBit.torrents[m.group(1)] = {"hash": m.group(1), "name": "moved", "category": form["category"]}
            return self._reply(200, b"Ok.")
        if path == "/api/v2/torrents/createCategory":
            form = dict(urllib.parse.parse_qsl(body.decode()))
            FakeQBit.categories[form["category"]] = {"name": form["category"], "savePath": form.get("savePath", "")}
            return self._reply(200, b"")
        FakeQBit.posts.append((path, body))
        return self._reply(200, b"Ok.")


class RouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.decypharr = start(FakeDecypharr)
        cls.qbit = start(FakeQBit)
        os.environ.update(
            DECYPHARR_URL=f"http://127.0.0.1:{cls.decypharr.server_port}",
            QBIT_URL=f"http://127.0.0.1:{cls.qbit.server_port}",
            QBIT_USERNAME="user",
            QBIT_PASSWORD="pass",
        )
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import decypharr_fallback
        cls.router = decypharr_fallback.serve(0, "127.0.0.1")
        threading.Thread(target=cls.router.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.router.server_port}"

    def setUp(self):
        FakeDecypharr.adds.clear()
        FakeDecypharr.posts.clear()
        FakeQBit.adds.clear()
        FakeQBit.posts.clear()
        FakeQBit.categories.pop("radarr-fallback", None)
        FakeDecypharr.extra.clear()
        FakeQBit.torrents = {"bbb": {"hash": "bbb", "name": "fallback item", "category": "radarr-fallback"}}

    def call(self, method, path, data=None, ctype="application/x-www-form-urlencoded"):
        if isinstance(data, dict):
            data = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": ctype} if data else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_accepted_add_stays_on_decypharr(self):
        status, body = self.call("POST", "/api/v2/torrents/add", {"urls": "magnet:?xt=good", "category": "radarr"})
        self.assertEqual((status, body), (200, b"Ok."))
        self.assertEqual(len(FakeDecypharr.adds), 1)
        self.assertEqual(FakeQBit.adds, [])

    def test_refused_add_falls_back_with_suffixed_category(self):
        status, body = self.call("POST", "/api/v2/torrents/add", {"urls": "magnet:?xt=blocked", "category": "radarr"})
        self.assertEqual((status, body), (200, b"Ok."))
        self.assertEqual(len(FakeQBit.adds), 1)
        form = dict(urllib.parse.parse_qsl(FakeQBit.adds[0][1].decode()))
        self.assertEqual(form["category"], "radarr-fallback")
        self.assertEqual(form["urls"], "magnet:?xt=blocked")

    def test_fallback_category_created_with_same_save_path(self):
        self.call("POST", "/api/v2/torrents/add", {"urls": "magnet:?xt=blocked", "category": "radarr"})
        self.assertEqual(FakeQBit.categories["radarr-fallback"]["savePath"], "/data/torrents/movies")

    def test_multipart_torrent_file_falls_back_intact(self):
        boundary = "XyZ"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="category"\r\n\r\nradarr\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="torrents"; filename="x.torrent"\r\n'
            f"Content-Type: application/x-bittorrent\r\n\r\nblocked-torrent-bytes\r\n--{boundary}--\r\n"
        ).encode()
        status, _ = self.call("POST", "/api/v2/torrents/add", body, f"multipart/form-data; boundary={boundary}")
        self.assertEqual(status, 200)
        ctype, sent = FakeQBit.adds[0]
        self.assertTrue(ctype.startswith("multipart/form-data"))
        self.assertIn(b"\r\n\r\nradarr-fallback\r\n", sent)
        self.assertIn(b"blocked-torrent-bytes", sent)

    def test_info_merges_both_and_restores_category(self):
        status, body = self.call("GET", "/api/v2/torrents/info?category=radarr")
        items = {t["hash"]: t for t in json.loads(body)}
        self.assertEqual(status, 200)
        self.assertEqual(set(items), {"aaa", "bbb"})
        self.assertEqual(items["bbb"]["category"], "radarr")

    def test_hash_requests_routed_to_owner(self):
        self.call("POST", "/api/v2/torrents/delete", {"hashes": "BBB", "deleteFiles": "true"})
        self.call("POST", "/api/v2/torrents/delete", {"hashes": "aaa", "deleteFiles": "true"})
        self.assertEqual([p for p, _ in FakeQBit.posts], ["/api/v2/torrents/delete"])
        self.assertIn(b"hashes=aaa", FakeDecypharr.posts[0][1])

    def test_stale_error_entry_is_left_alone(self):
        FakeDecypharr.extra.append({"hash": "ddd", "name": "old leftover", "category": "radarr", "state": "error"})
        items = json.loads(self.call("GET", "/api/v2/torrents/info?category=radarr")[1])
        self.assertEqual(FakeQBit.adds, [])
        self.assertIn("ddd", [t["hash"] for t in items])

    def test_failed_debrid_download_moves_to_qbit(self):
        FakeDecypharr.extra.append({"hash": "ccc", "name": "uncached thing", "category": "radarr", "state": "downloading"})
        self.call("GET", "/api/v2/torrents/info?category=radarr")   # seen working
        FakeDecypharr.extra[0]["state"] = "error"                   # then it fails on the debrid side
        items = {t["hash"]: t for t in json.loads(self.call("GET", "/api/v2/torrents/info?category=radarr")[1])}
        form = dict(urllib.parse.parse_qsl(FakeQBit.adds[0][1].decode()))
        self.assertTrue(form["urls"].startswith("magnet:?xt=urn:btih:ccc"))
        self.assertEqual(form["category"], "radarr-fallback")
        self.assertEqual(items["ccc"]["category"], "radarr")       # same hash, now the qBittorrent copy
        self.assertEqual(items["ccc"]["name"], "moved")
        self.assertEqual(FakeDecypharr.posts[0][0], "/api/v2/torrents/delete")
        self.assertIn(b"hashes=ccc", FakeDecypharr.posts[0][1])
        # next poll: Decypharr still lists it, but only the qBittorrent copy is reported, no second add
        items = json.loads(self.call("GET", "/api/v2/torrents/info?category=radarr")[1])
        self.assertEqual([t["name"] for t in items if t["hash"] == "ccc"], ["moved"])
        self.assertEqual(len(FakeQBit.adds), 1)

    def test_auth_failure_is_not_a_fallback(self):
        status, _ = self.call("POST", "/api/v2/torrents/add", {"urls": "magnet:?xt=badauth", "category": "radarr"})
        self.assertEqual(status, 401)
        self.assertEqual(FakeQBit.adds, [])

    def test_decypharr_down_falls_back(self):
        import decypharr_fallback
        original = decypharr_fallback.DECYPHARR
        decypharr_fallback.DECYPHARR = "http://127.0.0.1:9"  # nothing listens here
        try:
            status, body = self.call("POST", "/api/v2/torrents/add", {"urls": "magnet:?xt=good", "category": "radarr"})
            info = json.loads(self.call("GET", "/api/v2/torrents/info?category=radarr")[1])
        finally:
            decypharr_fallback.DECYPHARR = original
        self.assertEqual((status, body), (200, b"Ok."))
        self.assertEqual(len(FakeQBit.adds), 1)
        self.assertEqual([t["hash"] for t in info], ["bbb"])

    def test_health(self):
        status, body = self.call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"decypharr": True, "qbittorrent": True})


if __name__ == "__main__":
    unittest.main()
