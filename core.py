"""Core logic for the DeepAI free proxy (no external deps).

Serves both a local stdlib server (server.py) and Vercel Python (api/index.py)
through handle_request(method, path, headers, body) -> (status, headers, body).

API surface (OpenAI / OpenRouter compatible):
  POST /api/v1/chat/completions   OpenAI chat completions (stream + non-stream, multimodal)
  POST /api/v1/responses          Codex Responses API
  GET  /api/v1/models             OpenRouter-style model list
  POST /api/v1/images/generations (JSON or multipart)  text-to-image
  POST /api/v1/images/edits       (JSON or multipart)  image-to-image
  POST /api/v1/keys  -> create a key (public by default; admin role if admin requests)
  GET  /api/v1/keys  -> list keys (admin key required)
Legacy /api/* aliases are also supported.
"""
import json, re, os, uuid, base64, hashlib, random, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
DEEPAI_CHAT = "https://api.deepai.org/hacking_is_a_serious_crime"
DEEPAI_IMG = "https://api.deepai.org/api"
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
                "id": m["id"], "object": "model", "created": 1700000000,
                "owned_by": "deepai", "name": m["label"], "description": m.get("note", ""),
                "recommended": m.get("recommended", False),
                "architecture": {"modality": modality, "input_modalities": in_mod, "output_modalities": out_mod,
                                  "tokenizer": m.get("tokenizer", "deepai"), "context_length": ctx, "instruction_window": ctx},
                "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0", "web_search": "0", "internal_reasoning": "0"},
                "top_provider": {"context_length": ctx, "is_moderated": False, "max_completion_tokens": None},
                "per_request_limits": None,
            })
    return {"object": "list", "data": data}

# ---------------- tryit key (MD5, browser-client compatible) ----------------
def _h(s):
    return hashlib.md5(s.encode()).hexdigest()[::-1]
def make_tryit_key(ua=UA):
    r = str(random.randint(0, 10 ** 11))
    inner = _h(ua + r + "hackers_become_a_little_stinkier_every_time_they_hack")
    return "tryit-" + r + "-" + _h(ua + _h(ua + inner))

# ---------------- multipart ----------------
def _multipart(fields):
    boundary = "----deepaiproxy" + uuid.uuid4().hex
    body = "".join(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n" for k, v in fields.items()).encode() + f"--{boundary}--\r\n".encode()
    return boundary, body

def _build_multipart(parts):
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

def _parse_multipart(body_bytes, content_type):
    fields, files = {}, {}
    m = re.search(r"boundary=([^;]+)", content_type or "")
    if not m:
        return fields, files
    boundary = ("--" + m.group(1).strip().strip('"')).encode()
    for part in body_bytes.split(boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        data = data.rstrip(b"\r\n")
        head = head.decode("utf-8", "replace")
        nm = re.search(r'name="([^"]+)"', head)
        if not nm:
            continue
        name = nm.group(1)
        fn = re.search(r'filename="([^"]*)"', head)
        ct = re.search(r"Content-Type: ([^\r\n]+)", head)
        if fn and fn.group(1):
            ctype = ct.group(1).strip() if ct else "application/octet-stream"
            files[name] = (fn.group(1), ctype, data)
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files

def _image_parts(images):
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

# Native DeepAI "thinking" (web-UI parity) requires a logged-in session cookie,
# because the web UI sends credentials:'include'. Set DEEPAI_COOKIE to your
# deepai.org session cookie (from browser devtools) to enable real thinking.
DEEPAI_COOKIE = os.environ.get("DEEPAI_COOKIE", "")
DEEPAI_TASK = "https://api.deepai.org/check_chat_task_status"
# Models that expose DeepAI's native thinking task flow (from the web UI source).
THINKING_MODELS = {
    "supergenius", "o4-mini", "o3", "gpt-oss-120b", "gemini-3-pro-preview",
    "claude-opus-5", "claude-fable-5", "grok-4.3", "gpt-5.2", "gpt-5.6-sol",
    "gpt-5", "grok-3-mini", "deepseek-reasoner", "deepseek-r1-distill-llama-70b",
    "qwen3-235b-a22b-thinking",
}

def _build_chat_parts(model, messages, images, files, thinking):
    parts = [
        ("text", "chat_style", "chat"),
        ("text", "chat_model" if thinking else "model", model),
        ("text", "chatHistory", json.dumps(messages)),
        ("text", "session_uuid", str(uuid.uuid4())),
        ("text", "tool_activity_support", "1"),
        ("text", "enabled_tools", json.dumps(["image_generator", "image_editor"])),
    ]
    if thinking:
        parts.append(("text", "thinking_support", "1"))
    else:
        parts.append(("text", "hacker_is_stinky", "very_stinky"))
    for p in _image_parts(images or []):
        parts.append(p)
    if files:
        fp = _file_part(files[0])
        if fp:
            parts.append(fp)
    return parts

def _think_stream(model, messages, images=None, files=None):
    # Native DeepAI thinking: POST -> task_id, then poll thinking_text/answer_text.
    try:
        parts = _build_chat_parts(model, messages, images, files, True)
        boundary, body = _build_multipart(parts)
        req = urllib.request.Request(DEEPAI_CHAT, data=body, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": UA, "api-key": make_tryit_key(), "Cookie": DEEPAI_COOKIE,
        }, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except Exception:
        yield from stream_chat(model, messages, images, files, thinking=False)
        return
    try:
        td = json.loads(raw)
    except Exception:
        if raw.strip():
            yield {"reasoning": "", "content": raw}
        return
    task_id = td.get("task_id")
    if not task_id:
        if raw.strip():
            yield {"reasoning": "", "content": raw}
        return
    last_t, last_a = "", ""
    for _ in range(150):
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(
                f"{DEEPAI_TASK}?type=thinking-task&task_id={task_id}",
                headers={"User-Agent": UA, "api-key": make_tryit_key(), "Cookie": DEEPAI_COOKIE},
            ), timeout=30).read().decode("utf-8", "replace"))
        except Exception:
            break
        t = d.get("thinking_text") or ""
        if len(t) > len(last_t):
            yield {"reasoning": t[len(last_t):], "content": ""}
            last_t = t
        a = d.get("answer_text") or ""
        if len(a) > len(last_a):
            yield {"reasoning": "", "content": a[len(last_a):]}
            last_a = a
        if a or d.get("status") in ("COMPLETED", "FAILED"):
            break
        time.sleep(1.0)

