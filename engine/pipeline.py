"""Report -> Fix triage pipeline.

    python -m engine.pipeline            # full run (uses the cache; needs a key on a cache miss)
    python -m engine.pipeline --offline  # cache only, never calls the network
    python -m engine.pipeline --lint     # deterministic checks only, no LLM

Order of authority:  code facts  >  LLM judgment  >  student comment.
The LLM classifies and drafts; code decides the key, the scope and the route.
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import sys

from . import guards
from .lint import canonical_answer, lint_comment, lint_question, norm_key
from .llm import LLM, LLMUnavailable, load_env
from .textview import body, split_options, to_text

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLAIM_TYPES = ["answer_wrong", "vague_wrong", "missing_data", "option_problem", "spelling", "markup_or_noise",
               "figure", "solution_problem", "chapter", "syllabus", "unit_or_form", "display", "app", "language",
               "unintelligible", "instruction_attempt", "other"]
HINGLISH = re.compile(r"\b(hai|hain|galat|nahi|nahin|kyu|kyun|chahiye|aana|hona|toh|kya|karu|karun|raha|rahi|diya|"
                      r"pucha|kuch|sahi|ki value|me question|mein)\b", re.I)
EDIT_RULES = {
    "spelling": "Change letters inside words only. No digit, symbol, tag or spacing may change.",
    "noise": "Only delete the stray text. Add nothing.",
    "exam_reference": "Only delete the exam tag. Add nothing.",
    "markup_leak": "Replace the leaked raw code with the properly marked-up equivalent (MathML for maths). The meaning must not change.",
    "answer_form": "Only ADD the missing unit / rounding / answer form to the question (for example ' (in N)'). Remove nothing.",
    "notation": "Restore the one lost or misplaced symbol, exponent or bracket. Change nothing else.",
    "stem_data": "Insert the missing value or condition into the stem, using exactly the value the stored solution uses. Do not touch the options.",
    "option_repair": "Fill or restore the one broken option only, using the value given in the facts. The stem and the other options must not change.",
    "solution_fix": "Correct the wrong step, or delete the lines that belong to another problem. The solution must end at the stored key.",
    "solution_missing": "Write a short correct solution that ends at the stored key.",
}


def load_json(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def load_prompt(name):
    txt = (ROOT / "engine/config/prompts" / f"{name}.txt").read_text(encoding="utf-8")
    sys_part, user_part = txt.split("### USER", 1)
    return sys_part.replace("### SYSTEM", "").strip(), user_part.strip()


def fill(tpl, **kw):
    for k, v in kw.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    left = re.findall(r"\{\{(\w+)\}\}", tpl)
    if left:
        raise KeyError(f"unfilled prompt variables: {left}")
    return tpl


# ---------- output validators (return an error string, or None when valid) ----------

def v_solve(o):
    if not isinstance(o.get("value"), str) or not isinstance(o.get("letters"), list):
        return "need value:string and letters:list"
    if any(l not in ("A", "B", "C", "D") for l in o["letters"]):
        return "letters must be from A-D"
    return None


def v_audit(tax):
    def f(o):
        sf = o.get("solution_final")
        if not isinstance(sf, dict) or not isinstance(sf.get("letters", []), list):
            return "solution_final must be {value, letters}"
        if o.get("solution_belongs") not in ("yes", "partly", "no"):
            return "solution_belongs must be yes|partly|no"
        if o.get("solution_supports_key") not in (True, False, None):
            return "solution_supports_key must be true|false|null"
        for i in o.get("issues") or []:
            if i.get("repair") not in tax["repairs"]:
                return f"issue repair '{i.get('repair')}' is not in the taxonomy"
        return None
    return f


def v_classify(tax):
    def f(o):
        if o.get("verdict") not in tax["verdicts"]:
            return f"verdict '{o.get('verdict')}' is not in the taxonomy; allowed: {list(tax['verdicts'])}"
        if o["verdict"] == "defect" and o.get("repair") not in tax["repairs"]:
            return f"a defect needs a repair from: {list(tax['repairs'])}"
        if o["verdict"] != "defect" and o.get("repair") is not None:
            return "repair must be null unless the verdict is defect"
        if o.get("claim_type") not in CLAIM_TYPES:
            return f"claim_type must be one of {CLAIM_TYPES}"
        c = o.get("confidence")
        if not isinstance(c, (int, float)) or isinstance(c, bool) or not 0 <= c <= 1:
            return "confidence must be a number between 0 and 1"
        if not isinstance(o.get("evidence"), str) or not o["evidence"].strip():
            return "evidence must be a non-empty string"
        return None
    return f


def v_edit(o):
    if not all(k in o for k in ("field", "find", "replace")):
        return "need field, find, replace"
    return None


def v_reply(o):
    return None if isinstance(o.get("reply"), str) and o["reply"].strip() else "need reply:string"


class Engine:
    def __init__(self, cfg, llm):
        self.cfg, self.llm = cfg, llm
        self.tax = load_json(cfg["paths"]["taxonomy"])
        self.prompts = {role: load_prompt(name) for role, name in cfg["prompts"].items()}
        self.tol = cfg["thresholds"]["integer_tolerance"]
        self.repairs_block = "\n".join(f"- {k}: {v['label']}" for k, v in self.tax["repairs"].items())
        self.verdicts_block = "\n".join(f"- {k}: {v}" for k, v in self.tax["verdicts"].items())

    # ----- LLM call with schema validation: invalid twice -> None -----
    def ask(self, role, tag, validator, **vars_):
        system, user_tpl = self.prompts[role]
        system = fill(system, **{k: v for k, v in vars_.items() if "{{" + k + "}}" in system})
        user = fill(user_tpl, **{k: v for k, v in vars_.items() if "{{" + k + "}}" in user_tpl})
        err = None
        for attempt in (1, 2):
            u = user if attempt == 1 else user + f"\n\nYour previous output was rejected: {err}. Return a corrected JSON object."
            try:
                out = self.llm.chat_json(role, system, u, tag=f"{tag}#{attempt}")
            except ValueError as e:
                err = f"not valid JSON ({e})"
                continue
            err = validator(out)
            if err is None:
                return out
        print(f"    [invalid twice] {role}/{tag}: {err}")
        return None

    # ----- per-question assessment (comment never enters here) -----
    def assess(self, q):
        qtype, b = q["questionType"], body(q)
        stem, opts = split_options(to_text(b.get("question")))
        sol_text = to_text(b.get("solution"))
        key, malformed = norm_key(qtype, b.get("answer"))
        lint = lint_question(q)
        has_opts = qtype in ("scq", "mcq")
        options_block = "\n".join(f"({l}) {t if t else '[blank]'}" for l, t in sorted(opts.items())) if has_opts else ""
        type_hint = ("Options question. One or more options may be correct." if has_opts
                     else "Numeric answer (no options).")
        common = dict(subject=q.get("subject"), type_hint=type_hint, stem=stem, options_block=options_block)
        sa = self.ask("solve_a", q["_id"], v_solve, **common)
        # A second solve buys nothing when a deterministic check already proves a defect that
        # does not depend on the key (exam tag, identical options, missing figure, ...).
        key_bound = [f for f in lint if f["certain"] and (f["repair"] in ("answer_key", "type_mismatch")
                                                          or f["code"] in ("option_blank", "key_points_to_empty"))]
        skip_b = any(f["certain"] for f in lint) and not key_bound
        sb = None if skip_b else self.ask("solve_b", q["_id"], v_solve, **common)
        lint_block = "\n".join(f"- {f['code']}: {f['detail']}" + ("" if f["certain"] else " (unconfirmed lead)")
                               for f in lint) or "- nothing"
        audit = self.ask("audit", q["_id"], v_audit(self.tax), subject=q.get("subject"), chapter=q.get("chapter"),
                         concept=f"{q.get('concept')} / {q.get('subConcept')}", qtype=qtype, stem=stem,
                         options_block=options_block, key=json.dumps(b.get("answer")), solution=sol_text or "[empty]",
                         lint_block=lint_block, repairs_block=self.repairs_block)
        na, nb = self._norm_solve(qtype, sa), self._norm_solve(qtype, sb)
        nsol = self._norm_solution(qtype, audit, key)
        state = self._key_state(key, na, nb, nsol)
        if skip_b and self._eq(na, key):
            state = "single_confirmed"  # one solve by design, and it reaches the key
        findings = []
        for f in lint:
            findings.append({"id": f"L{len(findings) + 1}", "source": "lint", **f})
        for i in (audit or {}).get("issues") or []:
            findings.append({"id": f"A{len(findings) + 1}", "source": "audit", "code": "audit_issue", "certain": False,
                             "repair": i.get("repair"), "field": i.get("field"), "quote": i.get("quote"),
                             "detail": i.get("why"), "severity": i.get("severity")})
        return dict(q=q, qtype=qtype, stem=stem, opts=opts, options_block=options_block, sol_text=sol_text,
                    key=key, malformed=malformed, sa=sa, sb=sb, audit=audit, na=na, nb=nb, nsol=nsol,
                    state=state, findings=findings, skip_b=skip_b)

    def _norm_solve(self, qtype, s):
        if not s or s.get("blocker"):
            return None
        if qtype == "integerQuestion":
            m = re.search(r"-?\d+(?:\.\d+)?", str(s.get("value", "")).replace(",", ""))
            return float(m.group(0)) if m else None
        return frozenset(s.get("letters") or [])

    def _norm_solution(self, qtype, audit, key):
        if not audit:
            return None
        if audit.get("solution_belongs") == "no":
            return None
        if audit.get("solution_supports_key") is True:
            return key
        sf = audit.get("solution_final") or {}
        if qtype == "integerQuestion":
            m = re.search(r"-?\d+(?:\.\d+)?", str(sf.get("value") or "").replace(",", ""))
            return float(m.group(0)) if m else None
        return frozenset(sf["letters"]) if sf.get("letters") else None

    def _eq(self, x, y):
        if x is None or y is None:
            return False
        if isinstance(x, float) or isinstance(y, float):
            return abs(float(x) - float(y)) <= self.tol * max(1.0, abs(float(y)))
        return x == y

    def _key_state(self, key, na, nb, nsol):
        """confirmed | disproved | solves_only_disagree | partly_confirmed | unresolved"""
        eq = self._eq
        if eq(na, nb) and not eq(na, key):
            return "disproved" if eq(na, nsol) else "solves_only_disagree"
        if eq(na, key) and eq(nb, key):
            return "confirmed"
        if eq(na, key) or eq(nb, key):
            return "partly_confirmed"
        return "unresolved"

    def fmt_ans(self, a, norm):
        """Human form of a normalized answer: 'D (4800 N/m^2)' or '50'."""
        if norm is None:
            return "no result"
        if isinstance(norm, float):
            return canonical_answer("integerQuestion", norm)
        if not norm:
            return "none of the listed options"
        return ", ".join(f"{l} ({a['opts'].get(l) or 'blank'})" for l in sorted(norm))

    def key_check_text(self, a):
        def solve(s, n):
            if not s:
                return "no valid output"
            if s.get("blocker"):
                return f"blocked: {s['blocker']}"
            return f"{self.fmt_ans(a, n)} [value: {s.get('value')}]"
        txt = {
            "disproved": "KEY DISPROVED: both blind solves agree with each other and with the stored solution, and differ from the stored key.",
            "solves_only_disagree": "Both blind solves agree with each other and differ from the stored key; the stored solution does not back them.",
            "confirmed": "KEY CONFIRMED: both blind solves reach the stored key.",
            "partly_confirmed": "One blind solve reaches the stored key, the other does not.",
            "unresolved": "Unresolved: the blind solves do not settle the key.",
            "single_confirmed": "The one blind solve that was run reaches the stored key.",
        }[a["state"]]
        if a["malformed"]:
            txt += " The stored key is MALFORMED for its type; its letters read as " + str(sorted(a["key"] or [])) + "."
        s2 = "skipped (lint already proves a key-independent defect)" if a["skip_b"] else solve(a["sb"], a["nb"])
        return (f"solve 1 = {solve(a['sa'], a['na'])}; solve 2 = {s2}; "
                f"solution = {self.fmt_ans(a, a['nsol'])}; stored key = {self.fmt_ans(a, a['key'])}. {txt}")

    # ----- per-report -----
    def triage(self, rep, a):
        q, b = a["q"], body(a["q"])
        trace = []
        hist = q.get("errorHistory") or []
        later = [h for h in hist if str(h.get("at", "")) > rep["reportedAt"]]
        history = "; ".join(f"{h.get('at')} {h.get('actor')}: {h.get('type')} - {h.get('note')}" for h in hist) or "no edits recorded"
        flags = lint_comment(rep["comment"])
        solve_txt = lambda s: json.dumps({k: s.get(k) for k in ("value", "letters", "blocker", "working")}, ensure_ascii=False) if s else "no valid output"
        au = a["audit"] or {}
        audit_block = (f"solution_final = {json.dumps(au.get('solution_final'), ensure_ascii=False)}; "
                       f"solution_belongs = {au.get('solution_belongs')}; solution_supports_key = {au.get('solution_supports_key')}"
                       if au else "audit unavailable")
        findings_block = "\n".join(
            f"- {f['id']} [{f['source']}{'' if f['certain'] else ', unconfirmed'}] {f['repair']} in {f['field']}: {f['detail']}"
            + (f" (\"{f['quote']}\")" if f.get("quote") else "") for f in a["findings"]) or "- none"
        comment = rep["comment"].replace("COMMENT>>>", "COMMENT>>")
        c = self.ask("classify", rep["reportId"], v_classify(self.tax), verdicts_block=self.verdicts_block,
                     repairs_block=self.repairs_block, subject=q.get("subject"), chapter=q.get("chapter"),
                     qtype=a["qtype"], stem=a["stem"], options_block=a["options_block"],
                     key=json.dumps(b.get("answer")), solution=a["sol_text"] or "[empty]", solve_a=solve_txt(a["sa"]),
                     solve_b=solve_txt(a["sb"]), key_check=self.key_check_text(a), audit_block=audit_block,
                     findings_block=findings_block, history=history, reported_at=rep["reportedAt"],
                     comment_flags=", ".join(flags) or "none", comment=comment)
        force_review = None
        if c is None:
            c = {"verdict": "no_error", "repair": None, "confidence": 0.2, "claim": "", "claim_type": "other",
                 "evidence": "Classifier output failed schema validation twice; needs a human read."}
            force_review = "classifier invalid twice"
        verdict, repair, conf, evidence = c["verdict"], c["repair"], float(c["confidence"]), c["evidence"].strip()
        trace.append(f"classifier: {verdict}/{repair} conf={conf:.2f} claim_type={c.get('claim_type')}")
        state, key_claim = a["state"], c.get("claim_type") in ("answer_wrong", "vague_wrong", "instruction_attempt")
        proof = False  # set True only by a code-level check

        # --- code rules: facts override the classifier on anything touching the key ---
        if verdict == "already_fixed" and not later:
            force_review, conf = "already_fixed claimed but no edit is recorded after the report date", min(conf, 0.4)
        if verdict == "already_fixed" and later:
            conf = max(conf, 0.9)
            evidence = f"History: {later[-1].get('actor')} edit on {later[-1].get('at')[:10]} ({later[-1].get('note')}), after the report of {rep['reportedAt'][:10]}; text no longer has it."
        if verdict == "no_error" and key_claim:
            if state == "disproved" and not a["malformed"]:
                verdict, repair = "defect", "answer_key"
                trace.append("rule: classifier said no_error but the key is disproved by 2 solves + solution -> defect/answer_key")
            elif state == "confirmed":
                conf = max(conf, 0.9)
            elif state == "single_confirmed":
                conf = min(max(conf, 0.8), 0.85)
            elif state == "partly_confirmed" and self._eq(a["nsol"], a["key"]):
                conf = min(conf, 0.75)
            else:
                conf = min(conf, 0.45)
                trace.append(f"rule: key state '{state}' does not back no_error -> low confidence")
        # A key can only be "wrong" if some listed option is right. If both solves say that no
        # listed option (or more than one, in an scq) is correct, the question is unsound as written.
        none_right = a["na"] == a["nb"] == frozenset() and a["qtype"] in ("scq", "mcq")
        many_right = a["qtype"] == "scq" and a["na"] is not None and a["na"] == a["nb"] and len(a["na"]) > 1
        empty_opts = [l for l in "ABCD" if not a["opts"].get(l)]
        keyed_empty = [l for l in empty_opts if a["key"] and not isinstance(a["key"], float) and l in a["key"]]
        if verdict == "defect" and repair in ("answer_key", "unsound") and keyed_empty:
            trace.append(f"rule: classifier said {repair}, but the keyed option {keyed_empty} is blank in the record -> option_repair")
            repair = "option_repair"
            evidence = (f"Option {keyed_empty[0]} is blank and is the keyed answer; 2 blind solves give "
                        f"{(a['sa'] or {}).get('value')} / {(a['sb'] or {}).get('value')}, which no listed option has.")
        if verdict == "defect" and repair in ("answer_key", "solution_fix", "unsound") and (none_right or many_right) and not empty_opts:
            if repair != "unsound":
                trace.append(f"rule: classifier said {repair}, but both solves find "
                             f"{'no' if none_right else 'more than one'} correct option -> unsound")
            repair, conf = "unsound", max(conf, 0.8)
            evidence = (f"2 blind solves agree: {(a['sa'] or {}).get('value')}; "
                        f"{'no listed option says that' if none_right else 'options ' + ', '.join(sorted(a['na'])) + ' are both correct'}. "
                        f"Key {self.fmt_ans(a, a['key'])} cannot be repaired by changing the key.")
        if verdict == "defect" and repair == "answer_key":
            target = a["na"]
            if a["malformed"] and state == "confirmed":
                proof, conf = True, max(conf, 0.93)
                evidence = (f"Key {json.dumps(b.get('answer'))} is malformed for {a['qtype']}; 2 blind solves = "
                            f"{sorted(a['key'])}, the same letters. Form fixed, content unchanged.")
            elif state == "disproved":
                proof, conf = True, max(conf, 0.95)
                evidence = (f"2 blind solves = {self.fmt_ans(a, target)} = stored solution; "
                            f"key {self.fmt_ans(a, a['key'])}.")
            elif state == "solves_only_disagree":
                conf = min(conf, 0.7)
                evidence = (f"2 blind solves = {self.fmt_ans(a, target)}, key {self.fmt_ans(a, a['key'])}, "
                            f"but the stored solution does not confirm it; not proven enough to auto-change a key.")
            elif state == "confirmed":
                verdict, repair, conf = "no_error", None, 0.85
                evidence = f"2 blind solves = {self.fmt_ans(a, a['key'])} = stored key."
                trace.append("rule: classifier wanted answer_key but both solves confirm the key -> no_error")
            else:
                conf = min(conf, 0.45)
                trace.append(f"rule: answer_key not provable (key state '{state}') -> faculty_review")
        if verdict == "defect" and repair == "type_mismatch":
            proof = (a["qtype"] == "mcq" and a["key"] is not None and len(a["key"]) == 1 and state == "confirmed")
            if proof:
                conf = max(conf, 0.92)
                evidence = (f"Stored as mcq with key {json.dumps(b.get('answer'))}; 2 blind solves find exactly one "
                            f"correct option, {self.fmt_ans(a, a['key'])}. Single-correct, so scq.")
            else:
                conf = min(conf, 0.7)
        lint_backed = any(f["source"] == "lint" and f["certain"] and f["repair"] == repair for f in a["findings"])
        if verdict == "defect" and lint_backed:
            conf = max(conf, 0.9)
            trace.append("rule: deterministic lint confirms this repair type")
            if not proof:  # name the concrete check, not the classifier's paraphrase
                hit = next(f for f in a["findings"] if f["source"] == "lint" and f["certain"] and f["repair"] == repair)
                evidence = f"Stored record check: {hit['detail']}."

        # --- edit ---
        edit, extra, guard_errs, note = None, [], [], None
        spec = self.tax["repairs"].get(repair) if repair else None
        if verdict == "defect" and spec and spec["auto"]:
            edit, extra, note = self.build_edit(rep, a, repair, c, proof)
            if edit:
                guard_errs = guards.check_edit(q, repair, edit, self.tax, self.cfg["limits"])
                for e in extra:
                    guard_errs += guards.check_edit(q, repair, e, self.tax, self.cfg["limits"])
                if guard_errs:
                    trace.append(f"guards rejected the edit: {guard_errs}")
                    edit, extra = None, []

        # --- route (code only) ---
        tau = self.cfg["thresholds"]["auto_apply"]
        needs_proof = repair in ("answer_key", "type_mismatch", "option_repair")
        if verdict != "defect":
            route = "no_change"
            if force_review or conf < self.cfg["thresholds"]["needs_human_below"]:
                route = "faculty_review"
                trace.append(f"route: non-defect but {force_review or 'confidence below the human-read floor'} -> faculty_review")
        elif not spec["auto"]:
            route = "faculty_review"
            trace.append(f"route: {repair} is auto:false in the taxonomy -> faculty_review")
        elif edit and not guard_errs and conf >= tau.get(repair, tau["default"]) and (proof or not needs_proof):
            route = "auto_apply"
        else:
            route = "faculty_review"
            why = ("no safe edit could be built" if not edit else
                   "code-level proof missing" if needs_proof and not proof else "confidence below threshold")
            conf = min(conf, 0.75)
            trace.append(f"route: {why} -> faculty_review")
        if note:
            evidence = evidence.rstrip(". ") + ". " + note
        if "instruction_attempt" in flags:
            evidence = evidence.rstrip(". ") + ". Comment contains an instruction to the system; ignored."

        row = {"reportId": rep["reportId"], "questionId": rep["questionId"], "verdict": verdict,
               "repair": repair if verdict == "defect" else None, "edit": edit, "route": route,
               "confidence": round(conf, 2), "evidence": re.sub(r"\s+", " ", evidence)[:320]}
        if extra:
            row["extra_edits"] = extra
        row["student_reply"] = self.reply(rep, a, row, later)
        return row, {"reportId": rep["reportId"], "classifier": c, "trace": trace, "comment_flags": flags,
                     "guard_errors": guard_errs}

    def build_edit(self, rep, a, repair, c, proof):
        """-> (edit|None, extra_edits, evidence_note|None). Key edits are built in code, never by the LLM."""
        q, b = a["q"], body(a["q"])
        if repair == "answer_key":
            if not proof:
                return None, [], None
            target = a["key"] if (a["malformed"] and a["state"] == "confirmed") else a["na"]
            if not target and not isinstance(target, float):
                return None, [], None
            new = canonical_answer(a["qtype"], target)
            return ({"field": "answer", "find": b.get("answer"), "replace": new} if new != b.get("answer") else None), [], None
        if repair == "type_mismatch":
            if not proof:
                return None, [], None
            return ({"field": "answer", "find": b.get("answer"), "replace": sorted(a["key"])[0]},
                    [{"field": "questionType", "find": "mcq", "replace": "scq"}], None)
        if repair == "option_repair":
            empty = [l for l in "ABCD" if not a["opts"].get(l)]
            keyed = [l for l in empty if a["key"] and l in a["key"]]
            if not keyed:
                return None, [], (f"Option {', '.join(empty) or '?'} is not the keyed answer, so filling it means writing a "
                                  f"new distractor: authored content, faculty must write it.")
            va, vb = (a["sa"] or {}).get("value", ""), (a["sb"] or {}).get("value", "")
            same = re.sub(r"[^0-9a-z:./]", "", str(va).lower()) == re.sub(r"[^0-9a-z:./]", "", str(vb).lower()) and va
            if not (same and a["na"] == frozenset() and a["nb"] == frozenset()):
                return None, [], "The blind solves do not agree on the value for the blank keyed option."
            issue = f"Option ({keyed[0]}) is blank and it is the keyed answer. Fill option ({keyed[0]}) with: {va}"
            edit = self.llm_edit(rep, a, repair, issue, f"Both independent solves give {va}. No listed option has it.")
            return edit, [], None if edit else "No guard-passing edit could be written."
        for f in a["findings"]:
            if f.get("edit") and f["repair"] == repair:
                return f["edit"], [], None
        rel = [f for f in a["findings"] if f["id"] in (c.get("related_findings") or [])]
        issue = c.get("claim", "") + ". " + " ".join(f"{f['detail']} {f.get('quote') or ''}" for f in rel)
        facts = (f"Stored key: {json.dumps(b.get('answer'))}. Checks: {self.key_check_text(a)}\n"
                 f"Classifier evidence: {c.get('evidence')}")
        edit = self.llm_edit(rep, a, repair, issue, facts)
        return edit, [], None if edit else "No guard-passing edit could be written; faculty to edit by hand."

    def llm_edit(self, rep, a, repair, issue, facts):
        q, b = a["q"], body(a["q"])
        spec = self.tax["repairs"][repair]
        raw_block = "\n\n".join(f"RAW MARKUP of field \"{f}\":\n<<<RAW\n{b.get(f) or ''}\nRAW>>>"
                                for f in spec["fields"] if f in guards.TEXT_FIELDS)
        if "question" not in spec["fields"]:
            raw_block += f"\n\nQuestion (read-only, text view):\n{a['stem']}\n{a['options_block']}"
        elif "solution" not in spec["fields"]:
            raw_block += f"\n\nStored solution (read-only, text view):\n{a['sol_text']}"
        feedback = ""
        for attempt in (1, 2):
            out = self.ask("edit", f"{rep['reportId']}-{repair}-{attempt}", v_edit, fields=json.dumps(spec["fields"]),
                           repair=repair, repair_label=spec["label"], repair_rule=EDIT_RULES.get(repair, ""),
                           issue=issue, facts=facts, raw_block=raw_block, feedback=feedback)
            if not out or not out.get("field") or not out.get("find"):
                return None
            edit = {"field": out["field"], "find": out["find"], "replace": out.get("replace") or ""}
            errs = guards.check_edit(q, repair, edit, self.tax, self.cfg["limits"])
            if not errs:
                return edit
            feedback = f"\nYour previous edit was rejected by the checker: {'; '.join(errs)}. Write a corrected edit."
        return None

    def reply(self, rep, a, row, later):
        b = body(a["q"])
        best = a["sa"] if (a["sa"] and not a["sa"].get("blocker")) else a["sb"]
        change = "none"
        if row["edit"]:
            e = row["edit"]
            change = (f"{e['field']}: {json.dumps(e['find'])} -> {json.dumps(e['replace'])}" if e["field"] in guards.VALUE_FIELDS
                      else f"{e['field']}: '{to_text(e['find'])[:120]}' -> '{to_text(e['replace'])[:120]}'")
        checked = self.fmt_ans(a, a["na"] if a["na"] is not None else a["nb"])
        cfgl, forbid = self.cfg["limits"], re.compile(self.cfg["reply_forbidden_unless_auto"], re.I)
        # Did the student name a number that is not in the checked result? Then a bare verdict is not enough.
        named = set(re.findall(r"\d+(?:\.\d+)?", rep["comment"]))
        student_value_differs = (row["verdict"] in ("defect", "no_error") and bool(named) and bool(best)
                                 and row["repair"] in (None, "answer_key")
                                 and not named & set(re.findall(r"\d+(?:\.\d+)?", str(best.get("value", "")))))
        feedback = ""
        for attempt in (1, 2):
            out = self.ask("reply", f"{rep['reportId']}-{attempt}", v_reply, verdict=row["verdict"], repair=row["repair"],
                           route=row["route"], evidence=row["evidence"], change=change, key=json.dumps(b.get("answer")),
                           language="hinglish" if HINGLISH.search(rep["comment"]) else "english",
                           checked_answer=checked, working=(best or {}).get("working", "not available"),
                           history="; ".join(f"{h.get('at', '')[:10]}: {h.get('note')}" for h in later) or "none",
                           stem=a["stem"], options_block=a["options_block"],
                           comment=rep["comment"].replace("COMMENT>>>", "COMMENT>>"), feedback=feedback)
            if not out:
                break
            text = re.sub(r"\s+", " ", out["reply"]).strip()
            problems = []
            if len(text) > cfgl["reply_max_chars"]:
                problems.append(f"too long ({len(text)} chars, max {cfgl['reply_max_chars']})")
            if len(re.findall(r"[.!?](?:\s|$)", text)) > cfgl["reply_max_sentences"] + 1:
                problems.append("more than 3 sentences")
            if row["route"] != "auto_apply" and row["verdict"] != "already_fixed" and forbid.search(text):
                problems.append("it says something was fixed/changed, but nothing has been applied")
            if student_value_differs and "=" not in text:
                problems.append("the student named a value that differs from the checked result, so the reply must "
                                "contain the decisive calculation with numbers and an '=' sign")
            if not problems:
                return text
            feedback = f"\nYour previous reply was rejected: {'; '.join(problems)}. Write it again."
        return {"faculty_review": "Thanks for flagging this. We found something worth a closer look and have sent it to a teacher for review.",
                "auto_apply": "Thanks for flagging this. You were right, and the question has been corrected.",
                "no_change": "Thanks for reporting. We checked this question and the stored answer is correct."}[row["route"]]


def bonus_findings(assessments, rows, reports_by_q, engine):
    """Everything found that no report's verdict already covers."""
    out = []
    for qid, a in assessments.items():
        used = {r["repair"] for r in rows if r["questionId"] == qid and r["repair"]}
        seen = set()
        items = [f for f in a["findings"] if f["source"] == "audit" or f["certain"]]
        if a["state"] == "disproved" and "answer_key" not in used:
            items.append({"source": "solver", "repair": "answer_key", "field": "answer", "certain": False,
                          "detail": f"2 blind solves = {engine.fmt_ans(a, a['na'])} = solution; key {engine.fmt_ans(a, a['key'])}"})
        for f in items:
            k = (f["repair"], f["field"], (f.get("quote") or f["detail"])[:40])
            if f["repair"] in used or k in seen:
                continue
            seen.add(k)
            out.append({"questionId": qid, "subject": a["q"].get("subject"), "chapter": a["q"].get("chapter"),
                        "reported": qid in reports_by_q, "reportIds": reports_by_q.get(qid, []),
                        "repair": f["repair"], "field": f["field"], "source": f["source"],
                        "certain": bool(f.get("certain")), "severity": f.get("severity"),
                        "quote": f.get("quote"), "detail": f["detail"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--lint", action="store_true")
    ap.add_argument("--only", help="comma-separated report ids (debug; does not write results.json)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    load_env(ROOT / ".env")
    cfg = load_json("engine/config/config.json")
    questions = {q["_id"]: q for q in load_json(cfg["paths"]["questions"])}
    reports = sorted(load_json(cfg["paths"]["reports"]), key=lambda r: r["reportId"])
    if args.lint:
        for qid, q in questions.items():
            for f in lint_question(q):
                print(qid, f["code"], "|", f["detail"])
        return
    if args.only:
        reports = [r for r in reports if r["reportId"] in args.only.split(",")]
    engine = Engine(cfg, LLM(cfg, ROOT, offline=args.offline))
    reports_by_q = {}
    for r in load_json(cfg["paths"]["reports"]):
        reports_by_q.setdefault(r["questionId"], []).append(r["reportId"])
    try:
        wanted = [r["questionId"] for r in reports] + ([] if args.only else list(questions))
        assessments = {}
        for qid in dict.fromkeys(wanted):
            print(f"assess {qid} ({', '.join(reports_by_q.get(qid, ['unreported']))})")
            assessments[qid] = engine.assess(questions[qid])
        rows, traces = [], []
        for r in reports:
            row, tr = engine.triage(r, assessments[r["questionId"]])
            rows.append(row)
            traces.append(tr)
            print(f"{row['reportId']} {row['verdict']:<15} {str(row['repair']):<18} {row['route']:<15} {row['confidence']:.2f}")
    except LLMUnavailable as e:
        print(f"\nSTOPPED: {e}\nNothing was written. Completed calls are cached; re-run to continue.")
        sys.exit(2)
    usage = engine.llm.summary()
    qtrace = {qid: {"key_state": a["state"], "key_check": engine.key_check_text(a), "solve_a": a["sa"], "solve_b": a["sb"],
                    "audit": a["audit"], "findings": [{k: v for k, v in f.items() if k != "edit"} for f in a["findings"]]}
              for qid, a in assessments.items()}
    if args.only:
        print(json.dumps({"rows": rows, "traces": traces, "questions": qtrace}, ensure_ascii=False, indent=1))
        return
    dump = lambda rel, obj: (ROOT / rel).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    dump(cfg["paths"]["results"], rows)
    dump(cfg["paths"]["bonus"], bonus_findings(assessments, rows, reports_by_q, engine))
    dump(cfg["paths"]["trace"], {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                 "prompts": cfg["prompts"], "provider": usage["provider"],
                                 "models": {r: engine.llm.model_for(r) for r in cfg["prompts"]},
                                 "reports": traces, "questions": qtrace})
    dump(cfg["paths"]["usage"], usage)
    print("\nusage:", json.dumps(usage, indent=1))


if __name__ == "__main__":
    main()
