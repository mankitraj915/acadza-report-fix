// Audit log storage. One interface, two backends:
//   DATABASE_URL set  -> Postgres (Neon, over HTTP; works on Vercel functions)
//   otherwise         -> a local SQLite file (node:sqlite, no native build)
// The log is append-only. "Undo" is itself an entry, never a delete.
import type { AuditEntry } from "./types";

export interface NewEntry {
  questionId: string;
  reportIds: string[];
  action: string;
  actor: string;
  before: string | null;
  after: string | null;
  note: string | null;
}

interface AuditRepo {
  list(): Promise<AuditEntry[]>;
  insert(e: NewEntry): Promise<AuditEntry>;
}

type Row = {
  id: number;
  question_id: string;
  report_ids: string;
  action: string;
  actor: string;
  at: string | Date;
  before_value: string | null;
  after_value: string | null;
  note: string | null;
};

const toEntry = (r: Row): AuditEntry => ({
  id: Number(r.id),
  questionId: r.question_id,
  reportIds: JSON.parse(r.report_ids),
  action: r.action as AuditEntry["action"],
  actor: r.actor,
  at: new Date(r.at).toISOString(),
  before: r.before_value,
  after: r.after_value,
  note: r.note,
});

async function postgres(url: string): Promise<AuditRepo> {
  const { neon } = await import("@neondatabase/serverless");
  const sql = neon(url);
  await sql`CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    question_id TEXT NOT NULL,
    report_ids TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    before_value TEXT,
    after_value TEXT,
    note TEXT
  )`;
  return {
    async list() {
      const rows = (await sql`SELECT * FROM audit_log ORDER BY id`) as Row[];
      return rows.map(toEntry);
    },
    async insert(e) {
      const rows = (await sql`INSERT INTO audit_log (question_id, report_ids, action, actor, before_value, after_value, note)
        VALUES (${e.questionId}, ${JSON.stringify(e.reportIds)}, ${e.action}, ${e.actor}, ${e.before}, ${e.after}, ${e.note})
        RETURNING *`) as Row[];
      return toEntry(rows[0]);
    },
  };
}

async function sqlite(): Promise<AuditRepo> {
  const { DatabaseSync } = await import("node:sqlite");
  const path = await import("node:path");
  const db = new DatabaseSync(path.join(process.cwd(), "audit.sqlite"));
  db.exec(`CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL,
    report_ids TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    at TEXT NOT NULL,
    before_value TEXT,
    after_value TEXT,
    note TEXT
  )`);
  return {
    async list() {
      return (db.prepare("SELECT * FROM audit_log ORDER BY id").all() as unknown as Row[]).map(toEntry);
    },
    async insert(e) {
      const row = db
        .prepare(
          `INSERT INTO audit_log (question_id, report_ids, action, actor, at, before_value, after_value, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING *`,
        )
        .get(e.questionId, JSON.stringify(e.reportIds), e.action, e.actor, new Date().toISOString(), e.before, e.after, e.note);
      return toEntry(row as unknown as Row);
    },
  };
}

let repo: Promise<AuditRepo> | null = null;

export function auditRepo(): Promise<AuditRepo> {
  if (!repo) {
    const url = process.env.DATABASE_URL;
    repo = (url ? postgres(url) : sqlite()).catch((err) => {
      repo = null; // let the next request retry instead of caching a failed connection
      throw err;
    });
  }
  return repo;
}

export const backendName = () => (process.env.DATABASE_URL ? "postgres" : "sqlite");
