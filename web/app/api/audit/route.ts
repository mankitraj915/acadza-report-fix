import data from "@/data/console.json";
import { auditRepo, backendName } from "@/lib/db";

export const dynamic = "force-dynamic";

const ACTIONS = new Set(["approve", "edit", "reject", "undo"]);
const CASES = new Map((data.cases as { questionId: string; reports: { reportId: string }[] }[]).map((c) => [c.questionId, c]));
const clip = (v: unknown, n: number) => (typeof v === "string" && v.trim() ? v.slice(0, n) : null);

export async function GET() {
  try {
    const repo = await auditRepo();
    return Response.json({ backend: backendName(), entries: await repo.list() });
  } catch (err) {
    console.error("audit list failed", err);
    return Response.json({ error: "The audit log is unavailable." }, { status: 503 });
  }
}

export async function POST(request: Request) {
  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "Body must be JSON." }, { status: 400 });
  }
  const c = CASES.get(String(body.questionId));
  if (!c) return Response.json({ error: "Unknown questionId." }, { status: 400 });
  if (!ACTIONS.has(String(body.action))) return Response.json({ error: "Unknown action." }, { status: 400 });
  try {
    const repo = await auditRepo();
    const entry = await repo.insert({
      questionId: c.questionId,
      reportIds: c.reports.map((r) => r.reportId),
      action: String(body.action),
      actor: clip(body.actor, 60) ?? "Faculty",
      before: clip(body.before, 8000),
      after: clip(body.after, 8000),
      note: clip(body.note, 500),
    });
    return Response.json({ entry }, { status: 201 });
  } catch (err) {
    console.error("audit insert failed", err);
    return Response.json({ error: "Could not save to the audit log." }, { status: 503 });
  }
}
