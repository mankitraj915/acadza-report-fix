"""Edit guards. Shared by the pipeline (before routing) and validate.py (after).

An edit is {field, find, replace}.
  text fields (question / solution): find is a raw-markup substring that must
      occur exactly once; replace is its replacement.
  value fields (answer / questionType): find and replace are JSON VALUES, and
      find must equal the stored value exactly, e.g.
      {"field": "answer", "find": ["", "B"], "replace": "B"}.
"""
import html
import re
import xml.etree.ElementTree as ET

from .textview import body, split_options, to_text

TEXT_FIELDS = ("question", "solution")
VALUE_FIELDS = ("answer", "questionType")
TYPES = ("scq", "mcq", "integerQuestion")
_MATH = re.compile(r"<math\b.*?</math>", re.S)
_ENT = re.compile(r"&(#x?[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")
_TAG = re.compile(r"<[^>]*>")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def _xml_ok(block):
    def fix(m):
        ch = html.unescape(m.group(0))
        return m.group(0) if ch == m.group(0) else "".join(f"&#{ord(c)};" for c in ch)
    try:
        ET.fromstring(_ENT.sub(fix, block))
        return True
    except ET.ParseError:
        return False


def invalid_math_blocks(markup):
    return sum(0 if _xml_ok(b) else 1 for b in _MATH.findall(markup or ""))


def get_field(q, field):
    if field == "questionType":
        return q.get("questionType")
    return body(q).get(field)


def apply_edit(q, edit):
    """Return the new value of edit['field'] (does not mutate q)."""
    if edit["field"] in VALUE_FIELDS:
        return edit["replace"]
    return get_field(q, edit["field"]).replace(edit["find"], edit["replace"], 1)


def valid_answer(qtype, ans, letters):
    if qtype == "scq":
        return isinstance(ans, str) and ans in letters
    if qtype == "mcq":
        return (isinstance(ans, list) and len(ans) >= 1 and ans == sorted(set(ans))
                and all(isinstance(a, str) and a in letters for a in ans))
    if qtype == "integerQuestion":
        return isinstance(ans, str) and re.fullmatch(r"-?\d+(\.\d+)?", ans) is not None
    return False


def _squash(s):
    return re.sub(r"\s+", "", s)


def _is_subsequence(small, big):
    it = iter(big)
    return all(ch in it for ch in small)


def check_edit(q, repair, edit, taxonomy, limits):
    """-> list of violation strings; an empty list means the edit passes."""
    errs = []
    spec = taxonomy["repairs"].get(repair)
    if spec is None:
        return [f"unknown repair '{repair}'"]
    if not isinstance(edit, dict) or set(edit) != {"field", "find", "replace"}:
        return ["edit must be exactly {field, find, replace}"]
    field = edit["field"]
    if field not in spec["fields"]:
        return [f"field '{field}' is outside the scope of {repair} {spec['fields']}"]
    cur = get_field(q, field)
    if edit["find"] == edit["replace"]:
        return ["find equals replace (no-op)"]

    if field in VALUE_FIELDS:
        if edit["find"] != cur:
            errs.append(f"find {edit['find']!r} is not the stored {field} {cur!r}")
        if field == "questionType" and edit["replace"] not in TYPES:
            errs.append("replace is not a known questionType")
        return errs

    find, rep = edit["find"], edit["replace"]
    if not isinstance(find, str) or not isinstance(rep, str) or not find:
        return ["find/replace must be non-empty strings for text fields"]
    n = (cur or "").count(find)
    if n != 1:
        return [f"find occurs {n} times in {field}; must be exactly once"]
    caps = limits["max_find_chars"]
    cap = caps.get(repair, caps["default"])
    if len(find) > cap:
        errs.append(f"find is {len(find)} chars; limit for {repair} is {cap} (not surgical)")
    new = cur.replace(find, rep, 1)
    if invalid_math_blocks(new) > invalid_math_blocks(cur):
        errs.append("edit breaks a MathML block (not well-formed after the edit)")
    if new.count("<math") != new.count("</math>"):
        errs.append("unbalanced <math> tags after the edit")

    old_t, new_t = to_text(cur), to_text(new)
    old_stem, old_opts = split_options(old_t)
    new_stem, new_opts = split_options(new_t)
    if repair == "spelling":
        if _TAG.findall(find) != _TAG.findall(rep):
            errs.append("spelling edit changes markup tags")
        strip = lambda s: re.sub(r"[A-Za-z]+", "", _TAG.sub("", s))
        if strip(find) != strip(rep):
            errs.append("spelling edit changes a digit, symbol or spacing (words only)")
    if repair in ("noise", "exam_reference"):
        if not _is_subsequence(_squash(new_t), _squash(old_t)) or len(new_t) >= len(old_t):
            errs.append(f"{repair} edit must only remove text")
    if repair == "answer_form":
        if not _is_subsequence(_squash(old_t), _squash(new_t)):
            errs.append("answer_form edit must be additive only")
    if repair == "option_repair":
        if old_stem != new_stem:
            errs.append("option_repair edit changes the stem text")
        changed = sorted(k for k in set(old_opts) | set(new_opts) if old_opts.get(k) != new_opts.get(k))
        if len(changed) != 1:
            errs.append(f"option_repair must change exactly one option; changed {changed}")
    if repair == "stem_data":
        sol_nums = set(_NUM.findall(to_text(body(q).get("solution"))))
        added = set(_NUM.findall(new_t)) - set(_NUM.findall(old_t))
        unsupported = sorted(a for a in added if a not in sol_nums)
        if unsupported:
            errs.append(f"stem_data adds value(s) {unsupported} that the stored solution does not state")
    if field == "question" and repair != "option_repair" and old_opts != new_opts:
        if repair in ("stem_data", "answer_form", "noise", "exam_reference") or set(old_opts) != set(new_opts):
            errs.append(f"{repair} edit changes the options")
    return errs
