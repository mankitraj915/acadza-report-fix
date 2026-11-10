"""Builds web/data/console.json from results.json + data/ + bonus + trace.

    python -m engine.export_console

The console is read-only over this file: it never calls an LLM. Everything the
UI needs (split options, before/after text, ranking inputs) is computed here
so the browser does no parsing of question markup beyond sanitising it.
"""
import json
import pathlib
import re
import sys

from . import guards
from .textview import body, to_text

ROOT = pathlib.Path(__file__).resolve().parent.parent
MARK = re.compile(r"\(([A-D])\)")
ROUTE_RANK = {"faculty_review": 0, "auto_apply": 1, "no_change": 2}


def split_raw(raw):
    """Split question markup into (stem, [(letter, markup)]) at the option markers.
    Same rule as textview.split_options: the last '(A)' that is followed by '(B)'."""
    marks = [(m.group(1), m.start(), m.end()) for m in MARK.finditer(raw or "")]
    start = None
    for i, (letter, _, _) in enumerate(marks):
        if letter == "A" and any(l == "B" for l, _, _ in marks[i + 1:]):
            start = i
    if start is None:
        return raw or "", []
    chain, want = [], "A"
    for letter, s, e in marks[start:]:
        if letter == want:
            chain.append((letter, s, e))
            want = chr(ord(want) + 1)
    opts = []
    for j, (letter, s, e) in enumerate(chain):
        end = chain[j + 1][1] if j + 1 < len(chain) else len(raw)
        opts.append({"letter": letter, "html": raw[e:end]})
    return raw[: chain[0][1]], opts


def view(q, question=None, solution=None, answer=None, qtype=None):
    b = body(q)
    stem, opts = split_raw(question if question is not None else b.get("question"))
    return {"stemHtml": stem, "options": opts,
            "solutionHtml": solution if solution is not None else b.get("solution") or "",
            "answer": answer if answer is not None else b.get("answer"),
            "qtype": qtype or q.get("questionType")}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    load = lambda rel: json.loads((ROOT / rel).read_text(encoding="utf-8"))
    cfg = load("engine/config/config.json")
    tax = load(cfg["paths"]["taxonomy"])
    questions = {q["_id"]: q for q in load(cfg["paths"]["questions"])}
    reports = {r["reportId"]: r for r in load(cfg["paths"]["reports"])}
    rows = load(cfg["paths"]["results"])
    bonus = load(cfg["paths"]["bonus"])
    trace = load(cfg["paths"]["trace"])
    usage = load(cfg["paths"]["usage"])
    stakes = cfg["stakes"]

    by_q = {}
    for r in rows:
        by_q.setdefault(r["questionId"], []).append(r)
    cases = []
    for qid, rs in by_q.items():
        q = questions[qid]
        rs = sorted(rs, key=lambda r: r["reportId"])
        weight = lambda r: stakes.get(r["repair"], 0.5) if r["verdict"] == "defect" else 0.5
        lead = sorted(rs, key=lambda r: (ROUTE_RANK[r["route"]], -weight(r), -r["confidence"]))[0]
        edits = ([lead["edit"]] if lead["edit"] else []) + lead.get("extra_edits", [])
        after, diff = None, None
        if edits:
            new = {"question": None, "solution": None, "answer": None, "questionType": None}
            for e in edits:
                new[e["field"]] = guards.apply_edit(q, e)
            after = view(q, new["question"], new["solution"], new["answer"], new["questionType"])
            e = lead["edit"]
            if e["field"] in guards.TEXT_FIELDS:
                diff = {"field": e["field"], "kind": "text", "beforeText": to_text(body(q).get(e["field"])),
                        "afterText": to_text(new[e["field"]]), "rawFind": e["find"], "rawReplace": e["replace"]}
            else:
                diff = {"field": e["field"], "kind": "value", "beforeText": json.dumps(e["find"]),
                        "afterText": json.dumps(e["replace"]), "rawFind": json.dumps(e["find"]),
                        "rawReplace": json.dumps(e["replace"])}
        s = weight(lead)
        cases.append({
            "questionId": qid, "subject": q.get("subject"), "chapter": q.get("chapter"), "level": q.get("level"),
            "before": view(q), "after": after, "diff": diff,
            "leadReportId": lead["reportId"], "verdict": lead["verdict"], "repair": lead["repair"],
            "repairLabel": tax["repairs"][lead["repair"]]["label"] if lead["repair"] else None,
            "repairAuto": tax["repairs"][lead["repair"]]["auto"] if lead["repair"] else None,
            "route": lead["route"], "confidence": lead["confidence"], "evidence": lead["evidence"],
            "stakes": s, "reporters": len({reports[r["reportId"]]["studentId"] for r in rs}),
            "score": round(s * len(rs) * lead["confidence"], 2),
            "keyChange": bool(lead["edit"] and lead["edit"]["field"] == "answer"),
            "keyCheck": (trace["questions"].get(qid) or {}).get("key_check"),
            "history": q.get("errorHistory") or [],
            "reports": [{**{k: r[k] for k in ("reportId", "verdict", "repair", "route", "confidence", "evidence", "student_reply")},
                         "comment": reports[r["reportId"]]["comment"], "source": reports[r["reportId"]]["source"],
                         "reportedAt": reports[r["reportId"]]["reportedAt"]} for r in rs],
            "alsoFound": [b for b in bonus if b["questionId"] == qid],
        })
    cases.sort(key=lambda c: (ROUTE_RANK[c["route"]], -c["score"], c["questionId"]))
    unreported = {}
    for b in bonus:
        if not b["reported"]:
            unreported.setdefault(b["questionId"], None)
    out = {
        "meta": {"generatedAt": trace["generated_at"], "provider": trace["provider"], "models": trace["models"],
                 "prompts": trace["prompts"], "reports": len(rows), "questions": len(questions),
                 "calls": usage["calls"], "promptTokens": usage["prompt_tokens"],
                 "completionTokens": usage["completion_tokens"]},
        "verdicts": tax["verdicts"],
        "cases": cases,
        "bonus": bonus,
        "unreportedQuestions": {qid: {"subject": questions[qid].get("subject"), "chapter": questions[qid].get("chapter"),
                                      "before": view(questions[qid])} for qid in unreported},
    }
    dest = ROOT / "web/data/console.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {dest.relative_to(ROOT)}: {len(cases)} cases, {len(bonus)} bonus findings, {dest.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
