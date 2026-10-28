'use client';

import { Check, Clock, LoaderCircle, Undo2, X } from 'lucide-react';
import { useState } from 'react';
import { ConnectorChip } from '@/components/apps/ConnectorChip';
import { PreviewBlock } from '@/components/approvals/PreviewBlock';
import { Button } from '@/components/ui/Button';
import { Chip, RiskChip, StatusChip } from '@/components/ui/Chip';
import { apiErrorMessage } from '@/lib/api';
import { cn } from '@/lib/cn';
import { relativeTime, titleCase } from '@/lib/format';
import { useApprovalActions } from '@/lib/hooks';
import type { Approval } from '@/lib/types';

export function ApprovalCard({
  approval,
  variant = 'full',
}: {
  approval: Approval;
  variant?: 'inline' | 'full';
}) {
  const { approve, reject, revert, busy } = useApprovalActions();
  const [note, setNote] = useState('');
  const [noteOpen, setNoteOpen] = useState(false);

  const pending = approval.status === 'pending';
  const forbidden = approval.risk_tier === 'forbidden';
  const decided = approve.isSuccess || reject.isSuccess;
  const summary = approve.data?.result?.summary;
  const failure = approve.error ?? reject.error ?? revert.error;

  return (
    <article
      className={cn(
        'rounded-xl border bg-surface',
        pending ? 'border-hairline-strong' : 'border-hairline',
        variant === 'inline' ? 'p-4' : 'p-5',
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="serif text-[1.0625rem] leading-snug text-ink">{approval.title}</h3>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <RiskChip tier={approval.risk_tier} />
            <Chip>{titleCase(approval.action_type)}</Chip>
            <ConnectorChip connectorKey={approval.connector_key} />
            {!pending ? <StatusChip status={approval.status} /> : null}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1 text-[11px] text-ink-faint">
          <span>{relativeTime(approval.created_at)}</span>
          {approval.expires_at ? (
            <span className="flex items-center gap-1">
              <Clock size={11} /> expires {relativeTime(approval.expires_at)}
            </span>
          ) : null}
        </div>
      </div>

      {approval.rationale ? (
        <section className="mt-4">
          <h4 className="text-[11px] uppercase tracking-wider text-ink-faint">Why I want to do this</h4>
          <p className="mt-1 text-[13px] leading-relaxed text-ink-muted">{approval.rationale}</p>
        </section>
      ) : null}

      <section className="mt-3">
        <PreviewBlock actionType={approval.action_type} preview={approval.preview} />
      </section>

      <p className="mt-3 text-[12px] text-ink-faint">
        {approval.reversible
          ? 'Reversible — I can undo this after it runs.'
          : 'Not reversible — once it runs, it stands.'}
      </p>

      {summary ? (
        <p className="mt-3 rounded-lg border border-risk-safe/30 bg-risk-safe/10 px-3 py-2 text-[13px] text-risk-safe">
          Executed · {summary}
        </p>
      ) : null}

      {failure ? (
        <p className="mt-3 rounded-lg border border-risk-sensitive/30 bg-risk-sensitive/10 px-3 py-2 text-[13px] text-risk-sensitive">
          {apiErrorMessage(failure)}
        </p>
      ) : null}

      {pending && !decided ? (
        <div className="mt-4 border-t border-hairline pt-3">
          {noteOpen ? (
            <div className="pb-3">
              <label htmlFor={`note-${approval.id}`} className="sr-only">
                Note for this decision
              </label>
              <input
                id={`note-${approval.id}`}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Optional note for the audit log"
                className="w-full rounded-lg border border-hairline bg-raised px-3 py-2 text-[13px] text-ink placeholder:text-ink-faint"
              />
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={busy || forbidden}
              onClick={() => approve.mutate({ id: approval.id, note: note || undefined })}
            >
              {approve.isPending ? <LoaderCircle size={14} className="animate-spin" /> : <Check size={14} />}
              Approve
            </Button>
            <Button
              size="sm"
              variant="danger"
              disabled={busy}
              onClick={() => reject.mutate({ id: approval.id, note: note || undefined })}
            >
              <X size={14} />
              Reject
            </Button>
            <button
              type="button"
              onClick={() => setNoteOpen((v) => !v)}
              className="text-[12px] text-ink-faint underline-offset-4 hover:text-ink-muted hover:underline"
            >
              {noteOpen ? 'Hide note' : 'Add a note'}
            </button>
            {forbidden ? (
              <span className="text-[12px] text-risk-forbidden">
                Forbidden actions cannot be approved.
              </span>
            ) : null}
          </div>
        </div>
      ) : null}

      {!pending && approval.reversible && approval.status !== 'reverted' ? (
        <div className="mt-4 border-t border-hairline pt-3">
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={() => revert.mutate({ id: approval.id })}
          >
            {revert.isPending ? <LoaderCircle size={14} className="animate-spin" /> : <Undo2 size={14} />}
            Revert
          </Button>
        </div>
      ) : null}
    </article>
  );
}
