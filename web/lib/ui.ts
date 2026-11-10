import type { Answer, AuditEntry, Status } from "./types";

export const ROUTE_LABEL: Record<string, string> = {
  faculty_review: "Needs your decision",
  auto_apply: "Auto-applied",
  no_change: "No change",
};

export const pretty = (s: string | null) => (s ? s.replace(/_/g, " ") : "");

export const letters = (a: Answer | undefined | null): string[] =>
  a == null ? [] : (Array.isArray(a) ? a.join(" ") : String(a)).toUpperCase().match(/\b[A-D]\b/g) ?? [];

export const showAnswer = (a: Answer) => (Array.isArray(a) ? `[${a.map((x) => `"${x}"`).join(", ")}]` : String(a));

/** Latest decision on a case. "undo" removes the most recent decision that is still standing. */
export function statusOf(entries: AuditEntry[]): { status: Status; last: AuditEntry | null } {
  const stack: AuditEntry[] = [];
  for (const e of entries) {
    if (e.action === "undo") stack.pop();
    else stack.push(e);
  }
  const last = stack.at(-1) ?? null;
  const status: Status = !last ? "pending" : last.action === "approve" ? "approved" : last.action === "edit" ? "edited" : "rejected";
  return { status, last };
}

export type Tok = { t: string; op: "same" | "del" | "ins" };

/** Word-level diff (LCS). Inputs are short (one question field). */
export function wordDiff(a: string, b: string): Tok[] {
  const x = a.split(/(\s+)/).filter((s) => s.trim());
  const y = b.split(/(\s+)/).filter((s) => s.trim());
  const n = x.length;
  const m = y.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--) dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: Tok[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) {
      out.push({ t: x[i], op: "same" });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ t: x[i++], op: "del" });
    else out.push({ t: y[j++], op: "ins" });
  }
  while (i < n) out.push({ t: x[i++], op: "del" });
  while (j < m) out.push({ t: y[j++], op: "ins" });
  return out;
}

/** Keep `keep` words of context around each change; collapse the rest to an ellipsis. */
export function withContext(toks: Tok[], keep = 9): (Tok | null)[] {
  const near = new Array(toks.length).fill(false);
  toks.forEach((t, i) => {
    if (t.op !== "same") for (let k = Math.max(0, i - keep); k <= Math.min(toks.length - 1, i + keep); k++) near[k] = true;
  });
  const out: (Tok | null)[] = [];
  toks.forEach((t, i) => {
    if (near[i]) out.push(t);
    else if (out.at(-1) !== null) out.push(null);
  });
  return out;
}

export const when = (iso: string) =>
  new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
