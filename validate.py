"""Checks results.json against the taxonomy and the edit guards.

    python validate.py [results.json]

Exit code 0 only if there are no violations. It re-runs the SAME guards the
engine uses (engine/guards.py) on the stored data, so a hand-edited
results.json is checked too.
"""
import collections
import json
import pathlib
import sys

from engine import guards
from engine.textview import body

ROOT = pathlib.Path(__file__).resolve().parent
ROUTES = ("auto_apply", "faculty_review", "no_change")
REQUIRED = ("reportId", "questionId", "verdict", "repair", "edit", "route", "confidence", "evidence", "student_reply")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = json.loads((ROOT / "engine/config/config.json").read_text(encoding="utf-8"))
    load = lambda rel: json.loads((ROOT / rel).read_text(encoding="utf-8"))
    tax = load(cfg["paths"]["taxonomy"])
    questions = {q["_id"]: q for q in load(cfg["paths"]["questions"])}
    reports = {r["reportId"]: r for r in load(cfg["paths"]["reports"])}
    path = sys.argv[1] if len(sys.argv) > 1 else cfg["paths"]["results"]
    rows = load(path)
    errs = []
    bad = lambda rid, msg: errs.append(f"{rid}: {msg}")

    ids = [r.get("reportId") for r in rows]
    for rid in sorted(set(reports) - set(ids)):
        bad(rid, "missing from results")
    for rid, n in collections.Counter(ids).items():
        if n > 1:
            bad(rid, f"appears {n} times")
        if rid not in reports:
            bad(rid, "is not a known reportId")

    for r in rows:
        rid = r.get("reportId")
        miss = [k for k in REQUIRED if k not in r]
        if miss:
            bad(rid, f"missing keys {miss}")
            continue
        if rid in reports and reports[rid]["questionId"] != r["questionId"]:
            bad(rid, "questionId does not match the report")
        q = questions.get(r["questionId"])
        if q is None:
            bad(rid, "unknown questionId")
            continue
        if r["verdict"] not in tax["verdicts"]:
            bad(rid, f"verdict '{r['verdict']}' not in taxonomy")
        if r["route"] not in ROUTES:
            bad(rid, f"route '{r['route']}' invalid")
        if r["verdict"] == "defect":
            if r["repair"] not in tax["repairs"]:
                bad(rid, f"defect with repair '{r['repair']}' not in taxonomy")
                continue
        elif r["repair"] is not None or r["edit"] is not None:
            bad(rid, "non-defect must have repair = null and edit = null")
        if r["verdict"] != "defect" and r["route"] == "auto_apply":
            bad(rid, "auto_apply on a non-defect")
        c = r["confidence"]
        if not isinstance(c, (int, float)) or isinstance(c, bool) or not 0 <= c <= 1:
            bad(rid, "confidence must be a number in [0, 1]")
        if not isinstance(r["evidence"], str) or not r["evidence"].strip():
            bad(rid, "empty evidence")
        s = r["student_reply"]
        if not isinstance(s, str) or not s.strip() or len(s) > cfg["limits"]["reply_max_chars"]:
            bad(rid, "student_reply empty or too long")
        if r["verdict"] == "defect" and r["repair"] in tax["repairs"]:
            spec = tax["repairs"][r["repair"]]
            if not spec["auto"]:
                if r["route"] == "auto_apply":
                    bad(rid, f"UNSAFE: auto_apply on auto:false repair '{r['repair']}'")
                if r["edit"] is not None:
                    bad(rid, f"edit proposed for '{r['repair']}', which has no editable fields")
            if r["route"] == "auto_apply" and r["edit"] is None:
                bad(rid, "auto_apply without an edit")
            for e in ([r["edit"]] if r["edit"] else []) + r.get("extra_edits", []):
                for msg in guards.check_edit(q, r["repair"], e, tax, cfg["limits"]):
                    bad(rid, f"edit guard: {msg}")
                if isinstance(e, dict) and e.get("field") == "answer" and not guards.check_edit(q, r["repair"], e, tax, cfg["limits"]):
                    from engine.textview import split_options, to_text
                    qtype = next((x["replace"] for x in r.get("extra_edits", []) if x["field"] == "questionType"), q["questionType"])
                    letters = set(split_options(to_text(body(q).get("question")))[1])
                    if not guards.valid_answer(qtype, e["replace"], letters):
                        bad(rid, f"new answer {e['replace']!r} is not a valid {qtype} answer")

    print(f"{path}: {len(rows)} rows, {len(reports)} reports")
    for name in ("verdict", "repair", "route"):
        print(f"  {name}: " + ", ".join(f"{k}={v}" for k, v in collections.Counter(str(r.get(name)) for r in rows).most_common()))
    auto = [r for r in rows if r.get("route") == "auto_apply"]
    print(f"  auto_apply edits: {len(auto)} (" + ", ".join(sorted(f"{r['reportId']}:{r['repair']}" for r in auto)) + ")")
    if errs:
        print(f"\nFAIL: {len(errs)} violation(s)")
        for e in errs:
            print("  -", e)
        sys.exit(1)
    print("\nOK: enums valid, every edit in scope, every find matches exactly once, no auto_apply on auto:false.")


if __name__ == "__main__":
    main()
