"""Core logic for the DeepAI free proxy (no external deps).

Serves both a local stdlib server (server.py) and Vercel Python (api/index.py)
through handle_request(method, path, headers, body) -> (status, headers, body).
"""
import json, re, os, uuid, base64, hashlib, random, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
DEEPAI_CHAT = "https://api.deepai.org/hacking_is_a_serious_crime"
DEEPAI_IMG = "https://api.deepai.org/api"
KEYS_FILE = os.path.join(HERE, "data", "keys.json")

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

# ---------------- tryit key (MD5, browser-client compatible) ----------------
def _h(s):
    return hashlib.md5(s.encode()).hexdigest()[::-1]

def make_tryit_key():
    r = str(random.randint(0, 10 ** 11))
    inner = _h(UA + r + "hackers_become_a_little_stinkier_every_time_they_hack")
    return "tryit-" + r + "-" + _h(UA + _h(UA + inner))

# ---------------- DeepAI chat ----------------
def _multipart(fields):
    boundary = "----deepaiproxy" + uuid.uuid4().hex
    body = "".join(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
        for k, v in fields.items()
    ).encode() + f"--{boundary}--\r\n".encode()
    return boundary, body

def _img_cookie():
    if os.environ.get("DEEPAI_COOKIE"):
        return os.environ["DEEPAI_COOKIE"]
    did = os.environ.get("DEEPAI_DEVICE_ID")
    return f"deepai_device_id={did}" if did else ""

def stream_chat(model, messages):
    """Yield text deltas from DeepAI's anonymous chat endpoint."""
    chat_history = json.dumps(messages)
    boundary, body = _multipart({
        "chat_style": "chat",
        "model": model,
        "chatHistory": chat_history,
        "session_uuid": str(uuid.uuid4()),
        "tool_activity_support": "1",
        "hacker_is_stinky": "very_stinky",
        "enabled_tools": json.dumps(["image_generator", "image_editor"]),
    })
    req = urllib.request.Request(
        DEEPAI_CHAT, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": UA},
        method="POST",
    )
    record_re = re.compile(r"\x1c\{.*?\}\x1c", re.DOTALL)
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

def chat_once(model, messages):
    return "".join(stream_chat(model, messages))

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

# ---------------- API keys ----------------
def _load_keys():
    if not os.path.exists(KEYS_FILE):
        seed = _seed_key()
        data = {"keys": [{"key": seed, "name": "default", "created": int(time.time())}]}
        _save_keys(data)
        return data
    with open(KEYS_FILE) as f:
        return json.load(f)

def _seed_key():
    return os.environ.get("SEED_API_KEY") or ("sk-" + uuid.uuid4().hex + uuid.uuid4().hex[:8])

