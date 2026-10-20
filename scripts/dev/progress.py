from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHECKLIST = ROOT / "docs" / "checklist_status.md"
STATUS_WEIGHT = {"done": 1.0, "partial": 0.5, "missing": 0.0}


def main() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    matches = re.findall(r"^- `(?P<status>done|partial|missing)` ", text, flags=re.MULTILINE)
    if not matches:
        raise SystemExit("No checklist status rows found.")
    counts = {status: matches.count(status) for status in STATUS_WEIGHT}
    score = sum(STATUS_WEIGHT[status] for status in matches)
    percent = score / len(matches) * 100
    filled = round(percent / 5)
    bar = "#" * filled + "-" * (20 - filled)
    print(f"[{bar}] {percent:.1f}%")
    print(f"items={len(matches)} done={counts['done']} partial={counts['partial']} missing={counts['missing']}")
    print("source=docs/checklist_status.md")


if __name__ == "__main__":
    main()
