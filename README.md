# DeepAI Proxy

An OpenAI-compatible proxy + clean web UI for **DeepAI's free models**: chat (10+ free
models + live web search) and image generation (text-to-image & image-to-image). No API
key required on the DeepAI side — chat uses DeepAI's anonymous endpoint; images use your
own `deepai_device_id` cookie.

## Features

- **Chat** — any free DeepAI model, streaming, OpenAI-compatible.
- **Web search** — the `online` model returns live answers with cited sources.
- **Images** — text-to-image (`text2img`) and image-to-image (`image-editor`).
- **Models** — full registry (`models.json`) with a one-click "Test all" health check.
- **API keys** — generate keys for CLI use (`Authorization: Bearer <key>`).
- **Deployable** — local (stdlib only) or Vercel (Python runtime). No dependencies.

## Local run

```bash
cd deepai-proxy
# optional: enable images
export DEEPAI_DEVICE_ID=<your deepai cookie id>
python3 server.py 8000
# open http://127.0.0.1:8000
```

The site auto-generates an API key on first load and stores it in `localStorage`.

## API

OpenAI / OpenRouter compatible. Base URL: `https://<your-app>/api/v1`. API keys use the
`tnxbd-` prefix. All routes need `Authorization: Bearer <key>` except `GET /api/v1/models`.

| Method | Path | Body |
|--------|------|------|
| POST | `/api/v1/chat/completions` | OpenAI chat completions (`{model, messages, stream}`) |
| POST | `/api/v1/responses` | Codex Responses API (`{model, input, stream}`) |
| GET  | `/api/v1/models` | OpenRouter-style model list |
| POST | `/api/v1/images/generations` | `{prompt, size}` |
| POST | `/api/v1/images/edits` | `{prompt, image(url or dataURL), size}` |
| POST | `/api/v1/keys` | `{name}` → new key |

(Legacy `/api/*` aliases still work.)

### CLI config

- **OpenCode** (`opencode.json`): OpenAI-compatible provider with
  `baseURL: https://<your-app>/api/v1` and `apiKey: tnxbd-…`.
- **Codex** (`config.toml`): `model_providers` with `base_url = "https://<your-app>/api/v1"`
  and `wire_api = "responses"`, then use model `standard`. (Modern Codex expects the
  Responses API; this proxy serves both `/api/v1/chat/completions` and `/api/v1/responses`.)
- **Claude Code**: needs an Anthropic→OpenAI adapter (e.g. CC-Adapter) — it speaks the
  Anthropic Messages API, which this proxy does not implement.

## Deploy to Vercel

```bash
vercel env add SEED_API_KEY        # a tnxbd-… secret you choose
vercel env add DEEPAI_DEVICE_ID    # your deepai cookie id
vercel deploy
```

`vercel.json` is `{}` (Vercel defaults): `public/` is served as static, `api/index.py`
is the Python serverless function. No build step, no dependencies.

## Limitations

- Image generation uses DeepAI's **anonymous** quota, bound to `DEEPAI_DEVICE_ID`. When
  it returns `401: "Please try this model on deepai.org"`, refresh the id.
- Free chat models have limited context (most 8K–32K; `deepseek-v3.2` ~128K) and an
  anonymous rate quota — a 50K–500K-token job will be rejected by DeepAI.
- Key storage is best-effort (filesystem). On serverless, set `SEED_API_KEY` for durable
  CLI auth; the web UI keeps its key in `localStorage`.
