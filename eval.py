"""Scores results.json against hand labels.

    python eval.py [--note "what changed"]

labels.json is written by a human: [{"reportId", "verdict", "repair", "route"}, ...]
(optionally "answer": the correct key after the fix). Rows without a label are skipped
and counted. Each run appends one line to runs/eval_history.jsonl with the prompt
versions in use, so every prompt change has a measured delta.

unsafe auto-apply = the engine auto-applied where the label says it should not have
(label route is not auto_apply), or auto-applied a different repair / a different key.
"""
import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--note", default="")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "engine/config/config.json").read_text(encoding="utf-8"))
    lp = ROOT / cfg["paths"]["labels"]
    if not lp.exists():
        print(f"No {cfg['paths']['labels']} yet. Nothing scored (no ground truth is invented here).")
        sys.exit(1)
    labels = {l["reportId"]: l for l in json.loads(lp.read_text(encoding="utf-8"))}
    rows = json.loads((ROOT / cfg["paths"]["results"]).read_text(encoding="utf-8"))
    scored = [r for r in rows if r["reportId"] in labels]
    if not scored:
        print("labels.json has no matching reportIds.")
        sys.exit(1)
    acc, misses = {}, []
    for field in ("verdict", "repair", "route"):
        have = [r for r in scored if field in labels[r["reportId"]]]
        ok = [r for r in have if r[field] == labels[r["reportId"]][field]]
        acc[field] = (len(ok), len(have))
        misses += [(r["reportId"], field, r[field], labels[r["reportId"]][field]) for r in have if r not in ok]
    all3 = [r for r in scored if all(r[f] == labels[r["reportId"]].get(f, r[f]) for f in ("verdict", "repair", "route"))]
    unsafe = []
    for r in scored:
        l = labels[r["reportId"]]
        if r["route"] != "auto_apply":
            continue
        if l.get("route", "auto_apply") != "auto_apply" or l.get("repair", r["repair"]) != r["repair"]:
            unsafe.append((r["reportId"], f"auto-applied {r['repair']}; label says {l.get('verdict')}/{l.get('repair')}/{l.get('route')}"))
        elif "answer" in l and r["edit"] and r["edit"]["field"] == "answer" and r["edit"]["replace"] != l["answer"]:
            unsafe.append((r["reportId"], f"auto-applied key {r['edit']['replace']!r}; label key is {l['answer']!r}"))
    print(f"scored {len(scored)} of {len(rows)} rows ({len(rows) - len(scored)} unlabeled, skipped)")
    for f, (ok, n) in acc.items():
        print(f"  {f:<8} {ok}/{n} = {ok / n:.1%}" if n else f"  {f:<8} no labels")
    print(f"  all three correct: {len(all3)}/{len(scored)} = {len(all3) / len(scored):.1%}")
    print(f"  unsafe auto-applies: {len(unsafe)} (target 0)")
    for rid, why in unsafe:
        print(f"    !! {rid}: {why}")
    for rid, field, got, want in sorted(misses):
        print(f"    {rid} {field}: got {got}, label {want}")
    rec = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "prompts": cfg["prompts"],
           "thresholds": cfg["thresholds"]["auto_apply"], "scored": len(scored),
           **{f: round(ok / n, 4) if n else None for f, (ok, n) in acc.items()},
           "all_three": round(len(all3) / len(scored), 4), "unsafe_auto": len(unsafe),
           "misses": [f"{a}.{b}" for a, b, _, _ in sorted(misses)], "note": args.note}
    with (ROOT / "runs/eval_history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    main()
