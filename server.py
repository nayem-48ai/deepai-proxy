#!/usr/bin/env python3
"""Local dev server (stdlib only). Run: python3 server.py [port]"""
import sys, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import core

class H(BaseHTTPRequestHandler):
    def _handle(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        headers = {k: v for k, v in self.headers.items()}
        status, resp_headers, resp_body = core.handle_request(self.command, self.path, headers, body)
        self.send_response(status)
        for k, v in resp_headers.items():
            self.send_header(k, v)
        self.end_headers()
        if isinstance(resp_body, (bytes, bytearray)):
            self.wfile.write(resp_body)
        else:
            for chunk in resp_body:
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except Exception:
                    break

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"DeepAI Proxy running at http://127.0.0.1:{port}")
    print("Set DEEPAI_DEVICE_ID env var to enable image generation.")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