def stream_chat(model, messages, images=None, files=None, thinking=False):
    if thinking and DEEPAI_COOKIE and model in THINKING_MODELS:
        yield from _think_stream(model, messages, images, files)
        return
    chat_history = json.dumps(messages)
    parts = _build_chat_parts(model, messages, images, files, False)
    had_attach = bool(images or files)
    boundary, body = _build_multipart(parts)
    req = urllib.request.Request(DEEPAI_CHAT, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": UA}, method="POST")
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
                    yield {"reasoning": "", "content": visible}
    except urllib.error.HTTPError:
        if had_attach:
            yield from stream_chat(model, messages, images, files, thinking=False)
            return
        raise

def chat_once(model, messages, images=None, files=None, thinking=False):
    return "".join(d["content"] for d in stream_chat(model, messages, images, files, thinking))

def chat_full(model, messages, images=None, files=None, thinking=False):
    r, c = [], []
    for d in stream_chat(model, messages, images, files, thinking):
        if d["reasoning"]:
            r.append(d["reasoning"])
        if d["content"]:
            c.append(d["content"])
    return "".join(r), "".join(c)

# ---------------- reasoning (professional-provider parity) ----------------
# DeepAI's free chat models do not expose a separate reasoning channel, so we
# (a) surface any native <think>...</think> blocks as OpenAI-style
#     reasoning_content, and (b) offer a two-pass ":reason" mode that asks the
#     model for a plan first, then the answer, so the plan is visible as
#     reasoning_content (matching providers like the test hcnsec.cn endpoint).
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
def _extract_reasoning(text):
    blocks = [b.strip() for b in _THINK_RE.findall(text)]
    reasoning = "\n\n".join(b for b in blocks if b).strip()
    content = _THINK_RE.sub("", text).strip()
    return reasoning, content

def _split_reason_model(model):
    if model.endswith(":reason"):
        return model[:-6], True
    return model, False

def _reasoned_answer(model, messages, images=None, files=None):
    plan_msgs = [{"role": "system",
                  "content": "You are a meticulous senior engineer. Output a concise step-by-step plan "
                             "(reasoning only). Do NOT write the final deliverable/code yet."}] + list(messages)
    plan = chat_once(model, plan_msgs, images, files)
    exec_msgs = list(messages) + [
        {"role": "assistant", "content": "Plan:\n" + plan},
        {"role": "user", "content": "Now execute that plan and provide the final answer / deliverable."},
    ]
    answer = chat_once(model, exec_msgs, images, files)
    return plan.strip(), answer.strip()

def _stream_reasoned(model, messages, images=None, files=None):
    plan_msgs = [{"role": "system",
                  "content": "You are a meticulous senior engineer. Output a concise step-by-step plan "
                             "(reasoning only). Do NOT write the final deliverable/code yet."}] + list(messages)
    plan = []
    for d in stream_chat(model, plan_msgs, images, files):
        plan.append(d["content"])
        yield {"reasoning": d["content"], "content": ""}
    exec_msgs = list(messages) + [
        {"role": "assistant", "content": "Plan:\n" + "".join(plan)},
        {"role": "user", "content": "Now execute that plan and provide the final answer / deliverable."},
    ]
    for d in stream_chat(model, exec_msgs, images, files):
        yield {"reasoning": "", "content": d["content"]}

# ---------------- DeepAI images ----------------
def call_image(endpoint, fields):
    cookie = _img_cookie()
    if not cookie:
        return None, "Image generation needs DEEPAI_DEVICE_ID (server env)."
    boundary, body = _multipart(fields)
    req = urllib.request.Request(DEEPAI_IMG + "/" + endpoint, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": UA, "Cookie": cookie, "api-key": make_tryit_key()}, method="POST")
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
    payload = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"img.png\"\r\nContent-Type: image/png\r\n\r\n").encode() + blob_bytes + f"\r\n--{boundary}\r\n".encode()
    for k, v in fields.items():
        payload += f"Content-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    payload += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(DEEPAI_IMG + "/image-editor", data=payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": UA, "Cookie": cookie, "api-key": make_tryit_key()}, method="POST")
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

