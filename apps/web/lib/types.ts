/**
 * Shared types mirroring the Duet API contract (http://127.0.0.1:8787/api).
 * Every field the backend documents is represented here so component props
 * never need `any`.
 */

/** Allows documented literals while tolerating values the backend adds later. */
type Open<T extends string> = T | (string & {});

export type RiskTier = Open<'safe' | 'review' | 'sensitive' | 'forbidden'>;
export type Tone = Open<'ok' | 'warn' | 'bad'>;
export type TaskStatus = Open<
  'queued' | 'planning' | 'running' | 'awaiting_approval' | 'blocked' | 'done' | 'failed' | 'cancelled'
>;
/** Steps carry their own vocabulary, not the task's. Verified against `GET /api/tasks/{id}`. */
export type StepStatus = Open<'ok' | 'error' | 'pending' | 'running'>;
export type ApprovalStatus = Open<'pending' | 'approved' | 'executed' | 'rejected' | 'reverted' | 'expired'>;
/** Verified against `GET /api/signals`: the router emits exactly these four. */
export type SignalDisposition = Open<'notify_now' | 'briefing' | 'defer' | 'ignore'>;
export type MessageRole = Open<'user' | 'assistant' | 'system'>;
export type MessageKind = Open<'text' | 'brief' | 'approval' | 'task_update' | 'signal' | 'note'>;
export type RuleKind = Open<'vip' | 'mute' | 'boost' | 'never'>;

export type Health = {
  status: string;
  llm: { ok: boolean; detail: string; provider: string };
  /** The service reports a boolean today; older builds sent a label. */
  db: boolean | string;
  version: string;
};

export type MeCounts = {
  pending_approvals: number;
  unread_signals: number;
  running_tasks: number;
  connections: number;
};

export type Me = {
  id: string;
  email: string;
  name: string;
  timezone: string;
  quiet_hours: QuietHours;
  /** Not always present on /api/me; /api/settings is authoritative. */
  briefing_hour?: number;
  counts: MeCounts;
};

export type QuietHours = { start: number; end: number };

export type MessageMeta = {
  reasons?: string[];
  why?: string;
  score?: number;
  connector?: string;
  connector_key?: string;
  kind?: string;
  [key: string]: unknown;
};

export type ThreadMessage = {
  id: string;
  role: MessageRole;
  kind: MessageKind;
  content: string;
  task_id: string | null;
  approval_id: string | null;
  signal_id: string | null;
  proactive: boolean;
  meta: MessageMeta | null;
  created_at: string;
};

export type ThreadResponse = { messages: ThreadMessage[] };

/** `POST /api/thread` attaches only an identity stub, not a whole task. */
export type ThreadTaskStub = Pick<Task, 'id' | 'title' | 'status'>;

/**
 * `POST /api/thread` always answers with three things: the message it stored,
 * the assistant's `reply`, and a task stub when the turn started one.
 */
export type PostThreadResponse = {
  message: ThreadMessage;
  reply: ThreadMessage;
  task: ThreadTaskStub | null;
};

export type BriefingItem = {
  /** Suggestion items ("What I'd do first") arrive without an id. */
  id?: string;
  title: string;
  score: number;
  detail?: string;
  actor?: string;
  connector?: string;
  kind?: string;
  why?: string;
  when?: string;
  url?: string | null;
  approval_id?: string | null;
  risk?: RiskTier | null;
  action?: string | null;
};

export type BriefingSection = {
  title: string;
  lead: string;
  items: BriefingItem[];
  truncated?: boolean | number;
};

export type Briefing = {
  id: string;
  for_date: string;
  headline: string;
  sections: BriefingSection[];
  created_at: string;
};

export type BriefingResponse = { briefing: Briefing | null };

export type Signal = {
  id: string;
  title: string;
  body: string;
  actor: string;
  connector_key: string;
  kind: string;
  score: number;
  disposition: SignalDisposition;
  state: string;
  reasons: string[];
  factors: Record<string, number | string | boolean | null>;
  occurred_at: string;
  url: string;
};

export type SignalsResponse = { signals: Signal[] };

export type TaskArtifact = {
  label?: string;
  title?: string;
  kind?: string;
  type?: string;
  body?: string;
  url?: string;
  [key: string]: unknown;
};

export type Task = {
  id: string;
  title: string;
  intent: string;
  status: TaskStatus;
  origin: string;
  lead_agent: string;
  created_at: string;
  finished_at: string | null;
  artifacts: TaskArtifact[];
  error: string | null;
};

export type TaskStep = {
  id?: string;
  idx: number;
  agent: string;
  /** Null on planner steps, which call no tool. */
  tool: string | null;
  summary: string;
  status: StepStatus;
  duration_ms: number | null;
  output: unknown;
  error: string | null;
  started_at: string | null;
  finished_at?: string | null;
};

export type TasksResponse = { tasks: Task[] };
export type TaskDetail = { task: Task; steps: TaskStep[] };

export type ApprovalPreview = {
  to?: string | string[];
  cc?: string | string[];
  subject?: string;
  body?: string;
  text?: string;
  summary?: string;
  [key: string]: unknown;
};

export type Approval = {
  id: string;
  title: string;
  rationale: string;
  action_type: string;
  connector_key: string;
  risk_tier: RiskTier;
  preview: ApprovalPreview;
  payload: Record<string, unknown>;
  reversible: boolean;
  status: ApprovalStatus;
  /** Optional: the service omits it when an action never expires. */
  expires_at?: string | null;
  decision_note?: string | null;
  created_at: string;
  task_id: string | null;
};

