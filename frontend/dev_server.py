"""Tiny static file server that mirrors vercel.json rewrites for local dev.

Usage: python3 dev_server.py [port]
"""
import http.server
import socketserver
import os
import re
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
ROOT = os.path.dirname(os.path.abspath(__file__))

# Keep in sync with the `rewrites` array in /vercel.json.
REWRITES = [
    # /r/:op_id is a permanent redirect on Vercel; for local dev we just
    # rewrite it to keep behaviour identical without involving the redirect
    # mechanism.
    (re.compile(r"^/r/[^/]+/?$"),     "/r.html"),
    (re.compile(r"^/report/?$"),       "/r.html"),
    (re.compile(r"^/report/[^/]+/?$"), "/r.html"),
    (re.compile(r"^/attendance/?$"),       "/attendance.html"),
    (re.compile(r"^/attendance/[^/]+/?$"), "/attendance.html"),
    (re.compile(r"^/ops/?$"),          "/ops.html"),
    (re.compile(r"^/ops/general/?$"),  "/ops-admin.html"),
    # /ops/chief is a permanent redirect to /ops/general on Vercel; in dev we
    # rewrite it so older bookmarks still serve a working page.
    (re.compile(r"^/ops/chief/?$"),    "/ops-admin.html"),
    (re.compile(r"^/ops/captain/?$"),  "/captain.html"),
    (re.compile(r"^/ops/dashboard/?$"),"/dashboard.html"),
    (re.compile(r"^/ops/analytics/?$"),"/analytics.html"),
    (re.compile(r"^/ops/validate/?$"), "/validate.html"),
]

# Clean URL aliases (cleanUrls:true on Vercel) — let /onboard serve /onboard.html, etc.
CLEAN_URL_CANDIDATES = ("onboard", "admin", "r", "ops", "ops-admin", "captain", "dashboard", "analytics")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def translate_path(self, path):
        # Strip query string for matching.
        raw = path.split("?", 1)[0]
        for pat, dest in REWRITES:
            if pat.match(raw):
                path = dest + (("?" + path.split("?", 1)[1]) if "?" in path else "")
                break
        else:
            # cleanUrls: add .html if a matching file exists.
            stem = raw.lstrip("/").rstrip("/")
            if stem in CLEAN_URL_CANDIDATES:
                candidate = os.path.join(ROOT, stem + ".html")
                if os.path.exists(candidate):
                    path = "/" + stem + ".html" + (
                        ("?" + path.split("?", 1)[1]) if "?" in path else ""
                    )
        return super().translate_path(path)


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with ReusableThreadingTCPServer(("", PORT), Handler) as s:
        print(f"dev_server: serving {ROOT} at http://localhost:{PORT}")
        try:
            s.serve_forever()
        except KeyboardInterrupt:
            pass
