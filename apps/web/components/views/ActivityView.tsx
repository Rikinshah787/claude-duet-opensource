'use client';

import { useQuery } from '@tanstack/react-query';
import { CircleCheck, ScrollText, TriangleAlert } from 'lucide-react';
import { ViewScroll } from '@/components/ui/Card';
import { EmptyState, ErrorState, LoadingBlock } from '@/components/ui/States';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import { cn } from '@/lib/cn';
import { clockTime, dateTimeStamp, prettyJson, timeValue } from '@/lib/format';

const OUTCOME_TEXT: Record<string, string> = {
  ok: 'text-risk-safe',
  success: 'text-risk-safe',
  denied: 'text-risk-review',
  rejected: 'text-risk-review',
  error: 'text-risk-sensitive',
  failed: 'text-risk-sensitive',
};

/** The audit `detail` field is a JSON object; never hand it to React raw. */
function detailText(detail: unknown): string {
  if (detail === null || detail === undefined) return '';
  if (typeof detail === 'string') return detail;
  const text = prettyJson(detail);
  if (text === '{}' || text === '[]') return '';
  return text.replace(/\s+/g, ' ').trim();
}

export function ActivityView() {
  const audit = useQuery({ queryKey: qk.audit, queryFn: () => duet.audit(100), refetchInterval: 20000 });
  const verify = useQuery({ queryKey: qk.auditVerify, queryFn: () => duet.auditVerify() });

  const entries = [...(audit.data?.entries ?? [])].sort(
    (a, b) => timeValue(b.at) - timeValue(a.at),
  );

  const valid = verify.data?.valid ?? false;

  return (
    <ViewScroll>
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-hairline pb-6">
        <div>
          <h1 className="serif text-[1.75rem] leading-tight text-ink sm:text-[2rem]">Activity</h1>
          <p className="mt-2 max-w-reading text-sm text-ink-muted">
            Every read, write, decision and revert, in order, hash-chained so tampering shows.
          </p>
        </div>
        {verify.isSuccess ? (
          <span
            className={cn(
              'inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px]',
              valid
                ? 'border-risk-safe/35 bg-risk-safe/10 text-risk-safe'
                : 'border-risk-sensitive/35 bg-risk-sensitive/10 text-risk-sensitive',
            )}
            title={verify.data.head ? `head ${verify.data.head}` : undefined}
          >
            {valid ? <CircleCheck size={13} /> : <TriangleAlert size={13} />}
            {valid ? 'Chain verified' : 'Chain broken'} · {verify.data.checked} entries
          </span>
        ) : null}
      </div>

      <div className="mt-8">
        {audit.isLoading ? <LoadingBlock label="Reading the log" /> : null}
        {audit.isError ? (
          <ErrorState title="The audit log is unavailable" detail={apiErrorMessage(audit.error)} />
        ) : null}
        {audit.isSuccess && entries.length === 0 ? (
          <EmptyState
            icon={<ScrollText size={20} />}
            title="The log is empty"
            hint="It starts this way, and it is meant to: the log only ever records what actually happened. Every app I read, every message I send you, every approval you decide and every action I revert is appended here from the first one onward."
            footnote="Nothing is written here in advance, and nothing already written can be edited — the hash chain above is what proves it."
          />
        ) : null}

        <ol className="relative">
          {entries.map((entry, i) => (
            <li key={entry.id} className="relative pl-8">
              {i !== entries.length - 1 ? (
                <span aria-hidden className="absolute left-[7px] top-5 h-full w-px bg-hairline" />
              ) : null}
              <span
                aria-hidden
                className={cn(
                  'absolute left-[3px] top-[7px] h-2 w-2 rounded-full',
                  OUTCOME_TEXT[entry.outcome] ? 'bg-current' : 'bg-ink-faint',
                  OUTCOME_TEXT[entry.outcome],
                )}
              />
              <div className="pb-6">
                <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                  <span className="font-mono text-[12px] text-ink">{entry.action}</span>
                  <span className={cn('text-[11px]', OUTCOME_TEXT[entry.outcome] ?? 'text-ink-faint')}>
                    {entry.outcome}
                  </span>
                  <span
                    className="ml-auto text-[11px] tabular-nums text-ink-faint"
                    title={dateTimeStamp(entry.at)}
                  >
                    {clockTime(entry.at)}
                  </span>
                </div>
                <p className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">
                  {entry.actor}
                  {entry.resource_type ? (
                    <>
                      {' · '}
                      <span className="font-mono text-[11.5px] text-ink-faint">
                        {entry.resource_type}
                        {entry.resource_id ? `/${entry.resource_id}` : ''}
                      </span>
                    </>
                  ) : null}
                </p>
                {detailText(entry.detail) ? (
                  <p className="mt-1 whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed text-ink-faint">
                    {detailText(entry.detail)}
                  </p>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </ViewScroll>
  );
}
