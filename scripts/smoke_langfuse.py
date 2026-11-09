# -*- coding: utf-8 -*-
"""Langfuse Cloud connectivity smoke (V2-7).

Does **not** run the Graph. Verifies ``LANGFUSE_PUBLIC_KEY`` /
``LANGFUSE_SECRET_KEY`` can create a short trace and flush.

Usage::

    python scripts/smoke_langfuse.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> int:
    from data_reflex.config.settings import Settings
    from data_reflex.utils.tracing import (
        create_tracer,
        is_langfuse_configured,
        reset_tracer,
    )

    settings = Settings()
    if not is_langfuse_configured(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
    ):
        sys.stdout.write(
            "skip: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY empty "
            "(tracing stays no-op).\n"
        )
        return 0

    tracer = create_tracer(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    if not tracer.enabled:
        sys.stderr.write("fail: tracer disabled after client init\n")
        return 1

    try:
        with tracer.trace(
            "smoke_langfuse",
            metadata={"source": "scripts/smoke_langfuse.py"},
            input={"ping": True},
        ) as root:
            with tracer.span("smoke_child") as child:
                child.update(output={"ok": True})
            root.update(output={"ok": True})
        tracer.flush()
    finally:
        tracer.shutdown()
        reset_tracer(None)

    host = settings.langfuse_host.strip() or "(Langfuse Cloud default)"
    sys.stdout.write(f"ok langfuse host={host}\n")
    sys.stdout.write("check cloud UI for trace name=smoke_langfuse\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
