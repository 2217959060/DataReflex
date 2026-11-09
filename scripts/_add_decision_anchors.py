# -*- coding: utf-8 -*-
"""Insert <a id="D-xxx"></a> above each 【实现决策 D-xxx】 block in tasks.md."""
from __future__ import annotations

import re
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "docs" / "tasks.md"
text = path.read_text(encoding="utf-8")

# Remove prior broken or correct decision anchors (keep Txx intact).
text = re.sub(
    r'(?m)^<a id="(?:  - > \*\*【实现决策 )?(D-[A-Z0-9-]+)(?:｜)?"></a>\n',
    "",
    text,
)


def repl(match: re.Match[str]) -> str:
    did = match.group(1)
    return f'<a id="{did}"></a>\n{match.group(0)}'


new, count = re.subn(
    r"(?m)^  - > \*\*【实现决策 (D-[A-Z0-9-]+)｜",
    repl,
    text,
)
path.write_text(new, encoding="utf-8")
print(f"added {count} decision anchors")
