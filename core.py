"""Core logic for the DeepAI free proxy (no external deps).

Serves both a local stdlib server (server.py) and Vercel Python (api/index.py)
through handle_request(method, path, headers, body) -> (status, headers, body).

API surface (OpenAI / OpenRouter compatible):
  POST /api/v1/chat/completions   OpenAI chat completions (stream + non-stream, multimodal)
  POST /api/v1/responses          Codex Responses API
  GET  /api/v1/models             OpenRouter-style model list
  POST /api/v1/images/generations
  POST /api/v1/images/edits
  POST /api/v1/keys  GET /api/v1/keys   key management
Legacy /api/* aliases are also supported.
"""
import json, re, os, uuid, base64, hashlib, random, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
DEEPAI_CHAT = "https://api.deepai.org/hacking_is_a_serious_crime"
DEEPAI_IMG = "https://api.deepai.org/api"
KEYS_FILE = os.path.join(HERE, "data", "keys.json")
KEY_PREFIX = "tnxbd-"

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
}

# ---------------- models ----------------
def load_models():
    with open(os.path.join(HERE, "models.json")) as f:
        return json.load(f)

def all_model_ids():
    ids = []
    for cat in load_models()["categories"].values():
        for m in cat["models"]:
            ids.append(m["id"])
    return ids

def openrouter_models():
    """OpenRouter/OpenAI-style /v1/models listing."""
    data = []
    for cat in load_models()["categories"].values():
        for m in cat["models"]:
            kind = m.get("type")
            if kind == "image":
                in_mod, out_mod, modality, ctx = ["text"], ["image"], "image", 0
            elif kind == "image-edit":
                in_mod, out_mod, modality, ctx = ["text", "image"], ["image"], "image", 0
            else:
                in_mod = m.get("input_modalities", ["text"])
                out_mod = m.get("output_modalities", ["text"])
                ctx = m.get("context_length", 32000)
                modality = "text"
            data.append({
                "id": m["id"],
                "object": "model",
                "created": 1700000000,
                "owned_by": "deepai",
                "name": m["label"],
                "description": m.get("note", ""),
                "architecture": {
                    "modality": modality,
                    "input_modalities": in_mod,
                    "output_modalities": out_mod,
                    "tokenizer": m.get("tokenizer", "deepai"),
                    "context_length": ctx,
                    "instruction_window": ctx,
                },
                "pricing": {"prompt": "0", "completion": "0", "request": "0",
                            "image": "0", "web_search": "0", "internal_reasoning": "0"},
                "top_provider": {"context_length": ctx, "is_moderated": False,
                                 "max_completion_tokens": None},
                "per_request_limits": None,
            })
    return {"object": "list", "data": data}

# ---------------- tryit key (MD5, browser-client compatible) ----------------
def _h(s):
    return hashlib.md5(s.encode()).hexdigest()[::-1]

def make_tryit_key():
    r = str(random.randint(0, 10 ** 11))
    inner = _h(UA + r + "hackers_become_a_little_stinkier_every_time_they_hack")
    return "tryit-" + r + "-" + _h(UA + _h(UA + inner))

# ---------------- multipart builders ----------------
def _multipart(fields):
    boundary = "----deepaiproxy" + uuid.uuid4().hex
    body = "".join(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
        for k, v in fields.items()
    ).encode() + f"--{boundary}--\r\n".encode()
    return boundary, body

def _build_multipart(parts):
    """parts: ('text', name, value) | ('file', name, filename, bytes, ctype)."""
    boundary = "----deepaiproxy" + uuid.uuid4().hex
    body = b""
    for p in parts:
        if p[0] == "text":
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{p[1]}\"\r\n\r\n{p[2]}\r\n".encode()
        else:
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{p[1]}\"; filename=\"{p[2]}\"\r\n"
                     f"Content-Type: {p[4]}\r\n\r\n").encode() + p[3] + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return boundary, body

def _image_parts(images):
    """DeepAI image fields (image/image2/image3) from data URLs or http URLs."""
    out = []
    for img, nm in zip(images, ["image", "image2", "image3"]):
        if img.startswith("data:"):
            try:
                hdr, b64 = img.split(",", 1)
                ctype = hdr.split(":", 1)[1].split(";", 1)[0] if ":" in hdr else "image/png"
                out.append(("file", nm, "img.png", base64.b64decode(b64), ctype))
            except Exception:
                pass
        elif img.startswith("http"):
            out.append(("text", nm, img))
    return out

