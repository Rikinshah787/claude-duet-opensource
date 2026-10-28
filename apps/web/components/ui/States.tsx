import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

/**
 * The three states every view distinguishes:
 *
 *  - `LoadingBlock` — the answer has not arrived yet.
 *  - `ErrorState`   — the request failed; it carries the service's own words.
 *  - `EmptyState`   — the request succeeded and there is genuinely nothing.
 *
 * They are deliberately separate components. A failed fetch must never read as
 * "you have no data", so nothing here guesses which one a view meant.
 */
export function EmptyState({
  title,
  hint,
  icon,
  action,
  footnote,
  className,
}: {
  title: string;
  hint?: string;
  icon?: ReactNode;
  action?: ReactNode;
  footnote?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed',
        'border-hairline px-6 py-14 text-center',
        className,
      )}
    >
      {icon ? <div className="text-ink-faint">{icon}</div> : null}
      <p className="serif text-lg text-ink">{title}</p>
      {hint ? <p className="max-w-sm text-sm leading-relaxed text-ink-muted">{hint}</p> : null}
      {action ? <div className="pt-1">{action}</div> : null}
      {footnote ? <p className="max-w-sm text-[11.5px] leading-relaxed text-ink-faint">{footnote}</p> : null}
    </div>
  );
}

/**
 * A request that failed. `detail` is the message the service sent — pass it
 * through `apiErrorMessage()` so the person sees the API's own words rather
 * than status-and-path noise.
 */
export function ErrorState({
  title,
  detail,
  action,
  className,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-start gap-2 rounded-xl border border-risk-sensitive/30',
        'bg-risk-sensitive/10 px-4 py-4',
        className,
      )}
    >
      <p className="serif text-[1.05rem] leading-snug text-ink">{title}</p>
      <p className="text-[13px] leading-relaxed text-risk-sensitive">{detail}</p>
      {action ? <div className="pt-1">{action}</div> : null}
    </div>
  );
}

/** One-line failure, for a mutation rather than a whole panel. */
export function InlineError({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p
      role="alert"
      className={cn(
        'rounded-lg border border-risk-sensitive/30 bg-risk-sensitive/10 px-3 py-2',
        'text-[12.5px] leading-relaxed text-risk-sensitive',
        className,
      )}
    >
      {children}
    </p>
  );
}

export function LoadingBlock({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 px-1 py-10 text-sm text-ink-faint" role="status">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
      {label}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-lg bg-raised', className)} />;
}
