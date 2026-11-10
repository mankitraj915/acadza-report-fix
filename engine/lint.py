"""Deterministic checks. No LLM. Runs on every question, reported or not.

Each finding: {code, repair, field, detail, certain, edit?}
  certain=True  -> the check itself is proof (duplicate options, exam tag...).
  certain=False -> a lead that a later step must confirm (e.g. numbers differ).
"""
import re

from .textview import body, split_options, to_text

LATEX = re.compile(r"\\(?:frac|sqrt|times|alpha|beta|gamma|theta|pi|mu|AA|left|right|cdot|text|mathrm)\b|\$\$|\\\(|\\\[")
FRAC = re.compile(r"\\frac\{([^{}]+)\}\{([^{}]+)\}")
EXAM = re.compile(r"\(\s*(?:JEE|NEET|AIEEE|AIIMS|IIT|AIPMT|MHT|CET|KCET|BITSAT)[^()]{0,30}?(?:19|20)\d{2}\s*\)\s*", re.I)
NOISE = re.compile(r"space for rough work|^\s*(?:Q\.?\s*\d+|\d+[.)])\s", re.I)
FIGURE = re.compile(r"\b(?:figure|fig\.|diagram|shown below|as shown|graph shown|circuit shown)\b", re.I)
NUM = re.compile(r"-?\d+(?:\.\d+)?")
LETTER_IN_SOL = re.compile(r"(?:answer|option)\s*(?:is|:)?\s*\(?([A-Da-d])\)", re.I)


def norm_key(qtype, ans):
    """-> (normalized, malformed). scq/mcq -> frozenset of letters; integer -> float."""
    if qtype == "integerQuestion":
        try:
            return float(ans), not (isinstance(ans, str) and re.fullmatch(r"-?\d+(\.\d+)?", ans))
        except (TypeError, ValueError):
            return None, True
    raw = ans if isinstance(ans, list) else [ans]
    letters = frozenset(re.findall(r"[A-D]", " ".join(str(a) for a in raw).upper()))
    if qtype == "scq":
        ok = isinstance(ans, str) and re.fullmatch(r"[A-D]", ans) is not None
    else:
        ok = isinstance(ans, list) and ans and all(isinstance(a, str) and re.fullmatch(r"[A-D]", a) for a in ans) \
            and ans == sorted(set(ans))
    return (letters or None), not ok


def canonical_answer(qtype, norm):
    if qtype == "scq":
        return sorted(norm)[0]
    if qtype == "mcq":
        return sorted(norm)
    return str(int(norm)) if float(norm).is_integer() else str(norm)


