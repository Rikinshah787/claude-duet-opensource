import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

export function Card({
  children,
  className,
  tone = 'surface',
}: {
  children: ReactNode;
  className?: string;
  tone?: 'surface' | 'raised' | 'bare';
}) {
  return (
    <div
      className={cn(
        'rounded-xl border border-hairline',
        tone === 'surface' && 'bg-surface',
        tone === 'raised' && 'bg-raised',
        tone === 'bare' && 'bg-transparent',
        className,
      )}
    >
      {children}
    </div>
  );
}

export function PageHeader({
  title,
  lead,
  action,
}: {
  title: string;
  lead?: string;
  action?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4 border-b border-hairline pb-6">
      <div className="min-w-0">
        <h1 className="serif text-[1.75rem] leading-tight text-ink sm:text-[2rem]">{title}</h1>
        {lead ? <p className="mt-2 max-w-reading text-sm text-ink-muted">{lead}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </header>
  );
}

export function SectionTitle({
  title,
  meta,
  className,
}: {
  title: string;
  meta?: string;
  className?: string;
}) {
  return (
    <div className={cn('flex items-baseline justify-between gap-3', className)}>
      <h2 className="serif text-lg text-ink">{title}</h2>
      {meta ? <span className="text-xs uppercase tracking-wider text-ink-faint">{meta}</span> : null}
    </div>
  );
}

export function Hairline({ className }: { className?: string }) {
  return <div className={cn('h-px w-full bg-hairline', className)} />;
}

/** Standard scroll container for every view except the thread. */
export function ViewScroll({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className="duet-scroll min-h-0 flex-1 overflow-y-auto">
      <div className={cn('mx-auto w-full max-w-5xl px-5 py-8 sm:px-8 sm:py-10', className)}>{children}</div>
    </div>
  );
}
