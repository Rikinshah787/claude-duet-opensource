'use client';

import { useQueries, useQuery } from '@tanstack/react-query';
import { ShieldCheck } from 'lucide-react';
import { useMemo } from 'react';
import { ApprovalCard } from '@/components/approvals/ApprovalCard';
import { ViewScroll } from '@/components/ui/Card';
import { RiskChip } from '@/components/ui/Chip';
import { ConnectAppsButton } from '@/components/ViewNav';
import { EmptyState, ErrorState, LoadingBlock } from '@/components/ui/States';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import { relativeTime, timeValue, titleCase } from '@/lib/format';
import type { Approval, RiskTier } from '@/lib/types';

const TIER_ORDER: RiskTier[] = ['forbidden', 'sensitive', 'review', 'safe'];
const HISTORY_STATUSES = ['executed', 'approved', 'rejected', 'reverted'] as const;

export function ApprovalsView() {
  const me = useQuery({ queryKey: qk.me, queryFn: () => duet.me() });
  const pending = useQuery({
    queryKey: qk.approvals('pending'),
    queryFn: () => duet.approvals('pending'),
    refetchInterval: 10000,
  });

  // There is no "all" filter in the contract, so history is assembled from the
  // terminal statuses; any filter the backend rejects simply contributes nothing.
  const historyQueries = useQueries({
    queries: HISTORY_STATUSES.map((status) => ({
      queryKey: qk.approvals(status),
      queryFn: () => duet.approvals(status),
      retry: 0,
    })),
  });

  const history = useMemo(() => {
    const byId = new Map<string, Approval>();
    for (const query of historyQueries) {
      for (const approval of query.data?.approvals ?? []) {
        if (approval.status !== 'pending') byId.set(approval.id, approval);
      }
    }
    return [...byId.values()].sort(
      (a, b) => timeValue(b.created_at) - timeValue(a.created_at),
    );
  }, [historyQueries]);

  const groups = useMemo(() => {
    const list = pending.data?.approvals ?? [];
    const seen = new Map<string, Approval[]>();
    for (const approval of list) {
      const key = String(approval.risk_tier);
      const bucket = seen.get(key);
      if (bucket) bucket.push(approval);
      else seen.set(key, [approval]);
    }
    const ordered: Array<[string, Approval[]]> = [];
    for (const tier of TIER_ORDER) {
      const bucket = seen.get(String(tier));
      if (bucket) {
        ordered.push([String(tier), bucket]);
        seen.delete(String(tier));
      }
    }
    for (const [key, bucket] of seen) ordered.push([key, bucket]);
    return ordered;
  }, [pending.data]);

  const pendingCount = pending.data?.approvals.length ?? 0;
  const nothingConnected = me.isSuccess && me.data.counts.connections === 0;

  return (
    <ViewScroll>
      <div className="border-b border-hairline pb-6">
        <h1 className="serif text-[1.75rem] leading-tight text-ink sm:text-[2rem]">Approvals</h1>
        <p className="mt-2 max-w-reading text-sm text-ink-muted">
          Nothing consequential happens without you, and what is waiting is grouped by how much trust
          the action needs.
          {/* Never claim a count before the queue has answered. */}
          {pending.isSuccess
            ? ` ${pendingCount} waiting right now.`
            : ''}
        </p>
      </div>

      {pending.isLoading ? <LoadingBlock label="Loading the queue" /> : null}
      {pending.isError ? (
        <div className="mt-6">
          <ErrorState
            title="The approval queue is unavailable"
            detail={apiErrorMessage(pending.error)}
          />
        </div>
      ) : null}

      {pending.isSuccess && pendingCount === 0 ? (
        <div className="mt-8">
          <EmptyState
            icon={<ShieldCheck size={20} />}
            title="Nothing waiting on you"
            hint={
              nothingConnected
                ? 'When I want to send, change, or spend something in one of your apps, it stops here and waits for you. I have no app to act in yet, so nothing can reach this queue.'
                : 'When I want to send, change, or spend something, it stops here and waits for you. Nothing is waiting — you are clear.'
            }
            action={nothingConnected ? <ConnectAppsButton /> : undefined}
          />
        </div>
      ) : null}

      <div className="mt-8 flex flex-col gap-10">
        {groups.map(([tier, items]) => (
          <section key={tier}>
            <div className="flex items-center gap-3 pb-4">
              <RiskChip tier={tier} verbose />
              <span className="text-[11px] uppercase tracking-wider text-ink-faint">
                {items.length} action{items.length === 1 ? '' : 's'}
              </span>
            </div>
            <div className="flex flex-col gap-4">
              {items.map((approval) => (
                <ApprovalCard key={approval.id} approval={approval} />
              ))}
            </div>
          </section>
        ))}
      </div>

      {history.length > 0 ? (
        <section className="mt-14 border-t border-hairline pt-8">
          <h2 className="serif text-lg text-ink">Decided</h2>
          <p className="mt-1.5 text-[13px] text-ink-muted">
            What has already run or been turned down. Reversible actions can still be undone.
          </p>
          <div className="mt-5 flex flex-col gap-4">
            {history.map((approval) => (
              <div key={approval.id}>
                <p className="pb-1.5 text-[11px] uppercase tracking-wider text-ink-faint">
                  {titleCase(String(approval.status))} · {relativeTime(approval.created_at)}
                </p>
                <ApprovalCard approval={approval} />
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </ViewScroll>
  );
}
