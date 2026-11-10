export type Route = "auto_apply" | "faculty_review" | "no_change";
export type Answer = string | string[];

export interface QView {
  stemHtml: string;
  options: { letter: string; html: string }[];
  solutionHtml: string;
  answer: Answer;
  qtype: string;
}

export interface ReportRow {
  reportId: string;
  verdict: string;
  repair: string | null;
  route: Route;
  confidence: number;
  evidence: string;
  student_reply: string;
  comment: string;
  source: string;
  reportedAt: string;
}

export interface Finding {
  questionId: string;
  subject: string;
  chapter: string;
  reported: boolean;
  reportIds: string[];
  repair: string;
  field: string;
  source: string;
  certain: boolean;
  severity: string | null;
  quote: string | null;
  detail: string;
}

export interface Diff {
  field: string;
  kind: "text" | "value";
  beforeText: string;
  afterText: string;
  rawFind: string;
  rawReplace: string;
}

export interface Case {
  questionId: string;
  subject: string;
  chapter: string;
  level: string | null;
  before: QView;
  after: QView | null;
  diff: Diff | null;
  leadReportId: string;
  verdict: string;
  repair: string | null;
  repairLabel: string | null;
  repairAuto: boolean | null;
  route: Route;
  confidence: number;
  evidence: string;
  stakes: number;
  reporters: number;
  score: number;
  keyChange: boolean;
  keyCheck: string | null;
  history: { type?: string; actor?: string; at?: string; note?: string }[];
  reports: ReportRow[];
  alsoFound: Finding[];
}

export interface ConsoleData {
  meta: {
    generatedAt: string;
    provider: string;
    models: Record<string, string>;
    prompts: Record<string, string>;
    reports: number;
    questions: number;
    calls: number;
    promptTokens: number;
    completionTokens: number;
  };
  verdicts: Record<string, string>;
  cases: Case[];
  bonus: Finding[];
  unreportedQuestions: Record<string, { subject: string; chapter: string; before: QView }>;
}

export type Action = "approve" | "edit" | "reject" | "undo";

export interface AuditEntry {
  id: number | string;
  questionId: string;
  reportIds: string[];
  action: Action;
  actor: string;
  at: string;
  before: string | null;
  after: string | null;
  note: string | null;
  pending?: boolean;
}

export type Status = "pending" | "approved" | "edited" | "rejected";
