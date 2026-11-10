"""Injects the product prompts, verbatim from engine/config/prompts/, into PROMPTS.md.

    python -m engine.prompts_doc

Everything between the PRODUCT-PROMPTS markers in PROMPTS.md is regenerated: the
active version of each prompt in full, and a unified diff for each earlier
version. The prompt files are the source of truth; this keeps the doc honest.
"""
import difflib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
PDIR = ROOT / "engine/config/prompts"
START, END = "<!-- PRODUCT-PROMPTS:START -->", "<!-- PRODUCT-PROMPTS:END -->"
ORDER = ["solve_a", "solve_b", "audit", "classify", "edit", "reply"]


def main():
    cfg = json.loads((ROOT / "engine/config/config.json").read_text(encoding="utf-8"))
    out = []
    for role in ORDER:
        active = cfg["prompts"][role]
        versions = sorted(PDIR.glob(f"{role}.v*.txt"), key=lambda p: int(re.search(r"\.v(\d+)\.", p.name).group(1)))
        out.append(f"### `{role}` (active: `{active}`)\n")
        out.append(f"Model: `{cfg['llm']['models']['groq'][role]}` on Groq; fallback `{cfg['llm']['models']['gemini'][role]}` on Gemini.\n")
        text = (PDIR / f"{active}.txt").read_text(encoding="utf-8").rstrip()
        out.append(f"````text\n{text}\n````\n")
        for older, newer in zip(versions, versions[1:]):
            a = older.read_text(encoding="utf-8").splitlines()
            b = newer.read_text(encoding="utf-8").splitlines()
            diff = "\n".join(difflib.unified_diff(a, b, older.stem, newer.stem, lineterm="", n=0))
            out.append(f"<details><summary>Diff {older.stem} → {newer.stem}</summary>\n\n````diff\n{diff}\n````\n\n</details>\n")
    doc = ROOT / "PROMPTS.md"
    s = doc.read_text(encoding="utf-8")
    i, j = s.index(START) + len(START), s.index(END)
    doc.write_text(s[:i] + "\n\n" + "\n".join(out) + "\n" + s[j:], encoding="utf-8")
    print(f"PROMPTS.md: injected {len(ORDER)} prompts")


if __name__ == "__main__":
    main()
