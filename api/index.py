"""Vercel Python entry point (WSGI app). Reuses core.handle_request."""
import os
import core

def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")
    if not path:
        path = "/"
    raw_headers = {}
    for k, v in environ.items():
        if k.startswith("HTTP_"):
            raw_headers[k[5:].replace("_", "-").title()] = v
    if "CONTENT_TYPE" in environ:
        raw_headers["Content-Type"] = environ["CONTENT_TYPE"]
    if "HTTP_AUTHORIZATION" in environ:
        raw_headers["Authorization"] = environ["HTTP_AUTHORIZATION"]

    try:
        length = int(environ.get("CONTENT_LENGTH", 0) or 0)
    except ValueError:
        length = 0
    body = b""
    if length:
        body = environ["wsgi.input"].read(length)

    status, headers, resp_body = core.handle_request(method, path, raw_headers, body)

    if isinstance(resp_body, (bytes, bytearray)):
        resp_body = [resp_body]
    elif isinstance(resp_body, str):
        resp_body = [resp_body.encode()]

    start_response(str(status), [(k, str(v)) for k, v in headers.items()])
    return resp_body
