"use client";

import DOMPurify from "dompurify";
import { useMemo, useState } from "react";
import type { Diff, QView } from "@/lib/types";
import { letters, showAnswer, withContext, wordDiff } from "@/lib/ui";

/** Stored markup is untrusted: sanitise, allow MathML, drop inline styles (they hard-code black text). */
export function Html({ html, className = "" }: { html: string; className?: string }) {
  const clean = useMemo(
    () =>
      typeof window === "undefined"
        ? ""
        : DOMPurify.sanitize(html, { USE_PROFILES: { html: true, mathMl: true }, FORBID_ATTR: ["style", "class", "id"], FORBID_TAGS: ["style", "form", "input"] }),
    [html],
  );
  return <div className={`qhtml ${className}`} dangerouslySetInnerHTML={{ __html: clean }} />;
}

export function Chip({ children, tone = "plain", title }: { children: React.ReactNode; tone?: "plain" | "accent" | "key" | "quiet"; title?: string }) {
  const tones = {
    plain: "border-line bg-panel text-ink",
    accent: "border-transparent bg-accent-soft text-accent",
    key: "border-transparent bg-key-soft text-key",
    quiet: "border-line bg-sunk text-mute",
  };
  return (
    <span title={title} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

/** Question as the student sees it, with the stored key (✓) and the proposed key (→) marked on the options. */
export function Question({ view, proposed, keyChange }: { view: QView; proposed: QView | null; keyChange: boolean }) {
  const cur = letters(view.answer);
  const next = keyChange && proposed ? letters(proposed.answer) : [];
  const isOptions = view.options.length > 0;
  return (
    <div>
      <Html html={view.stemHtml} />
      {isOptions ? (
        <ul className="mt-3 space-y-1.5">
          {view.options.map((o) => {
            const isCur = cur.includes(o.letter);
            const isNext = next.includes(o.letter);
            const dropped = keyChange && isCur && !isNext;
            const blank = !o.html.replace(/<[^>]*>|&nbsp;|\s/g, "");
            return (
              <li
                key={o.letter}
                className={`flex items-start gap-2.5 rounded-md border px-2.5 py-1.5 ${
                  isNext && !isCur ? "border-key bg-key-soft" : isCur && !dropped ? "border-accent bg-accent-soft" : "border-line"
                }`}
              >
                <span className="mt-0.5 w-5 shrink-0 text-sm font-semibold text-mute">{o.letter}</span>
                <div className={`min-w-0 flex-1 ${dropped ? "line-through decoration-key/70" : ""}`}>
                  {blank ? <span className="text-sm italic text-mute">blank in the stored record</span> : <Html html={o.html} />}
                </div>
                {isCur && (
                  <span className={`shrink-0 text-xs font-semibold ${dropped ? "text-mute" : "text-accent"}`}>
                    ✓ {dropped ? "stored key" : keyChange ? "key (kept)" : "key"}
                  </span>
                )}
                {isNext && !isCur && <span className="shrink-0 text-xs font-semibold text-key">→ proposed key</span>}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="mt-3 text-sm">
          <span className="text-mute">Stored answer </span>
          <span className={`font-semibold ${keyChange ? "text-mute line-through decoration-key/70" : "text-accent"}`}>{showAnswer(view.answer)}</span>
          {keyChange && proposed && (
            <>
              <span className="text-key"> → </span>
              <span className="font-semibold text-key">{showAnswer(proposed.answer)}</span>
            </>
          )}
        </p>
      )}
    </div>
  );
}

// "\"C\"" -> C ; ["A","B"] -> A, B
const plain = (json: string) => {
  try {
    const v = JSON.parse(json);
    return Array.isArray(v) ? v.map((x) => x || "(empty)").join(", ") : String(v);
  } catch {
    return json;
  }
};

/** Before/after of the proposed edit: word diff on the readable text, with a raw-markup toggle. */
export function DiffView({ diff, keyChange, replaceOverride }: { diff: Diff; keyChange: boolean; replaceOverride?: string }) {
  const [raw, setRaw] = useState(false);
  const toks = useMemo(
    () => (raw ? wordDiff(diff.rawFind, replaceOverride ?? diff.rawReplace) : withContext(wordDiff(diff.beforeText, diff.afterText))),
    [diff, raw, replaceOverride],
  );
  const ins = keyChange ? "bg-key-soft text-key" : "bg-accent-soft text-accent";
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-xs font-medium uppercase tracking-wide text-mute">
          Change in <span className="text-ink">{diff.field}</span>
        </p>
        <div role="group" aria-label="Diff view" className="flex rounded-md border border-line p-0.5 text-xs">
          {[false, true].map((v) => (
            <button
              key={String(v)}
              onClick={() => setRaw(v)}
              aria-pressed={raw === v}
              className={`rounded px-2 py-0.5 ${raw === v ? "bg-sunk font-semibold text-ink" : "text-mute"}`}
            >
              {v ? "Raw markup" : "Rendered"}
            </button>
          ))}
        </div>
      </div>
      {diff.kind === "value" && !raw ? (
        <p className="text-lg font-semibold">
          <span className="text-mute line-through decoration-key/70">{plain(diff.beforeText)}</span>
          <span className={keyChange ? "text-key" : "text-accent"}> → {plain(replaceOverride ?? diff.afterText)}</span>
        </p>
      ) : (
        <p className={`whitespace-pre-wrap break-words leading-7 ${raw ? "font-mono text-xs leading-6" : "text-[15px]"}`}>
          {toks.map((t, i) =>
            t === null ? (
              <span key={i} className="text-mute"> … </span>
            ) : t.op === "same" ? (
              <span key={i}>{t.t} </span>
            ) : t.op === "del" ? (
              <del key={i} className="rounded bg-sunk px-0.5 text-mute">{t.t}</del>
            ) : (
              <ins key={i} className={`rounded px-0.5 font-semibold no-underline ${ins}`}>{t.t}</ins>
            ),
          ).flatMap((el, i) => [el, <span key={`s${i}`}> </span>])}
        </p>
      )}
    </div>
  );
}
