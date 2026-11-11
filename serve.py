"""Serve the site locally; refresh favorites on a schedule and other resorts on demand.

    python3 serve.py            # http://localhost:8765
    python3 serve.py --no-update

Favorites (catalog.FAVORITES) refresh every 3 h. Any other resort refreshes when you open it
and its data is missing or older than 3 h (GET /api/refresh?key=alta), which keeps ~40 resorts
inside Open-Meteo's free daily quota.
"""
import functools, http.server, json, os, sys, threading, time, traceback, urllib.parse
import update
from catalog import FAVORITES

PORT = 8765
REFRESH_S = 3 * 3600  # models update every 6 h; 3 h catches each new run without wasting quota
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_lock = threading.Lock()   # one update at a time: keeps request bursts inside the per-minute limit
_status = {}               # key -> "refreshing" | "error: ..."


def age_s(key):
    p = os.path.join(update.OUT, f"{key}.json")
    return time.time() - os.path.getmtime(p) if os.path.exists(p) else None


def refresh(key):
    _status[key] = "refreshing"
    try:
        with _lock:
            update.update_one(key)
            update.write_index()
        _status.pop(key, None)
    except Exception as e:
        traceback.print_exc()
        _status[key] = f"error: {e}"


def scheduler():
    while True:
        for key in FAVORITES:
            a = age_s(key)
            if a is None or a > REFRESH_S - 300:
                refresh(key)
        time.sleep(600)


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/refresh":
            return super().do_GET()
        key = urllib.parse.parse_qs(u.query).get("key", [""])[0]
        if key not in update.RESORTS:
            return self._json(404, dict(status="unknown resort"))
        a = age_s(key)
        st = _status.get(key)
        if st != "refreshing" and (a is None or a > REFRESH_S or "force" in u.query):
            threading.Thread(target=refresh, args=(key,), daemon=True).start()
            st = "refreshing"
        self._json(200, dict(status=st or "fresh", age_s=a))

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    update.write_index()
    if "--no-update" not in sys.argv:
        threading.Thread(target=scheduler, daemon=True).start()
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(Handler, directory=WEB)) as srv:
        print(f"Serving http://localhost:{PORT}")
        srv.serve_forever()
