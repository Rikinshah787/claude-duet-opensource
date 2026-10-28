'use client';

import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import { StatusChip, StatusDot } from '@/components/ui/Chip';
import { EmptyState, ErrorState, LoadingBlock } from '@/components/ui/States';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import { cn } from '@/lib/cn';
import { dateTimeStamp, duration, prettyJson } from '@/lib/format';
import type { TaskStep } from '@/lib/types';

const LIVE_STATES = new Set(['queued', 'planning', 'running', 'awaiting_approval']);

function StepRow({ step, last }: { step: TaskStep; last: boolean }) {
  const [open, setOpen] = useState(false);
  const hasDetail = step.output !== null && step.output !== undefined ? true : Boolean(step.error);

  return (
    <li className="relative pl-7">
      {/* trace rail */}
      {!last ? <span aria-hidden className="absolute left-[7px] top-4 h-full w-px bg-hairline" /> : null}
      <span className="absolute left-[3px] top-[7px]">
        <StatusDot status={String(step.status)} />
      </span>

      <div className="pb-5">
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <span className="font-mono text-[11px] uppercase tracking-wide text-accent">{step.agent}</span>
          {step.tool ? <span className="font-mono text-[11px] text-ink-faint">{step.tool}</span> : null}
          <span className="ml-auto font-mono text-[11px] tabular-nums text-ink-faint">
            {duration(step.duration_ms)}
          </span>
        </div>

        <p className="mt-1 text-[13.5px] leading-relaxed text-ink">{step.summary || '—'}</p>

        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-ink-faint">
          <span className="capitalize">{String(step.status).replace(/_/g, ' ')}</span>
          {step.started_at ? <span>· {dateTimeStamp(step.started_at)}</span> : null}
          {hasDetail ? (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              className="inline-flex items-center gap-1 text-ink-faint underline-offset-4 hover:text-ink-muted hover:underline"
            >
              {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              {open ? 'hide output' : 'output'}
            </button>
          ) : null}
        </div>

        {step.error ? (
          <p className="mt-2 rounded-lg border border-risk-sensitive/30 bg-risk-sensitive/10 px-2.5 py-1.5 text-[12px] text-risk-sensitive">
            {step.error}
          </p>
        ) : null}

        {open ? (
          <pre className="duet-scroll mt-2 max-h-72 overflow-auto rounded-lg border border-hairline bg-canvas/70 p-3 font-mono text-[11.5px] leading-relaxed text-ink-muted">
            {prettyJson(step.output) || '(no output)'}
          </pre>
        ) : null}
      </div>
    </li>
  );
}

export function TaskTrace({ taskId }: { taskId: string }) {
  const detail = useQuery({
    queryKey: qk.task(taskId),
    queryFn: () => duet.task(taskId),
    refetchInterval: (query) =>
      LIVE_STATES.has(String(query.state.data?.task.status ?? '')) ? 4000 : false,
  });

  if (detail.isLoading) return <LoadingBlock label="Loading the trace" />;
  if (detail.isError) {
    return <ErrorState title="That task could not be loaded" detail={apiErrorMessage(detail.error)} />;
  }

  const task = detail.data?.task;
  const steps = [...(detail.data?.steps ?? [])].sort((a, b) => a.idx - b.idx);
  if (!task) return <EmptyState title="Task not found" />;

  return (
    <div className="rounded-xl border border-hairline bg-surface">
      <div className="border-b border-hairline p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="serif text-[1.25rem] leading-snug text-ink">{task.title}</h2>
            <p className="mt-1.5 text-[13px] leading-relaxed text-ink-muted">{task.intent}</p>
          </div>
          <StatusChip status={task.status} />
        </div>
        <dl className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-[11px] text-ink-faint">
          <div>
            <dt className="uppercase tracking-wider">Lead agent</dt>
            <dd className="font-mono text-ink-muted">{task.lead_agent || '—'}</dd>
          </div>
          <div>
            <dt className="uppercase tracking-wider">Origin</dt>
            <dd className="text-ink-muted">{task.origin || '—'}</dd>
          </div>
          <div>
            <dt className="uppercase tracking-wider">Started</dt>
            <dd className="text-ink-muted">{dateTimeStamp(task.created_at) || '—'}</dd>
          </div>
          <div>
            <dt className="uppercase tracking-wider">Finished</dt>
            <dd className="text-ink-muted">{dateTimeStamp(task.finished_at) || 'still going'}</dd>
          </div>
        </dl>
        {task.error ? (
          <p className="mt-3 rounded-lg border border-risk-sensitive/30 bg-risk-sensitive/10 px-3 py-2 text-[12.5px] text-risk-sensitive">
            {task.error}
          </p>
        ) : null}
      </div>

      <div className="p-5">
        <p className="pb-4 text-[11px] uppercase tracking-wider text-ink-faint">Agent trace</p>
        {steps.length === 0 ? (
          <p className="text-[13px] text-ink-faint">No steps recorded yet — the plan is still forming.</p>
        ) : (
          <ol className={cn('relative')}>
            {steps.map((step, i) => (
              <StepRow key={`${step.idx}-${step.agent}`} step={step} last={i === steps.length - 1} />
            ))}
          </ol>
        )}

        {task.artifacts.length > 0 ? (
          <div className="mt-2 border-t border-hairline pt-4">
            <p className="pb-2 text-[11px] uppercase tracking-wider text-ink-faint">Artifacts</p>
            <ul className="flex flex-col gap-1.5">
              {task.artifacts.map((artifact, i) => {
                const name =
                  artifact.label ?? artifact.title ?? artifact.type ?? artifact.kind ?? `artifact ${i + 1}`;
                return (
                  <li key={i} className="text-[12.5px] text-ink-muted">
                    {artifact.url ? (
                      <a href={artifact.url} target="_blank" rel="noreferrer" className="hover:text-accent">
                        {name}
                      </a>
                    ) : (
                      name
                    )}
                    {artifact.body ? (
                      <span className="mt-1 block whitespace-pre-wrap border-l-2 border-hairline pl-3 text-[12px] text-ink-faint">
                        {artifact.body}
                      </span>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}
      </div>
    </div>
  );
}
