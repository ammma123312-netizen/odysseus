# Bundled Local LLM (Free CPU-only)

Odysseus ships with optional integration for **llama-cpp-python**, so the
Hugging Face Space (or any plain Docker host) can serve open-source GGUF
models on CPU without any external API key. Disabled by default — opt in
with `ODYSSEUS_LOCAL_LLM=1`.

## Why

- HF Spaces' free `cpu-basic` tier gives 2 vCPU + 16 GB RAM. Enough to
  serve a quantized 3B-class model alongside the Odysseus UI.
- No OpenAI/Anthropic/OpenRouter key required for the default chat
  experience.
- The local model speaks the OpenAI `/v1/chat/completions` protocol, so
  Odysseus' existing model discovery picks it up automatically — just
  point `LLM_HOSTS` at `localhost:8080`.

## Defaults

| Setting | Value | Why |
|--------|-------|-----|
| Model repo | `Qwen/Qwen2.5-3B-Instruct-GGUF` | Strong 3B; Apache 2.0; fluent Arabic + English |
| File | `qwen2.5-3b-instruct-q4_k_m.gguf` | ~2 GB on disk, ~4 GB RAM at runtime |
| Port | `8080` (bound to `127.0.0.1`) | Internal only — public traffic still goes through Odysseus on `:7000` |
| Context | `4096` tokens | Safe for cpu-basic; raise if you have headroom |
| Threads | `min(nproc, 4)` | Leaves CPU for uvicorn |

Override any of them via env vars (see `scripts/local_llm/serve.sh`).

## How to enable on the HF Space

1. Open **Settings → Variables and secrets** on your Space.
2. Add these **Variables** (not secrets — they're not sensitive):

   | Key | Value |
   |-----|-------|
   | `ODYSSEUS_LOCAL_LLM` | `1` |
   | `LLM_HOSTS` | `localhost:8080` |

3. Restart the Space (`Settings → Factory rebuild` if the previous image
   doesn't have `llama-cpp-python` yet — the first build after enabling
   this also has to compile llama.cpp, which takes ~3–5 min on
   cpu-basic).
4. After boot, the Space logs should show:
   ```
   [entrypoint] starting bundled local LLM server (default: Qwen2.5-3B-Instruct GGUF)...
   [local-llm] downloading Qwen/Qwen2.5-3B-Instruct-GGUF / qwen2.5-3b-instruct-q4_k_m.gguf -> /app/models/...
   [local-llm] launching llama-cpp-python OpenAI server on :8080
   ```

## Stronger models via free external APIs (recommended fallback)

For tasks that need more than a 3B model can deliver, configure Odysseus
to also talk to a free hosted endpoint. None of these require leaving
the free tier:

| Provider | Free quota | Models | Endpoint shape |
|----------|------------|--------|----------------|
| **Groq** | Generous free tier, no card | Llama-3.3-70B-Versatile, Llama-3.1-8B, DeepSeek-R1-Distill-Llama-70B | `https://api.groq.com/openai/v1` |
| **Cerebras** | Free tier, fast TTFT | Llama-3.3-70B, Llama-3.1-8B, Qwen-3-32B | `https://api.cerebras.ai/v1` |
| **OpenRouter** | Free-tier models with `:free` suffix | DeepSeek-V3-0324:free, Llama-3.3-70B:free | `https://openrouter.ai/api/v1` |

Set an `OPENAI_API_KEY` per provider in **Settings → Secrets** on the
Space, then add the host to `LLM_HOSTS` (comma-separated) — Odysseus'
existing model picker will surface every available model side-by-side.

## Self-hosted "stronger" backup (Modal.com)

If you want a genuinely self-hosted bigger model (Qwen2.5-32B,
Llama-3-70B, etc.) and not just a hosted API, Modal.com's $30/month
free credit covers a few hours/day of A10G time. A drop-in starter
that serves a Modal endpoint speaking the same OpenAI protocol lives in
`docs/modal_llm_example.md` (TODO — open an issue if you want this
prioritized).

## Cost summary

| Layer | Cost |
|------|------|
| HF Space cpu-basic + bundled Qwen2.5-3B | **$0/mo** |
| Groq / Cerebras / OpenRouter free models for harder queries | **$0/mo** (rate-limited) |
| Modal.com optional Qwen-32B / Llama-70B fallback | **$0/mo** (within $30 credit) |
| GitHub Actions for HF auto-deploy | **$0/mo** (~20 min/month, free tier is 2000 min) |
| **Total** | **$0/mo** |
