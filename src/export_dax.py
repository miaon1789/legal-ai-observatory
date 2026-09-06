"""Extract the measure definitions from docs/DAX_MEASURES.md into powerbi/measures.dax.

The documentation is the source of truth: it carries each measure alongside the
value it must return. This file is the same definitions stripped to something
that can be pasted into Power BI one measure at a time, and it means the
repository holds the model's logic even without the .pbix.

Run:  .venv/bin/python src/export_dax.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "DAX_MEASURES.md"
OUT = ROOT / "powerbi" / "measures.dax"

HEADER = """// Power BI measures for the Legal AI Operations Observatory.
//
// Generated from docs/DAX_MEASURES.md by src/export_dax.py -- edit the
// documentation, not this file. The documentation carries the value each
// measure must return; a measure that disagrees with it is wrong in the model,
// not in the database.
//
// Paste one measure at a time into Power BI Desktop:
//   right-click the _Measures table -> New measure -> paste -> Enter
// They are in dependency order, so work top to bottom.
"""


def main() -> None:
    text = SRC.read_text()
    blocks = re.findall(r"```dax\n(.*?)```", text, re.S)

    measures, seen = [], set()
    for block in blocks:
        # A measure header is a line at column 0 whose text before the first
        # "=" holds no bracket or comma -- that is what separates "Name =" from
        # a continuation line such as "CALCULATE ( x[y] = TRUE () )".
        # "VAR x =" satisfies that test as well, so DAX keywords that can open a
        # line are excluded explicitly; without this a measure written with
        # VAR/RETURN is cut in half and the half that keeps the name is empty.
        parts = re.split(
            r"\n(?!VAR\b|RETURN\b)(?=[A-Za-z][^\n=\[\],]*=(?!=))",
            "\n" + block.strip(),
        )
        for part in parts:
            part = part.strip()
            if not part or "=" not in part:
                continue
            name = part.split("=", 1)[0].strip()
            if name in seen:
                continue
            seen.add(name)
            measures.append(part)

    body = "\n\n".join(f"// ---- {m.split('=', 1)[0].strip()} ----\n{m}" for m in measures)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(f"{HEADER}\n{body}\n")
    print(f"wrote {OUT.relative_to(ROOT)} — {len(measures)} measures")
    for i, m in enumerate(measures, 1):
        print(f"  {i:2d}. {m.split('=', 1)[0].strip()}")


if __name__ == "__main__":
    main()
