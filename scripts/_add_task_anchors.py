# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "docs" / "tasks.md"
text = path.read_text(encoding="utf-8")

# Strip existing Txx anchors we may have added to avoid duplicates.
text = re.sub(r'(?m)^<a id="T\d+"></a>\n(?=- \[[ x]\])', "", text)


def repl(match: re.Match[str]) -> str:
    tid = match.group(2)
    return f'<a id="{tid}"></a>\n{match.group(1)}'


new, count = re.subn(
    r"(?m)^(- \[[ x]\] \*\*(T\d+)：)",
    repl,
    text,
)
path.write_text(new, encoding="utf-8")
print(f"added {count} task anchors")
