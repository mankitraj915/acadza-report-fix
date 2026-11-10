"""HTML + MathML -> compact plain text, and option parsing.

The LLM never needs the raw markup to *reason*; it only needs it to write an
edit. So every reasoning prompt gets this text view (about 4x fewer tokens).
"""
import re
from html.parser import HTMLParser

VOID = {"br", "img", "mspace", "hr"}
BLOCK = {"h1", "h2", "h3", "h4", "p", "div", "li", "ol", "ul", "tr", "mtr", "table"}
ARROWS = {"→", "⃗", "⟶"}


class Node:
    def __init__(self, tag, attrs=None):
        self.tag, self.attrs, self.children = tag, attrs or {}, []


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        n = Node(tag, dict(attrs))
        self.stack[-1].children.append(n)
        if tag not in VOID:
            self.stack.append(n)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _render(n):
    if isinstance(n, str):
        return n
    t = n.tag
    kids = [c for c in n.children if not (isinstance(c, str) and not c.strip())]
    r = [_render(c).strip() for c in kids]
    if t == "mfrac" and len(r) == 2:
        return f"({r[0]})/({r[1]})"
    if t == "msup" and len(r) == 2:
        return f"{r[0]}^({r[1]})"
    if t == "msub" and len(r) == 2:
        return f"{r[0]}_({r[1]})"
    if t == "msubsup" and len(r) == 3:
        return f"{r[0]}_({r[1]})^({r[2]})"
    if t == "msqrt":
        return f"sqrt({' '.join(r)})"
    if t == "mover" and len(r) == 2:
        return f"vec({r[0]})" if r[1] in ARROWS else r[0]
    if t == "sup":
        return f"^({''.join(r)})"
    if t == "sub":
        return f"_({''.join(r)})"
    if t == "img":
        return " [IMAGE] "
    if t == "br":
        return "\n"
    if t == "mspace":
        return " "
    inner = "".join(_render(c) for c in n.children)
    if t in ("math", "mtd"):
        return " " + inner + " "
    if t in BLOCK:
        return "\n" + inner + "\n"
    return inner


def to_text(markup):
    """Plain-text view of a question/solution field."""
    if not markup:
        return ""
    p = _Parser()
    p.feed(markup)
    p.close()
    s = _render(p.root)
    s = s.replace(" ", " ").replace("​", "").replace("⁡", "")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)


_MARK = re.compile(r"\(([A-D])\)")


def split_options(text):
    """-> (stem, {letter: option_text}). Uses the LAST '(A)' that is followed
    by '(B)', so 'Assertion (A): ...' inside a stem is not taken as an option."""
    marks = [(m.group(1), m.start(), m.end()) for m in _MARK.finditer(text)]
    start = None
    for i, (letter, _, _) in enumerate(marks):
        if letter == "A" and any(l == "B" for l, _, _ in marks[i + 1:]):
            start = i
    if start is None:
        return text.strip(), {}
    chain, want = [], "A"
    for letter, s, e in marks[start:]:
        if letter == want:
            chain.append((letter, s, e))
            want = chr(ord(want) + 1)
    opts = {}
    for j, (letter, s, e) in enumerate(chain):
        end = chain[j + 1][1] if j + 1 < len(chain) else len(text)
        opts[letter] = re.sub(r"\s+", " ", text[e:end]).strip()
    return text[: chain[0][1]].strip(), opts


def body(q):
    return q.get(q.get("questionType")) or {}
