'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Radar } from 'lucide-react';
import { ConnectorChip } from '@/components/apps/ConnectorChip';
import { Chip, DispositionChip, ScoreBadge } from '@/components/ui/Chip';
import { EmptyState, ErrorState, LoadingBlock } from '@/components/ui/States';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import { relativeTime } from '@/lib/format';

/** Recent raw signals with the two dispositions a human can set. */
export function SignalsPanel() {
  const queryClient = useQueryClient();
  const signals = useQuery({
    queryKey: qk.signals,
    queryFn: () => duet.signals(undefined, 12),
    refetchInterval: 15000,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: qk.signals });
    void queryClient.invalidateQueries({ queryKey: qk.me });
  };

  const dismiss = useMutation({ mutationFn: (id: string) => duet.dismissSignal(id), onSuccess: refresh });
  const handle = useMutation({ mutationFn: (id: string) => duet.handleSignal(id), onSuccess: refresh });

  const list = signals.data?.signals ?? [];
  const busy = dismiss.isPending || handle.isPending;

  if (signals.isLoading) {
    return <LoadingBlock label="Reading the latest signals" />;
  }

  if (signals.isError) {
    return <ErrorState title="Signals are unavailable" detail={apiErrorMessage(signals.error)} />;
  }

  if (list.length === 0) {
    return (
      <EmptyState
        icon={<Radar size={18} />}
        title="Nothing observed yet"
        hint="A signal is one thing I noticed in a connected app, scored before any of it reached you. There is nothing here because nothing has been observed."
      />
    );
  }

  return (
    <ul className="divide-y divide-hairline">
      {list.map((signal) => (
        <li key={signal.id} className="flex gap-4 py-3.5">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <p className="text-[13.5px] font-medium leading-snug text-ink">{signal.title}</p>
              <span className="text-[11px] text-ink-faint">{relativeTime(signal.occurred_at)}</span>
            </div>
            {signal.body ? (
              <p className="mt-1 line-clamp-2 text-[12.5px] leading-relaxed text-ink-muted">{signal.body}</p>
            ) : null}
            {signal.reasons.length > 0 ? (
              <p className="mt-1 text-[11.5px] text-ink-faint">{signal.reasons.join(' · ')}</p>
            ) : null}
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <ConnectorChip connectorKey={signal.connector_key} />
              <DispositionChip disposition={signal.disposition} />
              {signal.actor ? (
                <Chip className="normal-case tracking-normal">{signal.actor}</Chip>
              ) : null}
              <button
                type="button"
                disabled={busy}
                onClick={() => handle.mutate(signal.id)}
                className="text-[11.5px] text-ink-faint underline-offset-4 hover:text-ink hover:underline"
              >
                Mark handled
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => dismiss.mutate(signal.id)}
                className="text-[11.5px] text-ink-faint underline-offset-4 hover:text-ink hover:underline"
              >
                Dismiss
              </button>
            </div>
          </div>
          <div className="shrink-0 pt-0.5 text-right">
            <ScoreBadge score={signal.score} />
          </div>
        </li>
      ))}
    </ul>
  );
}
