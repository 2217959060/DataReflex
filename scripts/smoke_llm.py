# -*- coding: utf-8 -*-
"""GLM / OpenAI-compatible API smoke test (v1.5 R3).

Does **not** invoke the LangGraph pipeline. Verifies ``LLM_API_KEY`` /
``LLM_BASE_URL`` / ``LLM_MODEL`` can complete one chat round-trip.

Success = HTTP 200 + a ``choices[0]`` message object (content may be empty
for some models / short max_tokens). Usage::

    python scripts/smoke_llm.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> int:
    # Import after sys.path bootstrap (same pattern as eval/run_eval.py).
    from data_reflex.config.settings import Settings  # noqa: E402

    settings = Settings()
    if not settings.llm_api_key.strip():
        sys.stderr.write(
            "LLM_API_KEY is empty. Copy .env.example to .env and set the key.\n"
        )
        return 2

    base = settings.llm_base_url.rstrip("/")
    url = f"{base}/chat/completions"
    payload = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "messages": [
            {"role": "user", "content": "Reply with exactly one word: pong"},
        ],
        "max_tokens": 64,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.llm_api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        sys.stderr.write(f"HTTP {exc.code}: {detail}\n")
        return 1
    except urllib.error.URLError as exc:
        sys.stderr.write(f"URL error: {exc}\n")
        return 1

    try:
        choice = body["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError):
        sys.stderr.write(f"unexpected response: {body!r}\n")
        return 1

    content = message.get("content")
    if content is None:
        content = message.get("reasoning_content") or ""
    finish = choice.get("finish_reason", "")

    sys.stdout.write(f"ok model={settings.llm_model}\n")
    sys.stdout.write(f"finish_reason={finish!r}\n")
    sys.stdout.write(f"reply={content!r}\n")
    if not str(content).strip():
        sys.stdout.write(
            "note: empty reply still counts as connectivity success "
            "(HTTP OK + choices present)\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