def _file_part(data_url, name="file"):
    try:
        hdr, b64 = data_url.split(",", 1)
        ctype = hdr.split(":", 1)[1].split(";", 1)[0] if ":" in hdr else "application/octet-stream"
        return ("file", name, "file.bin", base64.b64decode(b64), ctype)
    except Exception:
        return None

# ---------------- DeepAI chat (multimodal-aware) ----------------
def _img_cookie():
    if os.environ.get("DEEPAI_COOKIE"):
        return os.environ["DEEPAI_COOKIE"]
    did = os.environ.get("DEEPAI_DEVICE_ID")
    return f"deepai_device_id={did}" if did else ""

def stream_chat(model, messages, images=None, files=None):
    """Yield text deltas from DeepAI's anonymous chat endpoint.
    images/files are forwarded when present; on error we fall back to text-only
    so chat never breaks for models that don't accept attachments."""
    chat_history = json.dumps(messages)
    parts = [
        ("text", "chat_style", "chat"),
        ("text", "model", model),
        ("text", "chatHistory", chat_history),
        ("text", "session_uuid", str(uuid.uuid4())),
        ("text", "tool_activity_support", "1"),
        ("text", "hacker_is_stinky", "very_stinky"),
        ("text", "enabled_tools", json.dumps(["image_generator", "image_editor"])),
    ]
    had_attach = bool(images or files)
    for p in _image_parts(images or []):
        parts.append(p)
    fp = _file_part((files or [None])[0]) if files else None
    if fp:
        parts.append(fp)

    boundary, body = _build_multipart(parts)
    req = urllib.request.Request(
        DEEPAI_CHAT, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": UA},
        method="POST",
    )
    record_re = re.compile(r"\x1c\{.*?\}\x1c", re.DOTALL)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            buf = ""
            while True:
                chunk = resp.read(1024)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                last = buf.rfind("\x1c")
                if last != -1 and buf[last:].count("\x1c") == 1:
                    visible, buf = buf[:last], buf[last:]
                else:
                    visible, buf = buf, ""
                visible = record_re.sub("", visible)
                if visible:
                    yield visible
    except urllib.error.HTTPError:
        if had_attach:
            yield from stream_chat(model, messages)
            return
        raise

def chat_once(model, messages, images=None, files=None):
    return "".join(stream_chat(model, messages, images, files))

# ---------------- DeepAI images ----------------
def call_image(endpoint, fields):
    cookie = _img_cookie()
    if not cookie:
        return None, "Image generation needs DEEPAI_DEVICE_ID (server env)."
    boundary, body = _multipart(fields)
    req = urllib.request.Request(
        DEEPAI_IMG + "/" + endpoint, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "User-Agent": UA, "Cookie": cookie, "api-key": make_tryit_key()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("err", detail)
        except Exception:
            pass
        return None, f"DeepAI {e.code}: {detail}"

def call_image_edit_with_blob(blob_bytes, fields):
    cookie = _img_cookie()
    if not cookie:
        return None, "Set DEEPAI_DEVICE_ID for image editing."
    boundary = "----deepaiproxy" + uuid.uuid4().hex
    payload = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"img.png\"\r\n"
               f"Content-Type: image/png\r\n\r\n").encode() + blob_bytes + f"\r\n--{boundary}\r\n".encode()
    for k, v in fields.items():
        payload += f"Content-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    payload += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(DEEPAI_IMG + "/image-editor", data=payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": UA,
                 "Cookie": cookie, "api-key": make_tryit_key()}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("err", detail)
        except Exception:
            pass
        return None, f"DeepAI {e.code}: {detail}"

# ---------------- API keys ----------------
def _seed_key():
    return os.environ.get("SEED_API_KEY") or (KEY_PREFIX + os.urandom(12).hex())