# ---------------- API keys (admin / public) ----------------
# Vercel serverless uses a read-only, ephemeral filesystem, so keys written to
# disk do NOT persist across requests/instances. Validation is therefore
# stateless: the canonical keys are the ones supplied via env
# (SEED_API_KEY / PUBLIC_API_KEY), which are identical on every instance.
_RATE = {}
def _ratelimit(ip, limit=40, window=600):
    now = time.time()
    hits = _RATE.get(ip, [])
    hits = [t for t in hits if now - t < window]
    if len(hits) >= limit:
        return False
    hits.append(now)
    _RATE[ip] = hits
    return True

def _admin_key():
    return os.environ.get("SEED_API_KEY") or (KEY_PREFIX + "admin" + os.urandom(8).hex())

def _public_key():
    return os.environ.get("PUBLIC_API_KEY") or (KEY_PREFIX + "public" + os.urandom(8).hex())

def gen_key(role="public", name="key"):
    return _admin_key() if role == "admin" else _public_key()

def list_keys():
    return [
        {"key": _admin_key(), "name": "admin", "role": "admin", "created": 0},
        {"key": _public_key(), "name": "public", "role": "public", "created": 0},
    ]

def role_of(auth_header):
    if not auth_header:
        return None
    t = auth_header.replace("Bearer ", "").replace("bearer ", "")
    if t == _admin_key():
        return "admin"
    if t == _public_key():
        return "public"
    return None

