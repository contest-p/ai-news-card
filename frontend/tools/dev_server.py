"""Local static frontend with SPA routes, bound only to the loopback interface."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {"/", "/login", "/privacy", "/subscribe", "/complete", "/manage", "/ended", "/feedback", "/service"}


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        # Firebase already authorizes localhost, but not the numeric loopback host.
        if urlsplit("http://" + self.headers.get("Host", "")).hostname == "127.0.0.1":
            self.send_response(307)
            self.send_header("Location", f"http://localhost:{self.server.server_port}{self.path}")
            self.end_headers()
            return
        if urlsplit(self.path).path.rstrip("/") in ROUTES or self.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5500)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(ROOT)))
    print(f"Frontend: http://localhost:{args.port}", flush=True)
    server.serve_forever()