def _save_keys(data):
    try:
        os.makedirs(os.path.dirname(KEYS_FILE), exist_ok=True)
        with open(KEYS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass  # read-only FS (e.g. Vercel) — keys are best-effort / env-based

def gen_key(name="key"):
    data = _load_keys()
    k = KEY_PREFIX + os.urandom(12).hex()
    data["keys"].append({"key": k, "name": name, "created": int(time.time())})
    _save_keys(data)
    if not os.path.exists(KEYS_FILE):
        return os.environ.get("SEED_API_KEY") or (data["keys"][0]["key"] if data["keys"] else k)
    return k

def list_keys():
    return _load_keys()["keys"]

def valid_key(auth_header):
    if not auth_header:
        return False
    t = auth_header.replace("Bearer ", "").replace("bearer ", "")
    if t == os.environ.get("SEED_API_KEY"):
        return True
    return any(k["key"] == t for k in _load_keys()["keys"])

def _load_keys():
    if not os.path.exists(KEYS_FILE):
        seed = _seed_key()
        data = {"keys": [{"key": seed, "name": "default", "created": int(time.time())}]}
        _save_keys(data)
        return data
    with open(KEYS_FILE) as f:
        return json.load(f)

# ---------------- helpers ----------------
def _size_to_wh(size):
    try:
        w, h = size.lower().split("x")
        return int(w), int(h)
    except Exception:
        return 640, 640

def _truncate(messages, keep=30):
    return messages[-keep:] if len(messages) > keep else messages

def _messages_from(body):
    msgs = body.get("messages")
    if msgs:
        return _truncate(msgs)
    inp = body.get("input", "")
    if isinstance(inp, str):
        return [{"role": "user", "content": inp}]
    out = []
    for it in inp:
        if isinstance(it, dict) and it.get("role"):
            c = it.get("content", "")
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
            out.append({"role": it["role"], "content": c})
    return _truncate(out)

def _json_body(body_bytes):
    if not body_bytes:
        return {}
    try:
        return json.loads(body_bytes.decode())
    except Exception:
        return {}

def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()

# ---------------- router ----------------
def handle_request(method, path, headers, body_bytes):
    method = method.upper()
    raw = path.split("?")[0]
    is_v1 = raw.startswith("/api/v1")
    path = raw
    if is_v1:
        path = "/api" + raw[len("/api/v1"):]

    def respond(status, resp_headers, body):
        h = dict(resp_headers)
        h.update(CORS)
        return status, h, body

    if method == "OPTIONS":
        return respond(204, {}, b"")

    if not path.startswith("/api/"):
        return respond(*_serve_static(path))

    # models (public)
    if path == "/api/models" and method == "GET":
        if is_v1:
            return respond(200, {"Content-Type": "application/json"}, json.dumps(openrouter_models()).encode())
        return respond(200, {"Content-Type": "application/json"}, json.dumps(load_models()).encode())

    # keys (site)
    if path == "/api/keys":
        if method == "GET":
            return respond(200, {"Content-Type": "application/json"}, json.dumps({"object": "list", "data": list_keys()}).encode())
        if method == "POST":
            body = _json_body(body_bytes)
            k = gen_key((body.get("name") or "key"))
            return respond(200, {"Content-Type": "application/json"}, json.dumps({"key": k}).encode())
        return respond(405, {}, b"")

    # auth required below
    if not valid_key(headers.get("Authorization", "")):
        return respond(401, {"Content-Type": "application/json"},
                       json.dumps({"error": "Invalid or missing API key. Use: Authorization: Bearer <key>"}).encode())

    # chat completions (OpenAI format, multimodal)
    if path in ("/api/chat", "/api/chat/completions") and method == "POST":
        body = _json_body(body_bytes)
        model = body.get("model", "standard")
        messages = _messages_from(body)
        images = body.get("images") or None
        files = body.get("files") or None
        if body.get("stream"):
            def gen():
                yield b"data: " + json.dumps({"id": "chatcmpl-" + uuid.uuid4().hex[:12],
                                              "object": "chat.completion.chunk",
                                              "model": model,
                                              "choices": [{"index": 0, "delta": {"role": "assistant"}}]}).encode() + b"\n\n"
                for d in stream_chat(model, messages, images, files):
                    yield b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": d}}]}).encode() + b"\n\n"
                yield b"data: [DONE]\n\n"
            return respond(200, {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}, gen())
        text = chat_once(model, messages, images, files)
        return respond(200, {"Content-Type": "application/json"}, json.dumps({
            "id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]
        }).encode())

    # responses (Codex Responses API)
    if path == "/api/responses" and method == "POST":
        body = _json_body(body_bytes)
        model = body.get("model", "standard")
        messages = _messages_from(body)
        images = body.get("images") or None
        files = body.get("files") or None
        rid = "resp_" + uuid.uuid4().hex[:24]
        if body.get("stream"):
            iid = "msg_" + uuid.uuid4().hex[:12]
            def gen():
                yield _sse("response.created", {"id": rid, "object": "response", "status": "in_progress"})
                yield _sse("response.output_item.added", {"item": {"id": iid, "type": "message", "role": "assistant", "content": []}})
                for d in stream_chat(model, messages, images, files):
                    yield _sse("response.output_text.delta", {"item_id": iid, "delta": d})
                yield _sse("response.output_text.done", {"item_id": iid})
                yield _sse("response.output_item.done", {"item": {"id": iid, "type": "message", "role": "assistant"}})
                yield _sse("response.completed", {"id": rid, "status": "completed"})
            return respond(200, {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}, gen())
        text = chat_once(model, messages, images, files)
        return respond(200, {"Content-Type": "application/json"}, json.dumps({
            "id": rid, "object": "response", "model": model,
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": text}]}],
            "status": "completed"}).encode())

    # images: text to image
    if path == "/api/images/generations" and method == "POST":
        body = _json_body(body_bytes)
        prompt = body.get("prompt", "")
        w, h = _size_to_wh(body.get("size", "640x640"))
        fields = {"text": prompt, "generation_source": "img", "width": str(w), "height": str(h),
                  "image_generator_version": "hd", "use_new_model": "true",
                  "use_old_model": "false", "quality": "true"}
        res, err = call_image("text2img", fields)
        if err:
            return respond(500, {"Content-Type": "application/json"}, json.dumps({"error": err}).encode())
        url = (res or {}).get("output_url") or (res or {}).get("share_url")
        return respond(200, {"Content-Type": "application/json"},
                       json.dumps({"created": int(time.time()), "data": [{"url": url}]}).encode())

    # images: image to image (edit)
    if path == "/api/images/edits" and method == "POST":
        body = _json_body(body_bytes)
        prompt = body.get("prompt", "")
        image = body.get("image", "")
        w, h = _size_to_wh(body.get("size", "640x640"))
        fields = {"text": prompt, "generation_source": "img", "width": str(w), "height": str(h),
                  "image_generator_version": "hd", "quality": "true"}
        if image.startswith("data:"):
            try:
                blob = base64.b64decode(image.split(",", 1)[1])
            except Exception as e:
                return respond(400, {"Content-Type": "application/json"}, json.dumps({"error": "bad image data: " + str(e)}).encode())
            res, err = call_image_edit_with_blob(blob, fields)
        else:
            fields["image"] = image
            res, err = call_image("image-editor", fields)
        if err:
            return respond(500, {"Content-Type": "application/json"}, json.dumps({"error": err}).encode())
        url = (res or {}).get("output_url") or (res or {}).get("share_url")
        return respond(200, {"Content-Type": "application/json"},
                       json.dumps({"created": int(time.time()), "data": [{"url": url}]}).encode())

    return respond(404, {"Content-Type": "application/json"}, json.dumps({"error": "not found"}).encode())

# ---------------- static ----------------
def _serve_static(path):
    public = os.path.join(HERE, "public")
    if path in ("", "/"):
        path = "/index.html"
    fp = os.path.normpath(os.path.join(public, path.lstrip("/")))
    if not fp.startswith(public) or not os.path.isfile(fp):
        return 404, {"Content-Type": "text/plain"}, b"Not found"
    ctype = "text/html"
    if fp.endswith(".css"):
        ctype = "text/css"
    elif fp.endswith(".js"):
        ctype = "application/javascript"
    elif fp.endswith(".json"):
        ctype = "application/json"
    elif fp.endswith(".svg"):
        ctype = "image/svg+xml"
    with open(fp, "rb") as f:
        return 200, {"Content-Type": ctype}, f.read()
