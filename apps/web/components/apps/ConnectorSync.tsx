'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { LoaderCircle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import type { ConnectorSyncResponse } from '@/lib/types';

/** Pull one app now, via `POST /api/connectors/{key}/sync`. */
export function ConnectorSync({ connectorKey }: { connectorKey: string }) {
  const queryClient = useQueryClient();

  const sync = useMutation<ConnectorSyncResponse, Error>({
    mutationFn: () => duet.syncConnector(connectorKey),
    onSuccess: () => {
      for (const key of [qk.connectors, qk.signals, qk.me, qk.thread, qk.audit, qk.dashboard]) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    },
  });

  const result = sync.data;
  const failures = result?.errors ?? [];

  return (
    <div className="flex flex-col gap-2">
      <Button variant="secondary" disabled={sync.isPending} onClick={() => sync.mutate()}>
        {sync.isPending ? (
          <LoaderCircle size={14} className="animate-spin" />
        ) : (
          <RefreshCw size={14} />
        )}
        {sync.isPending ? 'Checking…' : 'Sync now'}
      </Button>

      {sync.isError ? (
        <p role="alert" className="text-[12px] leading-relaxed text-risk-sensitive">
          {apiErrorMessage(sync.error)}
        </p>
      ) : null}

      {result && failures.length === 0 ? (
        <p role="status" className="text-[12px] text-ink-muted">
          {result.new_signals === 0
            ? 'Checked — nothing new.'
            : `${result.new_signals} new signal${result.new_signals === 1 ? '' : 's'}.`}
        </p>
      ) : null}

      {failures.length > 0 ? (
        <p role="alert" className="text-[12px] leading-relaxed text-risk-sensitive">
          {failures.join(' · ')}
        </p>
      ) : null}
    </div>
  );
}