def valid_key(auth_header):
    return role_of(auth_header) is not None

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
    # The OpenAI SDK builds URLs by concatenating baseURL + path, so a trailing-slash
    # base URL (e.g. ".../api/v1/") produces ".../api/v1//models" (double slash).
    # Collapse repeated slashes so these still resolve.
    raw = re.sub(r"/{2,}", "/", raw)
    is_v1 = raw.startswith("/api/v1")
    path = raw
    if is_v1:
        path = "/api" + raw[len("/api/v1"):]

    # Accept OpenAI/n8n-style URLs that may omit the /api prefix
    # (e.g. /models, /v1/models, /chat/completions, /images/generations, ...).
    if not path.startswith("/api/"):
        if path.startswith("/v1/"):
            path = "/api" + path
            is_v1 = True
        elif re.match(r"^(/models|/chat|/responses|/images|/keys)(/|$)", path):
            path = "/api/v1" + path
            is_v1 = True

    def respond(status, resp_headers, body):
        h = dict(resp_headers); h.update(CORS)
        return status, h, body

    if method == "OPTIONS":
        return respond(204, {}, b"")
    if not path.startswith("/api/"):
        return respond(*_serve_static(path))

    # models (public)
    if path == "/api/models" and method == "GET":
        if is_v1:
            # Minimal canonical OpenAI /v1/models shape (matches NVIDIA/OpenAI so
            # generic OpenAI clients / n8n load models dynamically).
            data = [{"id": m["id"], "object": "model", "created": 1700000000, "owned_by": "deepai"}
                    for cat in load_models()["categories"].values() for m in cat["models"]]
            return respond(200, {"Content-Type": "application/json"}, json.dumps({"object": "list", "data": data}).encode())
        return respond(200, {"Content-Type": "application/json"}, json.dumps(load_models()).encode())

    # rich models (our UI) — OpenRouter-style extended metadata
    if path == "/api/models/full" and method == "GET":
        if is_v1:
            return respond(200, {"Content-Type": "application/json"}, json.dumps(openrouter_models()).encode())
        return respond(200, {"Content-Type": "application/json"}, json.dumps(load_models()).encode())

    # tryit key (public) — used by the browser to call DeepAI image APIs directly
    if path == "/api/tryit" and method == "GET":
        ua = headers.get("X-Tryit-Ua") or UA
        return respond(200, {"Content-Type": "application/json"}, json.dumps({"api_key": make_tryit_key(ua)}).encode())

    # public key for the website / API section (never the admin key)
    if path == "/api/keys/public" and method == "GET":
        return respond(200, {"Content-Type": "application/json"},
                       json.dumps({"key": _public_key(), "role": "public"}).encode())

    # keys
    if path == "/api/keys":
        if method == "GET":
            if role_of(headers.get("Authorization", "")) != "admin":
                return respond(403, {"Content-Type": "application/json"}, json.dumps({"error": "admin key required"}).encode())
            return respond(200, {"Content-Type": "application/json"}, json.dumps({"object": "list", "data": list_keys()}).encode())
        if method == "POST":
            # light rate limit (per IP) to deter key farming
            ip = (headers.get("X-Forwarded-For") or headers.get("X-Vercel-Forwarded-For") or "").split(",")[0].strip()
            if not _ratelimit(ip):
                return respond(429, {"Content-Type": "application/json"}, json.dumps({"error": "rate limited, try later"}).encode())
            body = _json_body(body_bytes)
            role = "public"
            if role_of(headers.get("Authorization", "")) == "admin" and body.get("role") == "admin":
                role = "admin"
            k = gen_key(role, (body.get("name") or "key"))
            return respond(200, {"Content-Type": "application/json"}, json.dumps({"key": k, "role": role}).encode())
        return respond(405, {}, b"")

    # auth required for everything below
    if role_of(headers.get("Authorization", "")) is None:
        return respond(401, {"Content-Type": "application/json"},
                       json.dumps({"error": "Invalid or missing API key. Use: Authorization: Bearer <key>"}).encode())

    # chat completions (multimodal)
    if path in ("/api/chat", "/api/chat/completions") and method == "POST":
        body = _json_body(body_bytes)
        model = body.get("model", "deepseek-v3.2")
        real_model, want_reason = _split_reason_model(model)
        messages = _messages_from(body)
        images = body.get("images") or None
        files = body.get("files") or None
        if body.get("stream"):
            def gen():
                yield b"data: " + json.dumps({"id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion.chunk",
                                              "model": model, "choices": [{"index": 0, "delta": {"role": "assistant"}}]}).encode() + b"\n\n"
                if DEEPAI_COOKIE and real_model in THINKING_MODELS:
                    for d in stream_chat(real_model, messages, images, files, thinking=True):
                        yield b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": d["content"], "reasoning_content": d["reasoning"]}}]}).encode() + b"\n\n"
                elif want_reason:
                    for d in _stream_reasoned(real_model, messages, images, files):
                        yield b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": d["content"], "reasoning_content": d["reasoning"]}}]}).encode() + b"\n\n"
                else:
                    for d in stream_chat(real_model, messages, images, files):
                        yield b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": d["content"], "reasoning_content": ""}}]}).encode() + b"\n\n"
                yield b"data: [DONE]\n\n"
            return respond(200, {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}, gen())
        if DEEPAI_COOKIE and real_model in THINKING_MODELS:
            reasoning, content = chat_full(real_model, messages, images, files, thinking=True)
        elif want_reason:
            reasoning, content = _reasoned_answer(real_model, messages, images, files)
        else:
            text = chat_once(real_model, messages, images, files)
            reasoning, content = _extract_reasoning(text)
        return respond(200, {"Content-Type": "application/json"}, json.dumps({
            "id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content, "reasoning_content": reasoning}, "finish_reason": "stop"}]
        }).encode())

    # responses (Codex)
    if path == "/api/responses" and method == "POST":
        body = _json_body(body_bytes)
        model = body.get("model", "deepseek-v3.2")
        real_model, want_reason = _split_reason_model(model)
        messages = _messages_from(body)
        images = body.get("images") or None
        files = body.get("files") or None
        rid = "resp_" + uuid.uuid4().hex[:24]
        if body.get("stream"):
            iid = "msg_" + uuid.uuid4().hex[:12]
            def gen():
                yield _sse("response.created", {"id": rid, "object": "response", "status": "in_progress"})
                yield _sse("response.output_item.added", {"item": {"id": iid, "type": "message", "role": "assistant", "content": []}})
                if DEEPAI_COOKIE and real_model in THINKING_MODELS:
                    for d in stream_chat(real_model, messages, images, files, thinking=True):
                        if d["reasoning"]:
                            yield _sse("response.output_text.delta", {"item_id": iid, "delta": "", "reasoning": d["reasoning"]})
                        else:
                            yield _sse("response.output_text.delta", {"item_id": iid, "delta": d["content"]})
                elif want_reason:
                    for d in _stream_reasoned(real_model, messages, images, files):
                        if d["reasoning"]:
                            yield _sse("response.output_text.delta", {"item_id": iid, "delta": "", "reasoning": d["reasoning"]})
                        else:
                            yield _sse("response.output_text.delta", {"item_id": iid, "delta": d["content"]})
                else:
                    for d in stream_chat(real_model, messages, images, files):
                        yield _sse("response.output_text.delta", {"item_id": iid, "delta": d["content"]})
                yield _sse("response.output_text.done", {"item_id": iid})
                yield _sse("response.output_item.done", {"item": {"id": iid, "type": "message", "role": "assistant"}})
                yield _sse("response.completed", {"id": rid, "status": "completed"})
            return respond(200, {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}, gen())
        if DEEPAI_COOKIE and real_model in THINKING_MODELS:
            reasoning, content = chat_full(real_model, messages, images, files, thinking=True)
        elif want_reason:
            reasoning, content = _reasoned_answer(real_model, messages, images, files)
        else:
            text = chat_once(real_model, messages, images, files)
            reasoning, content = _extract_reasoning(text)
        return respond(200, {"Content-Type": "application/json"}, json.dumps({
            "id": rid, "object": "response", "model": model,
            "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": content, "reasoning_content": reasoning}]}], "status": "completed"
        }).encode())

    # images
    if path in ("/api/images/generations", "/api/images/edits") and method == "POST":
        is_edit = path.endswith("edits")
        ct = headers.get("Content-Type", "")
        prompt, size, image = "", "640x640", None
        if ct.startswith("multipart"):
            fields, files = _parse_multipart(body_bytes, ct)
            prompt = fields.get("prompt") or fields.get("text", "")
            size = fields.get("size", "640x640")
            if is_edit and "image" in files:
                fn, fct, blob = files["image"]
                image = "data:" + fct + ";base64," + base64.b64encode(blob).decode()
        else:
            b = _json_body(body_bytes)
            prompt = b.get("prompt", "")
            size = b.get("size", "640x640")
            image = b.get("image")
        if is_edit:
            if not image:
                return respond(400, {"Content-Type": "application/json"}, json.dumps({"error": "image required for edit"}).encode())
            w, h = _size_to_wh(size)
            fields = {"text": prompt, "generation_source": "img", "width": str(w), "height": str(h), "image_generator_version": "hd", "quality": "true"}
            if image.startswith("data:"):
                res, err = call_image_edit_with_blob(base64.b64decode(image.split(",", 1)[1]), fields)
            else:
                fields["image"] = image
                res, err = call_image("image-editor", fields)
        else:
            w, h = _size_to_wh(size)
            fields = {"text": prompt, "generation_source": "img", "width": str(w), "height": str(h),
                      "image_generator_version": "hd", "use_new_model": "true", "use_old_model": "false", "quality": "true"}
            res, err = call_image("text2img", fields)
        if err:
            return respond(500, {"Content-Type": "application/json"}, json.dumps({"error": err}).encode())
        url = (res or {}).get("output_url") or (res or {}).get("share_url")
        return respond(200, {"Content-Type": "application/json"}, json.dumps({"created": int(time.time()), "data": [{"url": url}]}).encode())

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
