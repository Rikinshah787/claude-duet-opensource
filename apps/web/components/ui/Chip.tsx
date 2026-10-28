import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { titleCase } from '@/lib/format';
import type { ApprovalStatus, RiskTier, SignalDisposition, TaskStatus, Tone } from '@/lib/types';

export function Chip({
  children,
  className,
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex max-w-full items-center gap-1.5 truncate rounded-md border border-hairline',
        'bg-raised px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-ink-muted',
        className,
      )}
    >
      {children}
    </span>
  );
}

const RISK_STYLES: Record<string, string> = {
  safe: 'text-risk-safe border-risk-safe/35 bg-risk-safe/10',
  review: 'text-risk-review border-risk-review/35 bg-risk-review/10',
  sensitive: 'text-risk-sensitive border-risk-sensitive/35 bg-risk-sensitive/10',
  forbidden: 'text-risk-forbidden border-risk-forbidden/45 bg-risk-forbidden/15',
};

const RISK_WORDS: Record<string, string> = {
  safe: 'Safe · auto-safe',
  review: 'Review · needs a look',
  sensitive: 'Sensitive · leaves the building',
  forbidden: 'Forbidden · blocked',
};

/** Risk is never colour-only: the tier is always spelled out. */
export function RiskChip({ tier, verbose = false }: { tier: RiskTier; verbose?: boolean }) {
  const key = String(tier);
  return (
    <Chip className={cn(RISK_STYLES[key] ?? '')} title={RISK_WORDS[key] ?? key}>
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />
      {verbose ? (RISK_WORDS[key] ?? titleCase(key)) : titleCase(key)}
    </Chip>
  );
}

const TASK_STYLES: Record<string, string> = {
  queued: 'text-ink-muted',
  planning: 'text-risk-review border-risk-review/30 bg-risk-review/10',
  running: 'text-accent border-accent/35 bg-accent/10',
  awaiting_approval: 'text-risk-review border-risk-review/35 bg-risk-review/10',
  blocked: 'text-risk-sensitive border-risk-sensitive/35 bg-risk-sensitive/10',
  done: 'text-risk-safe border-risk-safe/35 bg-risk-safe/10',
  failed: 'text-risk-sensitive border-risk-sensitive/35 bg-risk-sensitive/10',
  cancelled: 'text-ink-faint',
};

export function StatusChip({ status }: { status: TaskStatus | ApprovalStatus | string }) {
  const key = String(status);
  return <Chip className={cn(TASK_STYLES[key] ?? '')}>{titleCase(key)}</Chip>;
}

/**
 * What the router decided to do with a signal. The backend emits exactly
 * `notify_now | briefing | defer | ignore` — spell them out rather than
 * printing the raw enum at a person.
 */
const DISPOSITION_WORDS: Record<string, string> = {
  notify_now: 'Notify now',
  briefing: 'In the brief',
  defer: 'Deferred',
  ignore: 'Ignored',
};

const DISPOSITION_TITLES: Record<string, string> = {
  notify_now: 'Worth interrupting you for — I reached out.',
  briefing: 'Not urgent: it goes in the next morning brief.',
  defer: 'Parked. It stays here but I will not bring it up.',
  ignore: 'Below the bar — noted and dropped.',
};

const DISPOSITION_STYLES: Record<string, string> = {
  notify_now: 'text-accent border-accent/35 bg-accent/10',
  briefing: '',
  defer: 'bg-transparent text-ink-faint',
  ignore: 'bg-transparent text-ink-faint',
};

export function dispositionLabel(disposition: SignalDisposition): string {
  const key = String(disposition);
  return DISPOSITION_WORDS[key] ?? titleCase(key);
}

export function DispositionChip({ disposition }: { disposition: SignalDisposition }) {
  const key = String(disposition);
  return (
    <Chip className={cn(DISPOSITION_STYLES[key] ?? '')} title={DISPOSITION_TITLES[key] ?? key}>
      {dispositionLabel(disposition)}
    </Chip>
  );
}

const TONE_TEXT: Record<string, string> = {
  ok: 'text-risk-safe',
  warn: 'text-risk-review',
  bad: 'text-risk-sensitive',
};

export function toneText(tone: Tone): string {
  return TONE_TEXT[String(tone)] ?? 'text-ink';
}

/**
 * Covers both vocabularies: task steps report `ok | error | pending | running`
 * while tasks and tones use the words below them.
 */
const DOT: Record<string, string> = {
  ok: 'bg-risk-safe',
  error: 'bg-risk-sensitive',
  pending: 'bg-ink-faint',
  running: 'bg-accent',
  done: 'bg-risk-safe',
  queued: 'bg-ink-faint',
  blocked: 'bg-risk-review',
  failed: 'bg-risk-sensitive',
  warn: 'bg-risk-review',
  bad: 'bg-risk-sensitive',
};

export function StatusDot({ status, className }: { status: string; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn('inline-block h-2 w-2 shrink-0 rounded-full', DOT[status] ?? 'bg-ink-faint', className)}
    />
  );
}

export function ScoreBadge({ score }: { score: number }) {
  if (!Number.isFinite(score)) return null;
  const value = Math.round(score);
  const tone = value >= 80 ? 'text-accent' : value >= 55 ? 'text-risk-review' : 'text-ink-faint';
  return (
    <span className={cn('font-mono text-xs tabular-nums', tone)} title={`Priority score ${value}`}>
      {value}
    </span>
  );
}
