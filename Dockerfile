FROM python:3.12-slim

# System deps. tmux is required by Cookbook for background downloads/serves.
# openssh-client is required for Cookbook remote server tests, setup, probes,
# downloads, and serves from Docker installs.
# git/cmake are required when Cookbook builds llama.cpp on first llama.cpp
# launch inside Docker.
# nodejs/npm provide npx for the optional built-in Browser MCP server.
# gosu lets the entrypoint drop privileges cleanly so signals still reach
# uvicorn directly (no extra shell layer like `su`/`sudo` would add).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    curl \
    git \
    nodejs \
    npm \
    tmux \
    openssh-client \
    gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache). Optional extras (PyMuPDF AGPL, etc.)
# are opt-in so the default image stays MIT-core; see requirements-optional.txt.
ARG INSTALL_OPTIONAL=false
COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_OPTIONAL" = "true" ]; then pip install --no-cache-dir -r requirements-optional.txt; fi

# Install MCP NPX servers GLOBALLY so they're available to every user
# regardless of $HOME. Background: npx caches per-user under ~/.npm, but
# the entrypoint drops from root → PUID 1000 (HOME=/app), so anything we
# pre-warmed in /root/.npm during build is invisible at runtime. Putting
# the packages in /usr/lib/node_modules with `npm install -g` makes them
# system-wide, so `_is_npx_package_cached()` finds them no matter which
# UID the app runs as. Failures stay non-fatal so the image always builds.
RUN npm install -g \
        @modelcontextprotocol/server-sequential-thinking \
        @modelcontextprotocol/server-filesystem \
        @playwright/mcp@latest \
        2>/dev/null || true

# Copy app code
COPY . .

# Create data directory (mount a volume here for persistence)
RUN mkdir -p data logs services/cache/search

# Pre-create the bundled-LLM cache directory. The actual GGUF download
# happens at runtime in scripts/local_llm/serve.sh (gated by
# ODYSSEUS_LOCAL_LLM=1) so the image stays slim and we don't bake a
# multi-GB model layer into every build. On HF Spaces the same
# persistence watcher that snapshots /app/data can also be pointed at
# this dir if you want the GGUF to survive Space restarts without
# re-downloading.
RUN mkdir -p /app/models

# Entrypoint that drops to PUID/PGID (default 1000:1000) and repairs
# ownership on the bind-mounted /app/data and /app/logs. Without this,
# the container runs as root and writes root-owned files into host
# bind mounts — any later non-root run (or a host user trying to
# update them) silently fails on EPERM, breaking skill extraction,
# prefs persistence, mail attachments, etc.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 7000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