def lint_question(q):
    qtype = q.get("questionType")
    b = body(q)
    out = []

    def add(code, repair, field, detail, certain, edit=None):
        f = {"code": code, "repair": repair, "field": field, "detail": detail, "certain": certain}
        if edit:
            f["edit"] = edit
        out.append(f)

    if not b:
        add("no_body", "type_mismatch", "questionType", f"no body stored under '{qtype}'", True)
        return out
    raw_q, raw_s = b.get("question") or "", b.get("solution") or ""
    tq, ts = to_text(raw_q), to_text(raw_s)
    stem, opts = split_options(tq)
    key, malformed = norm_key(qtype, b.get("answer"))

    for field, raw, txt in (("question", raw_q, tq), ("solution", raw_s, ts)):
        m = LATEX.search(txt)
        if m:
            edit = None
            fm = FRAC.search(raw)
            if fm and raw.count(fm.group(0)) == 1 and fm.group(1).strip().isdigit() and fm.group(2).strip().isdigit():
                edit = {"field": field, "find": fm.group(0), "replace":
                        '<math xmlns="http://www.w3.org/1998/Math/MathML" display="inline"><mfrac><mn>%s</mn><mn>%s</mn></mfrac></math>'
                        % (fm.group(1).strip(), fm.group(2).strip())}
            add("markup_leak", "markup_leak", field, f"raw LaTeX visible as text: '{txt[max(0, m.start() - 12): m.end() + 12]}'", True, edit)
        m = EXAM.search(raw)
        if m and field == "question":
            edit = {"field": field, "find": m.group(0), "replace": ""} if raw.count(m.group(0)) == 1 else None
            add("exam_tag", "exam_reference", field, f"exam tag in text: '{m.group(0).strip()}'", True, edit)
        if NOISE.search(txt):
            add("noise_text", "noise", field, "stray numbering / rough-work text", False)
        if re.search(r"<ol>\s*<li>", raw) and len(re.findall(r"<li>", raw)) == 1:
            add("stray_list_number", "noise", field, "single <ol><li> wrapper renders a stray '1.' before the text", True)
        if re.search(r"<msqrt>(?:(?!</msqrt>).)*<mtext>(?:&#x000A0;|&nbsp;|\s)*</mtext>\s*<mi mathvariant=\"normal\">", raw, re.S):
            add("unit_inside_root", "notation", field, "a unit sits inside a square root, e.g. sqrt(2 m) instead of sqrt(2) m", True)

    if qtype in ("scq", "mcq"):
        letters = sorted(opts)
        expected = ["A", "B", "C", "D"]
        missing = [l for l in expected if l not in opts]
        blank = [l for l in letters if not opts[l]]
        if missing:
            add("option_missing", "option_repair", "question", f"option(s) {missing} missing; only {letters} present", True)
        if blank:
            add("option_blank", "option_repair", "question", f"option(s) {blank} blank", True)
        seen = {}
        for l in letters:
            k = re.sub(r"\s+", "", opts[l]).lower()
            if k and k in seen:
                add("options_identical", "identical_options", "question",
                    f"options {seen[k]} and {l} are identical ('{opts[l]}')", True)
            seen.setdefault(k, l)
        if key and any(l in missing or l in blank for l in key):
            add("key_points_to_empty", "option_repair", "question",
                f"the keyed option {sorted(key)} is blank or missing", True)
    if malformed:
        add("key_malformed", "answer_key", "answer", f"stored key {b.get('answer')!r} is not a well-formed {qtype} answer", True)
    if qtype == "mcq" and key and len(key) == 1:
        add("mcq_single_answer", "type_mismatch", "questionType",
            f"stored as mcq but the key has a single option {sorted(key)}", False)
    if qtype == "integerQuestion" and opts:
        add("integer_has_options", "type_mismatch", "questionType", "stored as integer but options are present", False)

    has_img = bool(b.get("quesImages")) or "<img" in raw_q
    if FIGURE.search(tq) and not has_img:
        add("figure_missing", "diagram", "question", "text refers to a figure but no image is stored", True)
    if len(ts) < 15:
        add("solution_missing", "solution_missing", "solution", "solution is empty or near-empty", True)

    # solution-final vs key (numeric lead only; unit conversions can make this a false lead)
    # "final" = first number after the last '=' (avoids picking up an exponent)
    nums = NUM.findall(ts.replace(",", "").rsplit("=", 1)[-1])[:1]
    if nums and key is not None:
        last = abs(float(nums[-1]))
        if qtype == "integerQuestion":
            if abs(last - key) > 1e-9:
                add("solution_final_differs", "answer_key", "answer",
                    f"last number in solution is {nums[-1]}; key is {b.get('answer')}", False)
        elif qtype == "scq":
            match = [l for l, t in opts.items() if NUM.findall(t.replace(",", "")) and
                     abs(float(NUM.findall(t.replace(",", ""))[0]) - last) < 1e-9]
            if len(match) == 1 and match[0] not in key:
                add("solution_final_differs", "answer_key", "answer",
                    f"solution ends at {nums[-1]} = option {match[0]}; key is {sorted(key)[0]}", False)
    m = LETTER_IN_SOL.search(ts)
    if m and qtype == "scq" and key and m.group(1).upper() not in key:
        add("solution_letter_differs", "answer_key", "answer",
            f"solution names option {m.group(1).upper()}; key is {sorted(key)[0]}", False)
    return out


INJECTION = re.compile(
    r"ignore (?:all |any )?(?:previous|prior|above) instructions|you are (?:now )?(?:the |an? )?(?:admin|system|developer)"
    r"|system prompt|mark this (?:report )?as|change the answer to", re.I)


def lint_comment(comment):
    c = (comment or "").strip()
    flags = []
    if INJECTION.search(c):
        flags.append("instruction_attempt")
    if len(re.findall(r"[A-Za-z\u0900-\u097f]{2,}", c)) == 0:
        flags.append("no_words")
    return flags
