"""A stand-in personal-agent endpoint: receives sentinel webhooks and verifies them.

Real personal agents would hand the verified alert to their LLM along with what
they know about their person; this one just prints it.
"""
import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from .notify import sign


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        ok = hmac.compare_digest(self.headers.get("X-Sentinel-Signature", ""), sign(body))
        self.send_response(200 if ok else 401)
        self.end_headers()
        if ok:
            a = json.loads(body)
            print(f"[verified] {a['level'].upper()} ({a['kind']}) for {a.get('label') or a['subscriber_id']}: "
                  f"{a['message']}", flush=True)
        else:
            print("[rejected] bad signature", flush=True)

    def log_message(self, *args):
        pass


def serve(port=9000):
    print(f"webhook sink on http://localhost:{port}/hook", flush=True)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
