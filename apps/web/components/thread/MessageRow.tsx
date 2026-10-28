'use client';

import { Radar, Workflow } from 'lucide-react';
import { ApprovalCard } from '@/components/approvals/ApprovalCard';
import { BriefCard } from '@/components/thread/BriefCard';
import { cn } from '@/lib/cn';
import { clockTime, relativeTime } from '@/lib/format';
import type { Approval, ThreadMessage } from '@/lib/types';

function reasonsOf(message: ThreadMessage): string[] {
  const raw = message.meta?.reasons;
  if (Array.isArray(raw)) return raw.filter((r): r is string => typeof r === 'string');
  if (typeof message.meta?.why === 'string') return [message.meta.why];
  return [];
}

export function MessageRow({
  message,
  approvals,
  optimistic = false,
  onOpenTask,
}: {
  message: ThreadMessage;
  approvals: Map<string, Approval>;
  optimistic?: boolean;
  onOpenTask?: (taskId: string) => void;
}) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div
          className={cn(
            'max-w-[85%] rounded-xl rounded-br-sm border border-hairline bg-raised px-3.5 py-2.5',
            optimistic && 'opacity-60',
          )}
        >
          <p className="whitespace-pre-wrap text-[14px] leading-relaxed text-ink">{message.content}</p>
          <p className="pt-1 text-right text-[10px] text-ink-faint">
            {optimistic ? 'sending…' : clockTime(message.created_at)}
          </p>
        </div>
      </div>
    );
  }

  const reasons = reasonsOf(message);
  const approval = message.approval_id ? approvals.get(message.approval_id) : undefined;

  return (
    <div
      className={cn(
        'animate-rise',
        message.proactive && 'border-l-2 border-accent/70 pl-4',
      )}
    >
      {message.proactive ? (
        <div className="flex flex-wrap items-center gap-2 pb-1.5">
          <span className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-accent">
            <Radar size={12} /> Reached out
          </span>
          <span className="text-[11px] text-ink-faint">{relativeTime(message.created_at)}</span>
        </div>
      ) : (
        <div className="flex items-center gap-2 pb-1.5 text-[11px] text-ink-faint">
          <span className="uppercase tracking-wider">Duet</span>
          <span>{clockTime(message.created_at)}</span>
        </div>
      )}

      {message.content && message.kind !== 'brief' ? (
        <p className="whitespace-pre-wrap text-[14.5px] leading-[1.65] text-ink">{message.content}</p>
      ) : null}

      {reasons.length > 0 ? (
        <p className="mt-2 text-[12px] leading-relaxed text-ink-faint">
          <span className="text-ink-muted">Why now:</span> {reasons.join(' · ')}
        </p>
      ) : null}

      {message.kind === 'brief' ? (
        <div className="mt-3">
          <BriefCard content={message.content} />
        </div>
      ) : null}

      {message.kind === 'approval' ? (
        <div className="mt-3">
          {approval ? (
            <ApprovalCard approval={approval} variant="inline" />
          ) : (
            <p className="rounded-xl border border-dashed border-hairline px-3.5 py-3 text-[12px] text-ink-faint">
              This action has already been decided — see Approvals for the record.
            </p>
          )}
        </div>
      ) : null}

      {message.task_id && onOpenTask ? (
        <button
          type="button"
          onClick={() => onOpenTask(message.task_id ?? '')}
          className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-hairline bg-surface px-2.5 py-1.5 text-[12px] text-ink-muted transition-colors hover:border-accent/40 hover:text-ink"
        >
          <Workflow size={13} /> See what I did
        </button>
      ) : null}
    </div>
  );
}
