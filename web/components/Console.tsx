"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { Action, AuditEntry, Case, ConsoleData, Status } from "@/lib/types";
import { pretty, ROUTE_LABEL, statusOf, when } from "@/lib/ui";
import { Chip, DiffView, Html, Question } from "./Parts";

const STATUS_LABEL: Record<Status, string> = { pending: "Open", approved: "Approved", edited: "Edited & approved", rejected: "Rejected" };
const ROUTES = ["faculty_review", "auto_apply", "no_change"] as const;
const RANK_HELP =
  "Rank = stakes × students reporting × confidence. Stakes: answer key or unsound 10 · missing data, options, figure 7 · solution 5 · notation, units 4 · chapter 2 · typos, tags 1. Cases that need your decision come first, auto-applied edits next (spot-check), no-change last.";

const noopSubscribe = () => () => {};

type Toast = { msg: string; undo?: string; error?: boolean } | null;

export default function Console({ data }: { data: ConsoleData }) {
  // false on the server and during hydration, true afterwards: stored markup is sanitised in the browser only
  const mounted = useSyncExternalStore(noopSubscribe, () => true, () => false);
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [load, setLoad] = useState<"loading" | "ready" | "error">("loading");
  const [tab, setTab] = useState<"queue" | "found">("queue");
  const [filters, setFilters] = useState({ route: "", verdict: "", subject: "" });
  const [showNoChange, setShowNoChange] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [mobileDetail, setMobileDetail] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<{ replace: string; replies: Record<string, string> }>({ replace: "", replies: {} });
  const [note, setNote] = useState("");
  const [toast, setToast] = useState<Toast>(null);
  const [help, setHelp] = useState(false);
  const [rankOpen, setRankOpen] = useState(false);
  const [actor, setActor] = useState("Faculty");
  const [theme, setTheme] = useState("light");
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchAudit = useCallback(async () => {
    setLoad("loading");
    try {
      const res = await fetch("/api/audit", { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      setEntries((await res.json()).entries);
      setLoad("ready");
    } catch {
      setLoad("error");
    }
  }, []);

  useEffect(() => {
    // Browser-only preferences, read once after hydration so server and client markup match.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTheme(document.documentElement.dataset.theme || "light");
    try {
      setActor(localStorage.getItem("actor") || "Faculty");
    } catch {}
    fetchAudit();
  }, [fetchAudit]);

  const byCase = useMemo(() => {
    const m = new Map<string, AuditEntry[]>();
    for (const e of entries) m.set(e.questionId, [...(m.get(e.questionId) ?? []), e]);
    return m;
  }, [entries]);
  const status = useCallback((id: string) => statusOf(byCase.get(id) ?? []).status, [byCase]);

  const subjects = useMemo(() => [...new Set(data.cases.map((c) => c.subject))].sort(), [data]);
  const verdicts = useMemo(() => [...new Set(data.cases.map((c) => c.verdict))], [data]);
  const cases = useMemo(
    () =>
      data.cases.filter(
        (c) => (!filters.route || c.route === filters.route) && (!filters.verdict || c.verdict === filters.verdict) && (!filters.subject || c.subject === filters.subject),
      ),
    [data, filters],
  );
  const noChangeOpen = showNoChange || filters.route === "no_change";
  const navigable = useMemo(() => cases.filter((c) => c.route !== "no_change" || noChangeOpen), [cases, noChangeOpen]);
  const current = useMemo(() => cases.find((c) => c.questionId === selected) ?? navigable[0] ?? cases[0] ?? null, [cases, navigable, selected]);

  const counts = useMemo(() => {
    const open = (r: string) => data.cases.filter((c) => c.route === r && status(c.questionId) === "pending").length;
    return {
      review: open("faculty_review"),
      auto: open("auto_apply"),
      none: open("no_change"),
      done: data.cases.filter((c) => status(c.questionId) !== "pending").length,
    };
  }, [data, status]);

  const say = useCallback((t: Toast) => {
    setToast(t);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 7000);
  }, []);

  const select = useCallback((id: string, openDetail = true) => {
    setSelected(id);
    setEditing(false);
    setNote("");
    if (openDetail) setMobileDetail(true);
  }, []);

  const beginEdit = useCallback((c: Case) => {
    setDraft({ replace: c.diff?.rawReplace ?? "", replies: Object.fromEntries(c.reports.map((r) => [r.reportId, r.student_reply])) });
    setEditing(true);
  }, []);

  const act = useCallback(
    async (c: Case, action: Action, edited?: { replace: string; replies: Record<string, string> }) => {
      if (load !== "ready") return;
      const cur = statusOf(byCase.get(c.questionId) ?? []).status;
      if (action === "undo" ? cur === "pending" : cur !== "pending") {
        say({ msg: action === "undo" ? "Nothing to undo on this case." : "Already decided. Press u to undo first." });
        return;
      }
      const replies = edited?.replies ?? Object.fromEntries(c.reports.map((r) => [r.reportId, r.student_reply]));
      const before = c.diff ? JSON.stringify({ field: c.diff.field, value: c.diff.rawFind }) : null;
      const after =
        action === "approve" || action === "edit"
          ? JSON.stringify({ ...(c.diff ? { field: c.diff.field, value: edited?.replace ?? c.diff.rawReplace } : {}), replies })
          : null;
      const temp: AuditEntry = {
        id: `tmp-${Date.now()}`, questionId: c.questionId, reportIds: c.reports.map((r) => r.reportId), action, actor,
        at: new Date().toISOString(), before, after, note: note.trim() || null, pending: true,
      };
      setEntries((es) => [...es, temp]);
      setEditing(false);
      setNote("");
      if (action !== "undo") {
        const i = navigable.findIndex((x) => x.questionId === c.questionId);
        const next = [...navigable.slice(i + 1), ...navigable.slice(0, i)].find((x) => status(x.questionId) === "pending");
        say({ msg: `${STATUS_LABEL[action === "approve" ? "approved" : action === "edit" ? "edited" : "rejected"]} · ${c.reports.map((r) => r.reportId).join(", ")}`, undo: c.questionId });
        if (next) setSelected(next.questionId);
      } else say({ msg: "Undone. The case is open again." });
      try {
        const res = await fetch("/api/audit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ questionId: c.questionId, action, actor, before, after, note: temp.note }),
        });
        if (!res.ok) throw new Error(String(res.status));
        const { entry } = await res.json();
        setEntries((es) => es.map((e) => (e.id === temp.id ? entry : e)));
      } catch {
        setEntries((es) => es.filter((e) => e.id !== temp.id));
        setSelected(c.questionId);
        say({ msg: "Not saved: the audit log could not be reached. Your action was rolled back.", error: true });
      }
    },
    [actor, byCase, load, navigable, note, say, status],
  );

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const el = ev.target as HTMLElement;
      if (ev.key === "Escape") {
        setHelp(false);
        setRankOpen(false);
        setEditing(false);
        el.blur?.();
        return;
      }
      if (ev.metaKey || ev.ctrlKey || ev.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return;
      if (ev.key === "?") return setHelp((h) => !h);
      if (tab !== "queue" || !current) return;
      const i = navigable.findIndex((c) => c.questionId === current.questionId);
      if (ev.key === "j" && navigable[i + 1]) select(navigable[i + 1].questionId, false);
      else if (ev.key === "k" && i > 0) select(navigable[i - 1].questionId, false);
      else if (ev.key === "a") act(current, "approve");
      else if (ev.key === "r") act(current, "reject");
      else if (ev.key === "u") act(current, "undo");
      else if (ev.key === "e" && status(current.questionId) === "pending") {
        ev.preventDefault();
        beginEdit(current);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [act, beginEdit, current, navigable, select, status, tab]);

  useEffect(() => {
    if (current) document.getElementById(`q-${current.questionId}`)?.scrollIntoView({ block: "nearest" });
  }, [current]);

  const toggleTheme = () => {
    const t = theme === "dark" ? "light" : "dark";
    setTheme(t);
    document.documentElement.dataset.theme = t;
    try {
      localStorage.setItem("theme", t);
    } catch {}
  };

  const selectCls = "h-8 rounded-md border border-line bg-panel px-2 text-[13px] text-ink";
  const curStatus = current ? status(current.questionId) : "pending";
  const curEntries = current ? byCase.get(current.questionId) ?? [] : [];

  return (
    <div className="flex h-dvh flex-col">
      <header className="border-b border-line bg-panel">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
          <h1 className="text-[15px] font-semibold tracking-tight">
            Report <span className="text-accent">→</span> Fix <span className="font-normal text-mute">· Faculty console</span>
          </h1>
          <dl className="order-3 flex w-full gap-4 text-xs text-mute sm:order-none sm:w-auto" aria-label="Queue counts">
            {([["Needs decision", counts.review], ["Auto-applied", counts.auto], ["No change", counts.none], ["Reviewed", `${counts.done}/${data.cases.length}`]] as const).map(([k, v]) => (
              <div key={k} className="flex items-baseline gap-1.5">
                <dd className="text-[15px] font-semibold text-ink">{v}</dd>
                <dt>{k}</dt>
              </div>
            ))}
          </dl>
          <div className="ml-auto flex items-center gap-2">
            <label className="hidden items-center gap-1.5 text-xs text-mute md:flex">
              Reviewing as
              <input
                value={actor}
                onChange={(e) => {
                  setActor(e.target.value);
                  try {
                    localStorage.setItem("actor", e.target.value);
                  } catch {}
                }}
                className="h-8 w-28 rounded-md border border-line bg-panel px-2 text-[13px] text-ink"
              />
            </label>
            <button onClick={() => setHelp(true)} className="h-8 rounded-md border border-line px-2.5 text-[13px]" title="Keyboard shortcuts">
              <kbd>?</kbd> Keys
            </button>
            <button onClick={toggleTheme} className="h-8 rounded-md border border-line px-2.5 text-[13px]" title="Switch colour theme">
              {mounted && theme === "dark" ? "Light" : "Dark"}
            </button>
          </div>
        </div>
        <p className={`border-t border-line bg-sunk px-4 py-1 text-xs text-mute lg:block ${mobileDetail && tab === "queue" ? "hidden" : ""}`}>
          Precomputed results: the triage engine ran once on {new Date(data.meta.generatedAt).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })} over{" "}
          {data.meta.reports} reports. This console makes no live AI calls. Your decisions are saved to the audit log.
        </p>
        <div className={`flex-wrap items-center gap-2 border-t border-line px-4 py-2 lg:flex ${mobileDetail && tab === "queue" ? "hidden" : "flex"}`}>
          <div role="tablist" aria-label="View" className="flex rounded-md border border-line p-0.5 text-[13px]">
            {(["queue", "found"] as const).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`rounded px-2.5 py-1 ${tab === t ? "bg-sunk font-semibold" : "text-mute"}`}>
                {t === "queue" ? `Queue (${data.cases.length})` : `Also found (${data.bonus.length})`}
              </button>
            ))}
          </div>
          {tab === "queue" && (
            <>
              <select aria-label="Filter by route" className={selectCls} value={filters.route} onChange={(e) => setFilters({ ...filters, route: e.target.value })}>
                <option value="">All routes</option>
                {ROUTES.map((r) => <option key={r} value={r}>{ROUTE_LABEL[r]}</option>)}
              </select>
              <select aria-label="Filter by verdict" className={selectCls} value={filters.verdict} onChange={(e) => setFilters({ ...filters, verdict: e.target.value })}>
                <option value="">All verdicts</option>
                {verdicts.map((v) => <option key={v} value={v}>{pretty(v)}</option>)}
              </select>
              <select aria-label="Filter by subject" className={selectCls} value={filters.subject} onChange={(e) => setFilters({ ...filters, subject: e.target.value })}>
                <option value="">All subjects</option>
                {subjects.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
              <div className="relative">
                <button onClick={() => setRankOpen((o) => !o)} aria-expanded={rankOpen} title={RANK_HELP} className="h-8 rounded-md border border-line px-2.5 text-[13px] text-mute">
                  How is this ranked?
                </button>
                {rankOpen && (
                  <p role="tooltip" className="absolute left-0 top-9 z-20 w-80 max-w-[85vw] rounded-md border border-line bg-panel p-3 text-xs leading-5 shadow-lg">
                    {RANK_HELP}
                  </p>
                )}
              </div>
            </>
          )}
          {load === "loading" && <span className="text-xs text-mute" role="status">Loading audit log…</span>}
        </div>
      </header>

      {load === "error" && (
        <div role="alert" className="flex items-center gap-3 border-b border-line bg-sunk px-4 py-2 text-[13px]">
          <span>Could not load the audit log, so approve, edit and reject are switched off. You can still read every case.</span>
          <button onClick={fetchAudit} className="rounded-md border border-line bg-panel px-2.5 py-1 font-medium">Retry</button>
        </div>
      )}

      {tab === "found" ? (
        <Found data={data} mounted={mounted} openCase={(id) => { setTab("queue"); setFilters({ route: "", verdict: "", subject: "" }); setShowNoChange(true); select(id); }} />
      ) : (
        <div className="flex min-h-0 flex-1">
          <nav aria-label="Review queue" className={`w-full shrink-0 overflow-y-auto border-r border-line bg-panel lg:block lg:w-[380px] ${mobileDetail ? "hidden" : ""}`}>
            {cases.length === 0 && (
              <div className="p-8 text-center text-[13px] text-mute">
                <p className="font-medium text-ink">No cases match these filters.</p>
                <button onClick={() => setFilters({ route: "", verdict: "", subject: "" })} className="mt-2 text-accent underline">Clear filters</button>
              </div>
            )}
            {ROUTES.map((route) => {
              const list = cases.filter((c) => c.route === route);
              if (!list.length) return null;
              const collapsed = route === "no_change" && !noChangeOpen;
              return (
                <section key={route} aria-label={ROUTE_LABEL[route]}>
                  <h2 className="sticky top-0 z-10 flex items-center justify-between border-b border-line bg-sunk px-4 py-1.5 text-xs font-semibold uppercase tracking-wide text-mute">
                    <span>{ROUTE_LABEL[route]} · {list.length}</span>
                    {route === "no_change" && filters.route !== "no_change" && (
                      <button onClick={() => setShowNoChange((s) => !s)} aria-expanded={!collapsed} className="font-medium normal-case text-accent">
                        {collapsed ? "Show" : "Hide"}
                      </button>
                    )}
                  </h2>
                  {!collapsed && (
                    <ul>
                      {list.map((c) => {
                        const st = status(c.questionId);
                        const on = current?.questionId === c.questionId;
                        return (
                          <li key={c.questionId} id={`q-${c.questionId}`}>
                            <button
                              onClick={() => select(c.questionId)}
                              aria-current={on ? "true" : undefined}
                              className={`block w-full border-b border-line px-4 py-2.5 text-left ${on ? "bg-accent-soft" : st !== "pending" ? "bg-sunk" : "hover:bg-sunk"}`}
                            >
                              <span className="flex items-center gap-2">
                                <span className={`text-[13px] font-semibold ${c.keyChange ? "text-key" : ""}`}>{pretty(c.repair ?? c.verdict)}</span>
                                {c.reporters > 1 && <Chip tone="quiet">{c.reporters} students</Chip>}
                                <span className="ml-auto text-xs text-mute" title="Engine confidence">{c.confidence.toFixed(2)}</span>
                              </span>
                              <span className="mt-0.5 block truncate text-[13px]">{c.evidence}</span>
                              <span className="mt-0.5 flex items-center gap-1.5 text-xs text-mute">
                                <span className="truncate">{c.subject} · {c.chapter}</span>
                                {st !== "pending" && <span className="ml-auto shrink-0 font-medium text-ink">{STATUS_LABEL[st]}</span>}
                              </span>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
              );
            })}
          </nav>

          <main className={`min-w-0 flex-1 flex-col lg:flex ${mobileDetail ? "flex" : "hidden"}`}>
            {!mounted ? (
              <div className="space-y-3 p-6" aria-busy="true" aria-label="Loading case">
                {[40, 90, 70, 55].map((w) => <div key={w} className="h-4 animate-pulse rounded bg-sunk" style={{ width: `${w}%` }} />)}
              </div>
            ) : !current ? (
              <p className="p-8 text-center text-[13px] text-mute">Nothing selected.</p>
            ) : (
              <>
                <div className="min-h-0 flex-1 overflow-y-auto">
                  <div className="mx-auto max-w-6xl p-4 lg:p-6">
                    <button onClick={() => setMobileDetail(false)} className="mb-3 text-[13px] font-medium text-accent lg:hidden">← Queue</button>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Chip tone={current.verdict === "defect" ? "accent" : "quiet"} title={data.verdicts[current.verdict]}>{pretty(current.verdict)}</Chip>
                      {current.repair && <Chip tone={current.keyChange ? "key" : "plain"} title={current.repairLabel ?? ""}>{pretty(current.repair)}</Chip>}
                      <Chip tone="plain">{ROUTE_LABEL[current.route]}</Chip>
                      <Chip tone="quiet" title="Engine confidence, 0 to 1">confidence {current.confidence.toFixed(2)}</Chip>
                      <Chip tone="quiet">{current.reporters} {current.reporters === 1 ? "student" : "students"}</Chip>
                      {curStatus !== "pending" && <Chip tone="accent">{STATUS_LABEL[curStatus]}</Chip>}
                      <span className="ml-auto text-xs text-mute">{current.subject} · {current.chapter}</span>
                    </div>
                    <p className="mt-2.5 text-base font-semibold leading-snug">{current.evidence}</p>

                    <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
                      <div className="min-w-0 space-y-4">
                        <section aria-label="Proposed change" className={`rounded-lg border bg-panel p-4 ${current.keyChange ? "border-key" : "border-line"}`}>
                          {current.diff ? (
                            <>
                              <DiffView diff={current.diff} keyChange={current.keyChange} replaceOverride={editing ? draft.replace : undefined} />
                              {editing && (
                                <label className="mt-3 block text-xs font-medium text-mute">
                                  Replacement ({current.diff.kind === "value" ? "JSON value" : "raw markup"})
                                  <textarea
                                    autoFocus
                                    value={draft.replace}
                                    onChange={(e) => setDraft({ ...draft, replace: e.target.value })}
                                    rows={current.diff.kind === "value" ? 1 : 4}
                                    className="mt-1 block w-full rounded-md border border-line bg-bg p-2 font-mono text-xs text-ink"
                                  />
                                </label>
                              )}
                            </>
                          ) : (
                            <p className="text-[13px]">
                              <span className="font-semibold">No edit proposed. </span>
                              {current.verdict !== "defect"
                                ? `${data.verdicts[current.verdict]} The stored question stays as it is; only the reply goes to the student.`
                                : current.repairAuto === false
                                  ? `${current.repairLabel} This repair type is never auto-applied, so it needs a teacher to write the fix.`
                                  : "The engine did not write this edit itself (see the evidence above), so the fix is yours to write."}
                            </p>
                          )}
                        </section>

                        <section aria-label="Question" className="rounded-lg border border-line bg-panel p-4">
                          <Question view={current.before} proposed={current.after} keyChange={current.keyChange} />
                          <details className="mt-4 border-t border-line pt-3">
                            <summary className="cursor-pointer text-[13px] font-medium text-mute">Stored solution</summary>
                            <Html html={current.before.solutionHtml} className="mt-2 overflow-x-auto" />
                          </details>
                          {current.keyCheck && (
                            <details className="mt-3 border-t border-line pt-3">
                              <summary className="cursor-pointer text-[13px] font-medium text-mute">How the key was checked</summary>
                              <p className="mt-2 text-[13px] leading-6">{current.keyCheck}</p>
                            </details>
                          )}
                        </section>

                        {current.alsoFound.length > 0 && (
                          <section aria-label="Also found on this question" className="rounded-lg border border-line bg-panel p-4">
                            <h3 className="text-xs font-semibold uppercase tracking-wide text-mute">Also found on this question · not part of this decision</h3>
                            <ul className="mt-2 space-y-1.5 text-[13px]">
                              {current.alsoFound.map((f, i) => (
                                <li key={i}><span className="font-medium">{pretty(f.repair)}</span> in {f.field}: {f.detail}{f.quote ? ` (“${f.quote}”)` : ""}</li>
                              ))}
                            </ul>
                          </section>
                        )}
                      </div>

                      <div className="min-w-0 space-y-4">
                        <section aria-label="Student reports and replies" className="rounded-lg border border-line bg-panel p-4">
                          <h3 className="text-xs font-semibold uppercase tracking-wide text-mute">Students said · we reply</h3>
                          <ul className="mt-2 space-y-3">
                            {current.reports.map((r) => (
                              <li key={r.reportId} className="text-[13px]">
                                <p className="text-xs text-mute">{r.reportId} · {r.source} · {when(r.reportedAt)}{r.verdict !== current.verdict || r.repair !== current.repair ? ` · ${pretty(r.repair ?? r.verdict)}` : ""}</p>
                                <p className="mt-0.5 border-l-2 border-line pl-2 italic">“{r.comment}”</p>
                                {editing ? (
                                  <textarea
                                    aria-label={`Reply to ${r.reportId}`}
                                    value={draft.replies[r.reportId] ?? ""}
                                    onChange={(e) => setDraft({ ...draft, replies: { ...draft.replies, [r.reportId]: e.target.value } })}
                                    rows={4}
                                    className="mt-1.5 block w-full rounded-md border border-line bg-bg p-2 text-[13px] text-ink"
                                  />
                                ) : (
                                  <p className="mt-1.5 rounded-md bg-sunk p-2">{r.student_reply}</p>
                                )}
                              </li>
                            ))}
                          </ul>
                        </section>

                        <section aria-label="Audit timeline" className="rounded-lg border border-line bg-panel p-4">
                          <h3 className="text-xs font-semibold uppercase tracking-wide text-mute">Audit timeline</h3>
                          <ol className="mt-2 space-y-2 text-[13px]">
                            {current.history.map((h, i) => (
                              <li key={`h${i}`}><span className="font-medium">{h.actor}</span> · {h.at ? when(h.at) : ""} · {h.type}<span className="block text-mute">{h.note}</span></li>
                            ))}
                            <li><span className="font-medium">Engine</span> · {when(data.meta.generatedAt)} · proposed {pretty(current.repair ?? current.verdict)} ({ROUTE_LABEL[current.route].toLowerCase()})</li>
                            {curEntries.map((e) => <Entry key={e.id} e={e} />)}
                            {curEntries.length === 0 && <li className="text-mute">No decision yet.</li>}
                          </ol>
                        </section>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="border-t border-line bg-panel px-4 py-2.5">
                  <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-2">
                    {editing ? (
                      <>
                        <button onClick={() => act(current, "edit", draft)} className="h-9 rounded-md bg-accent px-4 text-[13px] font-semibold text-accent-ink">Save edit & approve</button>
                        <button onClick={() => setEditing(false)} className="h-9 rounded-md border border-line px-3 text-[13px]">Cancel <kbd>Esc</kbd></button>
                      </>
                    ) : curStatus === "pending" ? (
                      <>
                        <button disabled={load !== "ready"} onClick={() => act(current, "approve")} className="h-9 rounded-md bg-accent px-4 text-[13px] font-semibold text-accent-ink disabled:opacity-50">
                          {current.route === "auto_apply" ? "Confirm" : "Approve"} <kbd>a</kbd>
                        </button>
                        <button disabled={load !== "ready"} onClick={() => beginEdit(current)} className="h-9 rounded-md border border-line px-3 text-[13px] font-medium disabled:opacity-50">Edit <kbd>e</kbd></button>
                        <button disabled={load !== "ready"} onClick={() => act(current, "reject")} className="h-9 rounded-md border border-line px-3 text-[13px] font-medium disabled:opacity-50">
                          {current.route === "auto_apply" ? "Revert" : "Reject"} <kbd>r</kbd>
                        </button>
                      </>
                    ) : (
                      <>
                        <span className="text-[13px] font-medium">{STATUS_LABEL[curStatus]}</span>
                        <button disabled={load !== "ready"} onClick={() => act(current, "undo")} className="h-9 rounded-md border border-line px-3 text-[13px] font-medium disabled:opacity-50">Undo <kbd>u</kbd></button>
                      </>
                    )}
                    {curStatus === "pending" && (
                      <input
                        aria-label="Note for the audit log (optional)"
                        placeholder="Note (optional)"
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                        className="h-9 min-w-0 flex-1 rounded-md border border-line bg-bg px-2.5 text-[13px] text-ink placeholder:text-mute"
                      />
                    )}
                    <span className="ml-auto hidden text-xs text-mute lg:block"><kbd>j</kbd> <kbd>k</kbd> next / previous</span>
                  </div>
                </div>
              </>
            )}
          </main>
        </div>
      )}

      {toast && (
        <div role="status" className={`fixed bottom-16 left-1/2 z-30 flex max-w-[92vw] -translate-x-1/2 items-center gap-3 rounded-lg border px-4 py-2.5 text-[13px] shadow-lg ${toast.error ? "border-key bg-key-soft text-key" : "border-line bg-ink text-bg"}`}>
          <span>{toast.msg}</span>
          {toast.undo && (
            <button
              onClick={() => {
                const c = data.cases.find((x) => x.questionId === toast.undo);
                if (c) { setSelected(c.questionId); act(c, "undo"); }
              }}
              className="font-semibold underline"
            >
              Undo
            </button>
          )}
        </div>
      )}

      {help && (
        <div className="fixed inset-0 z-40 grid place-items-center bg-black/40 p-4" onClick={() => setHelp(false)}>
          <div role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" onClick={(e) => e.stopPropagation()} className="w-full max-w-sm rounded-lg border border-line bg-panel p-5">
            <h2 className="text-[15px] font-semibold">Keyboard shortcuts</h2>
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-[13px]">
              {[["j / k", "Next / previous case"], ["a", "Approve (confirm an auto-applied edit)"], ["e", "Edit the fix or the reply, then approve"], ["r", "Reject (revert an auto-applied edit)"], ["u", "Undo the last decision on this case"], ["Esc", "Cancel editing / close"], ["?", "This help"]].map(([k, v]) => (
                <div key={k} className="contents"><dt><kbd>{k}</kbd></dt><dd>{v}</dd></div>
              ))}
            </dl>
            <button autoFocus onClick={() => setHelp(false)} className="mt-4 h-8 rounded-md border border-line px-3 text-[13px]">Close</button>
          </div>
        </div>
      )}
    </div>
  );
}

function Entry({ e }: { e: AuditEntry }) {
  const parse = (s: string | null) => {
    try {
      return s ? (JSON.parse(s) as { field?: string; value?: string }) : null;
    } catch {
      return null;
    }
  };
  const b = parse(e.before);
  const a = parse(e.after);
  const short = (v?: string) => (v == null ? "" : v.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 60) || "(markup)");
  return (
    <li className={e.pending ? "opacity-60" : ""}>
      <span className="font-medium">{e.actor}</span> · {when(e.at)} · <span className="font-semibold">{e.action}</span>
      {e.pending && " · saving…"}
      {a?.field && (e.action === "approve" || e.action === "edit") && (
        <span className="block text-mute">{a.field}: {short(b?.value)} → {short(a.value)}</span>
      )}
      {e.note && <span className="block text-mute">Note: {e.note}</span>}
    </li>
  );
}

function Found({ data, mounted, openCase }: { data: ConsoleData; mounted: boolean; openCase: (id: string) => void }) {
  const groups = useMemo(() => {
    const m = new Map<string, typeof data.bonus>();
    for (const f of data.bonus) m.set(f.questionId, [...(m.get(f.questionId) ?? []), f]);
    return [...m.entries()].sort((x, y) => Number(x[1][0].reported) - Number(y[1][0].reported));
  }, [data]);
  return (
    <main className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl p-4 lg:p-6">
        <h2 className="text-base font-semibold">Also found: defects nobody reported</h2>
        <p className="mt-1 text-[13px] text-mute">
          Found by the lint pass and the audit over all {data.meta.questions} questions. These are leads for a teacher, not decisions: nothing here is applied. Questions with no report at all are listed first.
        </p>
        {groups.length === 0 && <p className="mt-8 text-center text-[13px] text-mute">Nothing extra was found.</p>}
        <ul className="mt-4 space-y-3">
          {groups.map(([qid, fs]) => {
            const unreported = data.unreportedQuestions[qid];
            return (
              <li key={qid} className="rounded-lg border border-line bg-panel p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Chip tone={fs[0].reported ? "quiet" : "accent"}>{fs[0].reported ? `reported · ${fs[0].reportIds.join(", ")}` : "never reported"}</Chip>
                  <span className="text-xs text-mute">{fs[0].subject} · {fs[0].chapter}</span>
                  {fs[0].reported && <button onClick={() => openCase(qid)} className="ml-auto text-[13px] font-medium text-accent underline">Open case</button>}
                </div>
                {unreported && mounted && <Html html={unreported.before.stemHtml} className="mt-2 text-mute" />}
                <ul className="mt-2 space-y-1.5 text-[13px]">
                  {fs.map((f, i) => (
                    <li key={i}>
                      <span className="font-medium">{pretty(f.repair)}</span> in {f.field}: {f.detail}
                      {f.quote ? ` (“${f.quote}”)` : ""} <span className="text-xs text-mute">· {f.source}{f.certain ? ", certain" : ", to confirm"}</span>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ul>
      </div>
    </main>
  );
}