export type ApprovalsResponse = { approvals: Approval[] };
export type ApprovalActionResult = {
  approval: Approval;
  result?: { ok: boolean; summary: string; data: unknown };
};

export type ConnectorWrite = {
  key: string;
  label: string;
  risk: RiskTier;
  reversible: boolean;
  note: string;
};

export type Connection = {
  id: string;
  status: string;
  last_sync_at: string | null;
  enabled: boolean;
  monitored: boolean;
  account_label: string;
  /** Present on the connect/disconnect responses, absent inside a connector. */
  connector_key?: string;
  scopes?: string[];
  last_error?: string | null;
};

/**
 * The body of `POST /api/connectors/{key}/connect`.
 *
 * `credential` is opaque to the API: it is handed to the vault and only ever
 * resolved inside an adapter. Keep secrets in here and nowhere else — never a
 * query string, never a log line.
 */
export type ConnectRequest = {
  account_label?: string | null;
  grant_write?: boolean;
  credential?: Record<string, string> | null;
};

/** How an app expects to be authenticated. Drives the credential form. */
export type ConnectorAuth = Open<'oauth2' | 'api_key' | 'basic' | 'app_password'>;

/**
 * Whether this app can be connected by signing in, and whether this machine is
 * set up to do it. `oauth: false` is the whole object for apps with no OAuth
 * path, so always narrow on it before reading anything else.
 */
export type OAuthStatus =
  | { oauth: false }
  | {
      oauth: true;
      provider: string;
      /** Provider name as a person would say it: "Google", "Slack". */
      label: string;
      /** A client id and secret exist on this machine. */
      configured: boolean;
      /** Signing in is the ONLY way in - do not also render a token form. */
      required: boolean;
      setup_url: string;
      setup_hint: string;
      /** Must be registered with the provider byte for byte. */
      redirect_uri: string;
      client_id_env: string;
      client_secret_env: string;
      scopes: string[];
    };

export type Connector = {
  key: string;
  name: string;
  category: string;
  color: string;
  /** Letter mark, used wherever there is no real brand logo. */
  glyph: string;
  summary: string;
  auth: ConnectorAuth;
  tags?: string[];
  signal_kinds: string[];
  read_scopes?: string[];
  write_scopes?: string[];
  writes: ConnectorWrite[];
  monitored: boolean;
  /** A real integration exists for this app. */
  has_adapter: boolean;
  /**
   * Whether this app can actually be connected today. `false` means the
   * catalogue lists it but no working integration exists yet.
   *
   * Optional only so a service build that predates the field degrades to
   * `has_adapter` instead of marking all 58 apps unavailable; read it through
   * `isAvailable()` in `lib/connectors.ts`, never directly.
   */
  available?: boolean;
  /** True once a real adapter is talking to the live service. */
  live?: boolean;
  /**
   * Sign-in availability. Optional so a service build that predates the field
   * degrades to the credential form rather than rendering nothing.
   */
  oauth?: OAuthStatus;
  connected: boolean;
  connection: Connection | null;
  signal_count: number;
};

export type ConnectorsResponse = { connectors: Connector[]; categories: string[] };

export type AgentSummary = {
  name: string;
  role: string;
  capabilities: string[];
  status: string;
  runs: number;
  avg_ms: number;
  last_run: string | null;
};

export type AgentsResponse = { agents: AgentSummary[] };

export type DashboardTile = { label: string; value: string | number; detail: string; tone: Tone };
export type SeriesPoint = { name: string; value: number };
export type DashboardSeries = {
  label: string;
  kind: Open<'bar' | 'line'>;
  data: SeriesPoint[];
  note: string;
};
export type DashboardResponse = { tiles: DashboardTile[]; series: DashboardSeries[] };

export type AuditEntry = {
  id: string | number;
  at: string;
  actor: string;
  action: string;
  resource_type: string;
  resource_id: string;
  outcome: string;
  /** The service sends a JSON object here, not a string. */
  detail: unknown;
};

export type AuditResponse = { entries: AuditEntry[] };
export type AuditVerifyResponse = { valid: boolean; checked: number; head: string };

export type PriorityRule = {
  id: string;
  kind: RuleKind;
  pattern: string;
  weight: number;
  note: string;
  enabled: boolean;
};

export type SettingsResponse = {
  quiet_hours: QuietHours;
  briefing_hour: number;
  rules: PriorityRule[];
};

/** One app that refused to sync, as `POST /api/sync` reports it. */
export type SyncFailure = { connector: string; error: string };

/** `POST /api/sync` - check every connected app now. */
export type SyncAllResponse = {
  connections_swept: number;
  new_signals: number;
  notified: number;
  errors: SyncFailure[];
};

/** `POST /api/connectors/{key}/sync` - check one app now. */
export type ConnectorSyncResponse = {
  ok: boolean;
  connector_key: string;
  new_signals: number;
  errors: string[];
};

/**
 * `POST /api/admin/reset` - destructive. Wipes signals, tasks, approvals,
 * messages and memories, and disconnects every app, destroying the stored
 * credentials. `deleted` counts what went, keyed by record type.
 */
export type AdminResetResponse = { ok: boolean; deleted: Record<string, number> };

export type OkResponse = { ok: boolean };
