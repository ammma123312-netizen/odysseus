#!/usr/bin/env python3
"""Seed the bundled local LLM as Odysseus' default model on first start.

Adds a `ModelEndpoint` row pointing at the in-container llama-cpp-python
server (127.0.0.1:8001/v1), and writes `data/settings.json` so the
default chat model is the bundled Qwen2.5-3B alias.

Idempotent — only runs when:
  * ODYSSEUS_LOCAL_LLM=1 (the bundled server is actually enabled), and
  * no endpoint with the same base_url already exists.

Re-running this script after the admin manually picks a different
default is also safe: we only fill `default_model` when it's empty.

Wired into docker/entrypoint.sh AFTER setup.py (which creates the DB)
and BEFORE uvicorn starts (so the first page load already sees the
endpoint registered).
"""

from __future__ import annotations

import json
import os
import sys
import uuid

# Make sure /app is on the path the same way setup.py does it.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

# Match the DB location setup.py uses, so we hit the SAME sqlite file.
DATA_DIR = os.path.join(BASE_DIR, "data")
os.environ.setdefault(
    "DATABASE_URL", f"sqlite:///{os.path.join(DATA_DIR, 'app.db')}"
)

SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

# Has to match the alias we pass to llama_cpp.server (see serve.sh).
LOCAL_MODEL_ALIAS = os.getenv("LOCAL_LLM_ALIAS", "qwen2.5-3b-instruct")
LOCAL_PORT = os.getenv("ODYSSEUS_LOCAL_LLM_PORT", "8001")
LOCAL_BASE_URL = f"http://127.0.0.1:{LOCAL_PORT}/v1"
ENDPOINT_NAME = "Bundled Local (Qwen2.5-3B)"


def main() -> int:
    if os.getenv("ODYSSEUS_LOCAL_LLM", "0") != "1":
        # Local LLM is off — nothing to seed.
        return 0

    try:
        from core.database import Base, SessionLocal, engine, ModelEndpoint
    except Exception as exc:
        print(f"[seed-local-llm] DB import failed: {exc}", file=sys.stderr)
        return 0

    # Make sure tables exist (setup.py also does this — idempotent).
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        existing = (
            db.query(ModelEndpoint)
            .filter(ModelEndpoint.base_url == LOCAL_BASE_URL)
            .one_or_none()
        )
        if existing is None:
            endpoint = ModelEndpoint(
                id=str(uuid.uuid4()),
                name=ENDPOINT_NAME,
                base_url=LOCAL_BASE_URL,
                api_key=None,
                is_enabled=True,
                # Pin the alias so the picker shows it even before the first
                # /v1/models probe lands. discovery will refresh this with
                # the real list once llama-cpp-python is up.
                pinned_models=json.dumps([LOCAL_MODEL_ALIAS]),
                cached_models=json.dumps([LOCAL_MODEL_ALIAS]),
                model_type="llm",
                endpoint_kind="local",
                model_refresh_mode="auto",
                # llama-cpp-python's OpenAI server supports OpenAI tool calls.
                supports_tools=True,
                owner=None,  # shared, visible to every user
            )
            db.add(endpoint)
            db.commit()
            print(
                f"[seed-local-llm] registered endpoint '{ENDPOINT_NAME}' "
                f"at {LOCAL_BASE_URL} (model={LOCAL_MODEL_ALIAS})"
            )
            endpoint_id = endpoint.id
        else:
            endpoint_id = existing.id
            print(
                f"[seed-local-llm] endpoint already present "
                f"(id={endpoint_id}); leaving as-is"
            )
    finally:
        db.close()

    # ── Make it the default in data/settings.json ─────────────────────
    # We only fill empty slots — never overwrite a user-set default. Keeps
    # this script safe to call on every container start.
    try:
        if os.path.exists(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                settings = json.load(f) or {}
        else:
            settings = {}
    except json.JSONDecodeError:
        # Corrupt file — leave it alone, don't make things worse.
        print(
            "[seed-local-llm] settings.json is unreadable; "
            "skipping default-model write",
            file=sys.stderr,
        )
        return 0

    changed = False
    if not settings.get("default_model"):
        settings["default_model"] = LOCAL_MODEL_ALIAS
        changed = True
    if not settings.get("default_endpoint_id"):
        settings["default_endpoint_id"] = endpoint_id
        changed = True
    # Mirror to utility/task so background features (naming, summarization)
    # also land on the local model instead of failing with "no model".
    if not settings.get("utility_model"):
        settings["utility_model"] = LOCAL_MODEL_ALIAS
        settings["utility_endpoint_id"] = endpoint_id
        changed = True
    if not settings.get("task_model"):
        settings["task_model"] = LOCAL_MODEL_ALIAS
        settings["task_endpoint_id"] = endpoint_id
        changed = True

    if changed:
        os.makedirs(DATA_DIR, exist_ok=True)
        # Atomic write so a crash mid-write can't corrupt settings.json.
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SETTINGS_PATH)
        print(
            f"[seed-local-llm] set default_model={LOCAL_MODEL_ALIAS} "
            f"+ endpoint_id={endpoint_id} in settings.json"
        )
    else:
        print("[seed-local-llm] default model already configured; no-op")

    return 0


if __name__ == "__main__":
    sys.exit(main())
