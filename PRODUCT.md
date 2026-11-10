# PRODUCT.md

> Draft written with AI help; the positions are mine to rewrite before submission.

## Who loses the most when a question is broken

The student who gets it **right** and is marked wrong, and never reports it. A wrong key (R001, R004, R005) silently
costs every correct student marks and, worse, teaches them to distrust a method that works, weeks before JEE/NEET.
The reporter is the lucky one: they noticed. The second loser is the faculty member, who reads 44 comments by hand
to find the 5 that matter.

How the design reflects that:

- **Stakes rank the queue, not arrival order.** Rank = stakes × students reporting × confidence. Answer-key and
  unsound questions sit at the top; typos sit at the bottom.
- **The verdict comes from checks, not from the comment.** Two blind solves and the stored solution decide the key,
  so "wrong ans" (R002) and a detailed derivation (R003) reach the same fix, and a confident wrong student (R028,
  R029) changes nothing.
- **Every defect is looked for, reported or not.** The lint and audit run on all 43 questions; what nobody reported
  goes to "Also found" for a teacher, separate from report decisions.
- **The reply teaches.** A student who is wrong gets the one decisive step, in the language they wrote in.

## What the AI is never allowed to auto-apply

1. **Anything the taxonomy marks `auto: false`** (diagram, identical options, unsound, wrong question, wrong
   chapter, syllabus). Enforced in code and re-checked by `validate.py`; the model cannot override it.
2. **A key change that is not independently proven.** The key edit is built in code, only when two blind solves
   (different model families, never shown the key or the comment) agree with each other *and* with the stored
   solution, and differ from the key. Anything less goes to a teacher (R005 did, on the first run).
3. **Authored content.** A missing distractor (R009/R010) or a replacement for a duplicate option is new exam
   content. The model may say what is missing; a teacher writes it.
4. **Anything a student's comment asks for.** The comment is data. It never reaches the solver, the auditor or the
   edit writer, so "change the answer to C" (R027) has nothing to act on.

Why: a wrong auto-edit is worse than a slow correct one. It converts one broken question into a broken question
that the system has vouched for.

## One metric for week 1

**Unsafe auto-applies caught by faculty spot-check (target 0), read next to median time-to-fix for answer-key
defects.** The first is the cost of automation, the second is its benefit; either alone can be gamed (auto-apply
nothing, or auto-apply everything). The console already records both: every Confirm/Revert on an auto-applied edit
is an audit row with a timestamp.

## One idea beyond the brief

**Use attempts, not just reports.** Students who report are a small, vocal sample. Response data is the full one:
a question where strong students (top quartile on the chapter) pick the same "wrong" option far more often than the
key is probably mis-keyed; a question nearly everyone skips probably has missing data or a missing figure. Run the
same blind-solve check on those questions nightly and put them in the same queue, ranked by the number of students
affected. That finds the broken question before the first student is annoyed enough to type "ans galat hai".