def _save_keys(data):
    try:
        os.makedirs(os.path.dirname(KEYS_FILE), exist_ok=True)
        with open(KEYS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass  # read-only FS (e.g. Vercel) — keys are best-effort / env-based

def gen_key(name="key"):
    data = _load_keys()
    k = "sk-" + uuid.uuid4().hex + uuid.uuid4().hex[:8]
    data["keys"].append({"key": k, "name": name, "created": int(time.time())})
    _save_keys(data)
    # On read-only/ephemeral FS, return the deterministic seeded key so auth stays consistent.
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
    # strip query string
    path = path.split("?")[0]

    # ---- static ----
    if not path.startswith("/api/"):
        return _serve_static(path)

    # ---- models (public) ----
    if path == "/api/models" and method == "GET":
        return 200, {"Content-Type": "application/json"}, json.dumps(load_models()).encode()

    # ---- keys (site) ----
    if path == "/api/keys":
        if method == "GET":
            return 200, {"Content-Type": "application/json"}, json.dumps({"keys": list_keys()}).encode()
        if method == "POST":
            body = _json_body(body_bytes)
            name = (body.get("name") or "key")
            k = gen_key(name)
            return 200, {"Content-Type": "application/json"}, json.dumps({"key": k}).encode()
        return 405, {}, b""

    # ---- everything below needs a valid API key ----
    if not valid_key(headers.get("Authorization", "")):
        return 401, {"Content-Type": "application/json"}, json.dumps({"error": "Invalid or missing API key. Use: Authorization: Bearer <key>"}).encode()

    # ---- chat completions (OpenAI format) ----
    if path == "/api/chat" and method == "POST":
        body = _json_body(body_bytes)
        model = body.get("model", "standard")
        messages = _messages_from(body)
        if body.get("stream"):
            def gen():
                yield b"data: " + json.dumps({"choices": [{"delta": {"role": "assistant"}}]}).encode() + b"\n\n"
                for d in stream_chat(model, messages):
                    yield b"data: " + json.dumps({"choices": [{"delta": {"content": d}}]}).encode() + b"\n\n"
                yield b"data: [DONE]\n\n"
            return 200, {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}, gen()
        text = chat_once(model, messages)
        return 200, {"Content-Type": "application/json"}, json.dumps({
            "id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]
        }).encode()

    # ---- responses (Codex Responses API) ----
    if path == "/api/responses" and method == "POST":
        body = _json_body(body_bytes)
        model = body.get("model", "standard")
        messages = _messages_from(body)
        rid = "resp_" + uuid.uuid4().hex[:24]
        if body.get("stream"):
            iid = "msg_" + uuid.uuid4().hex[:12]
            def gen():
                yield _sse("response.created", {"id": rid, "object": "response", "status": "in_progress"})
                yield _sse("response.output_item.added", {"item": {"id": iid, "type": "message", "role": "assistant", "content": []}})
                for d in stream_chat(model, messages):
                    yield _sse("response.output_text.delta", {"item_id": iid, "delta": d})
                yield _sse("response.output_text.done", {"item_id": iid})
                yield _sse("response.output_item.done", {"item": {"id": iid, "type": "message", "role": "assistant"}})
                yield _sse("response.completed", {"id": rid, "status": "completed"})
            return 200, {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}, gen()
        text = chat_once(model, messages)
        return 200, {"Content-Type": "application/json"}, json.dumps({
            "id": rid, "object": "response", "model": model,
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": text}]}],
            "status": "completed"}).encode()

    # ---- images: text to image ----
    if path == "/api/images/generations" and method == "POST":
        body = _json_body(body_bytes)
        prompt = body.get("prompt", "")
        w, h = _size_to_wh(body.get("size", "640x640"))
        fields = {"text": prompt, "generation_source": "img", "width": str(w), "height": str(h),
                  "image_generator_version": "hd", "use_new_model": "true",
                  "use_old_model": "false", "quality": "true"}
        res, err = call_image("text2img", fields)
        if err:
            return 500, {"Content-Type": "application/json"}, json.dumps({"error": err}).encode()
        url = (res or {}).get("output_url") or (res or {}).get("share_url")
        return 200, {"Content-Type": "application/json"}, json.dumps({"created": int(time.time()), "data": [{"url": url}]}).encode()

    # ---- images: image to image (edit) ----
    if path == "/api/images/edits" and method == "POST":
        body = _json_body(body_bytes)
        prompt = body.get("prompt", "")
        image = body.get("image", "")
        w, h = _size_to_wh(body.get("size", "640x640"))
        fields = {"text": prompt, "generation_source": "img"}
        if image.startswith("data:"):
            try:
                blob = base64.b64decode(image.split(",", 1)[1])
                boundary = "----deepaiproxy" + uuid.uuid4().hex
                payload = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"img.png\"\r\n"
                           f"Content-Type: image/png\r\n\r\n").encode() + blob + f"\r\n--{boundary}\r\n".encode()
                for k, v in fields.items():
                    payload += f"Content-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                payload += f"--{boundary}--\r\n".encode()
                cookie = _img_cookie()
                if not cookie:
                    return 400, {"Content-Type": "application/json"}, json.dumps({"error": "Set DEEPAI_DEVICE_ID for image editing."}).encode()
                req = urllib.request.Request(DEEPAI_IMG + "/image-editor", data=payload,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": UA,
                             "Cookie": cookie, "api-key": make_tryit_key()}, method="POST")
                with urllib.request.urlopen(req, timeout=120) as resp:
                    res = json.loads(resp.read().decode())
                url = res.get("output_url") or res.get("share_url")
                return 200, {"Content-Type": "application/json"}, json.dumps({"created": int(time.time()), "data": [{"url": url}]}).encode()
            except Exception as e:
                return 500, {"Content-Type": "application/json"}, json.dumps({"error": str(e)}).encode()
        else:
            fields["image"] = image
            fields["width"] = str(w); fields["height"] = str(h)
            fields["image_generator_version"] = "hd"; fields["quality"] = "true"
            res, err = call_image("image-editor", fields)
            if err:
                return 500, {"Content-Type": "application/json"}, json.dumps({"error": err}).encode()
            url = (res or {}).get("output_url") or (res or {}).get("share_url")
            return 200, {"Content-Type": "application/json"}, json.dumps({"created": int(time.time()), "data": [{"url": url}]}).encode()

    return 404, {"Content-Type": "application/json"}, json.dumps({"error": "not found"}).encode()

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
