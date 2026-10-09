"""qBittorrent-API router: Decypharr first, real qBittorrent as fallback.

The arrs talk to this service as if it were Decypharr. Every request is passed
through to Decypharr. When Decypharr refuses torrents/add (not cached, blocked
for legal reasons, or Decypharr unreachable), the same torrent is sent to
qBittorrent under "<category>-fallback". torrents/info merges both backends and
reports fallback items under the original category, and any request naming a
hash that lives in qBittorrent is routed there.
"""
import http.cookiejar
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DECYPHARR = os.environ.get("DECYPHARR_URL", "http://decypharr:8282").rstrip("/")
QBIT = os.environ.get("QBIT_URL", "http://qbittorrent:8080").rstrip("/")
QBIT_USER = os.environ["QBIT_USERNAME"]
QBIT_PASS = os.environ["QBIT_PASSWORD"]
SUFFIX = "-fallback"
TIMEOUT = 60

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("decypharr-fallback")


class QBit:
    """Logged-in session to the real qBittorrent."""

    def __init__(self):
        self.lock = threading.Lock()
        self._new_opener()

    def _new_opener(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _login(self):
        self._new_opener()
        data = urllib.parse.urlencode({"username": QBIT_USER, "password": QBIT_PASS}).encode()
        with self.opener.open(urllib.request.Request(f"{QBIT}/api/v2/auth/login", data=data), timeout=TIMEOUT) as r:
            if r.status not in (200, 204) or r.read().strip() == b"Fails.":
                raise RuntimeError("qBittorrent login failed")

    def request(self, method, path, body=None, headers=None):
        """Returns (status, headers, body). Re-logs in once on 401/403."""
        for attempt in (1, 2):
            req = urllib.request.Request(f"{QBIT}{path}", data=body, method=method, headers=headers or {})
            try:
                with self.opener.open(req, timeout=TIMEOUT) as r:
                    return r.status, dict(r.headers), r.read()
            except urllib.error.HTTPError as e:
                if e.code in (401, 403) and attempt == 1:
                    with self.lock:
                        self._login()
                    continue
                return e.code, dict(e.headers), e.read()
        raise RuntimeError("unreachable")

    def ensure_category(self, category):
        """Create <category>-fallback with the same save path as <category>, if missing."""
        status, _, body = self.request("GET", "/api/v2/torrents/categories")
        if status != 200:
            return
        cats = json.loads(body)
        if not category or category + SUFFIX in cats:
            return
        data = {"category": category + SUFFIX}
        if cats.get(category, {}).get("savePath"):
            data["savePath"] = cats[category]["savePath"]
        self.request("POST", "/api/v2/torrents/createCategory", urllib.parse.urlencode(data).encode(),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        log.info("created qBittorrent category %s", data)

    def fallback_torrents(self, category=None):
        cats = [category + SUFFIX] if category else [None]
        out = []
        for cat in cats:
            q = "?" + urllib.parse.urlencode({"category": cat}) if cat else ""
            status, _, body = self.request("GET", f"/api/v2/torrents/info{q}")
            if status != 200:
                continue
            for t in json.loads(body):
                if t.get("category", "").endswith(SUFFIX):
                    t["category"] = t["category"][: -len(SUFFIX)]
                    out.append(t)
        return out


qbit = QBit()
seen_working = set()  # Decypharr hashes observed in a non-error state since startup


def rewrite_category(body, content_type):
    """Append SUFFIX to the category field of an add request (urlencoded or multipart)."""
    if content_type.startswith("multipart/form-data"):
        pattern = rb'(name="category"\r\n(?:[^\r\n]+\r\n)*\r\n)([^\r\n]*)(\r\n)'
        if re.search(pattern, body):
            return re.sub(pattern, lambda m: m.group(1) + m.group(2) + SUFFIX.encode() + m.group(3), body, count=1)
        return body
    fields = urllib.parse.parse_qsl(body.decode(), keep_blank_values=True)
    fields = [(k, v + SUFFIX if k == "category" and v else v) for k, v in fields]
    return urllib.parse.urlencode(fields).encode()


def category_of(body, content_type):
    if content_type.startswith("multipart/form-data"):
        m = re.search(rb'name="category"\r\n(?:[^\r\n]+\r\n)*\r\n([^\r\n]*)\r\n', body)
        return m.group(1).decode() if m else ""
    return dict(urllib.parse.parse_qsl(body.decode(errors="replace"))).get("category", "")


def hashes_in(path, body, content_type):
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(path).query))
    if body and content_type.startswith("application/x-www-form-urlencoded"):
        params.update(urllib.parse.parse_qsl(body.decode(errors="replace")))
    raw = params.get("hashes") or params.get("hash") or ""
    return {h.lower() for h in raw.split("|") if h and h != "all"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, headers, body):
        self.send_response(status)
        for k, v in headers.items():
            if k.lower() in ("content-type", "set-cookie"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _decypharr(self, method, body, path=None, content_type=None):
        headers = {k: v for k, v in self.headers.items() if k.lower() in ("cookie", "content-type", "authorization", "referer")}
        if content_type:
            headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
            headers["Content-Type"] = content_type
        req = urllib.request.Request(f"{DECYPHARR}{path or self.path}", data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                hdrs = dict(r.headers)
                hdrs["Set-Cookie"] = r.headers.get("Set-Cookie", "")
                if not hdrs["Set-Cookie"]:
                    del hdrs["Set-Cookie"]
                return r.status, hdrs, r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def _handle(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        ctype = self.headers.get("Content-Type", "")
        route = urllib.parse.urlsplit(self.path).path

        try:
            if route == "/health":
                return self._health()
            if route == "/api/v2/torrents/add":
                return self._add(method, body, ctype)
            if route == "/api/v2/torrents/info":
                return self._info(method, body)
            wanted = hashes_in(self.path, body, ctype)
            if wanted:
                fb = {t["hash"].lower() for t in qbit.fallback_torrents()}
                if wanted <= fb:
                    hdrs = {"Content-Type": ctype} if ctype else {}
                    return self._send(*qbit.request(method, self.path, body, hdrs))
            return self._send(*self._decypharr(method, body))
        except Exception as e:  # never leave the arr hanging
            log.exception("error handling %s %s", method, route)
            return self._send(502, {"Content-Type": "text/plain"}, str(e).encode())

    def _add(self, method, body, ctype):
        try:
            status, hdrs, resp = self._decypharr(method, body)
            reason = resp[:200].decode(errors="replace")
        except Exception as e:
            status, hdrs, resp, reason = 0, {}, b"", f"Decypharr unreachable: {e}"
        if 200 <= status < 300 and resp.strip() != b"Fails.":
            return self._send(status, hdrs, resp)
        if status in (401, 403):
            # bad credentials are a config error the *arr must see, not a reason to fall back
            return self._send(status, hdrs, resp)

        log.warning("Decypharr refused add (%s): %s -> falling back to qBittorrent", status, " ".join(reason.split()))
        qbit.ensure_category(category_of(body or b"", ctype))
        qstatus, qhdrs, qresp = qbit.request(method, "/api/v2/torrents/add", rewrite_category(body or b"", ctype), {"Content-Type": ctype})
        if 200 <= qstatus < 300 and qresp.strip() != b"Fails.":
            log.info("added to qBittorrent")
            return self._send(200, {"Content-Type": "text/plain"}, b"Ok.")
        log.error("qBittorrent also refused (%s): %s", qstatus, qresp[:200])
        return self._send(status or 502, hdrs, resp)

    def _info(self, method, body):
        status, hdrs, resp = 0, {"Content-Type": "application/json"}, b"[]"
        try:
            status, hdrs, resp = self._decypharr(method, body)
        except Exception as e:
            log.warning("Decypharr unreachable for info: %s", e)
        items = json.loads(resp) if 200 <= status < 300 else []
        category = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query)).get("category")
        try:
            fallback = qbit.fallback_torrents(category)
            moved = {t["hash"].lower() for t in fallback}
            kept = []
            for t in items:
                h = t.get("hash", "").lower()
                if h in moved:
                    continue  # already handed to qBittorrent; report that copy instead
                if t.get("state") != "error":
                    seen_working.add(h)
                elif h in seen_working and self._move_to_qbit(t):
                    # only torrents that failed while being tracked; stale leftovers stay put
                    seen_working.discard(h)
                    moved.add(h)
                    continue
                kept.append(t)
            if moved - {t["hash"].lower() for t in fallback}:
                fallback = qbit.fallback_torrents(category)
            items = kept + fallback
        except Exception as e:
            log.warning("qBittorrent unreachable for info: %s", e)
        hdrs["Content-Type"] = "application/json"
        return self._send(200, hdrs, json.dumps(items).encode())

    def _move_to_qbit(self, torrent):
        """A torrent Decypharr accepted but then failed: restart it on qBittorrent by hash."""
        h, cat = torrent["hash"].lower(), torrent.get("category", "")
        qbit.ensure_category(cat)
        form = urllib.parse.urlencode({
            "urls": f"magnet:?xt=urn:btih:{h}&dn={urllib.parse.quote(torrent.get('name', h))}",
            "category": cat + SUFFIX if cat else "",
        }).encode()
        status, _, resp = qbit.request("POST", "/api/v2/torrents/add", form, {"Content-Type": "application/x-www-form-urlencoded"})
        if not 200 <= status < 300 or resp.strip() == b"Fails.":
            log.error("could not move failed torrent %s to qBittorrent (%s): %s", torrent.get("name"), status, resp[:200])
            return False
        log.warning("Decypharr reports %s as failed -> restarted on qBittorrent", torrent.get("name", h))
        self._decypharr("POST", urllib.parse.urlencode({"hashes": h, "deleteFiles": "true"}).encode(),
                        "/api/v2/torrents/delete", "application/x-www-form-urlencoded")
        return True

    def _health(self):
        """200 while this service runs; reports whether each backend answers."""
        def reachable(fn):
            try:
                return fn()[0] < 500
            except Exception:
                return False
        status = {
            "decypharr": reachable(lambda: self._decypharr("GET", None, "/api/v2/app/version")),
            "qbittorrent": reachable(lambda: qbit.request("GET", "/api/v2/app/version")),
        }
        return self._send(200, {"Content-Type": "application/json"}, json.dumps(status).encode())

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def serve(port, host="0.0.0.0"):
    server = ThreadingHTTPServer((host, port), Handler)
    log.info("decypharr-fallback on :%d  primary=%s  fallback=%s", server.server_port, DECYPHARR, QBIT)
    return server


if __name__ == "__main__":
    serve(int(os.environ.get("PORT", "8283"))).serve_forever()
