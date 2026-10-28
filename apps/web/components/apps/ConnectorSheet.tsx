'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Eye, LoaderCircle, Unplug, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { BrandTile } from '@/components/apps/BrandMark';
import { ConnectPanel } from '@/components/apps/ConnectPanel';
import { ConnectorCapabilities } from '@/components/apps/ConnectorCapabilities';
import { ConnectorSync } from '@/components/apps/ConnectorSync';
import { ConnectorUnavailable } from '@/components/apps/ConnectorUnavailable';
import { Button } from '@/components/ui/Button';
import { Chip } from '@/components/ui/Chip';
import { InlineError } from '@/components/ui/States';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import { isAvailable } from '@/lib/connectors';
import { relativeTime } from '@/lib/format';
import type { Connector } from '@/lib/types';

export function ConnectorSheet({
  connector,
  onClose,
}: {
  connector: Connector;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [replacing, setReplacing] = useState(false);
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: qk.connectors });
    void queryClient.invalidateQueries({ queryKey: qk.me });
  };

  const disconnect = useMutation({
    mutationFn: () => duet.disconnect(connector.key),
    onSuccess: () => {
      setConfirmingDisconnect(false);
      refresh();
    },
  });
  const monitor = useMutation({
    mutationFn: (monitored: boolean) => duet.updateConnector(connector.key, { monitored }),
    onSuccess: refresh,
  });

  const busy = disconnect.isPending || monitor.isPending;
  const failure = disconnect.error ?? monitor.error;
  const watching = connector.connection?.monitored ?? connector.monitored;
  const available = isAvailable(connector);

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button
        type="button"
        aria-label="Close panel"
        onClick={onClose}
        className="absolute inset-0 bg-black/50"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`${connector.name} details`}
        className="duet-scroll relative flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-hairline bg-surface"
      >
        <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-hairline bg-surface px-5 py-4">
          <BrandTile
            connectorKey={connector.key}
            glyph={connector.glyph}
            color={connector.color}
            className="h-11 w-11 shrink-0 rounded-xl text-[15px]"
            markClassName="h-[22px] w-[22px]"
          />
          <div className="min-w-0 flex-1">
            <h2 className="serif text-[1.15rem] leading-snug text-ink">{connector.name}</h2>
            <p className="mt-0.5 text-[11px] uppercase tracking-wider text-ink-faint">
              {connector.category} · {connector.auth}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg p-1 text-ink-faint hover:bg-raised hover:text-ink"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex flex-col gap-6 px-5 py-5">
          <p className="text-[13.5px] leading-relaxed text-ink-muted">{connector.summary}</p>

          <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-faint">
            {connector.connected ? (
              <span className="inline-flex items-center gap-1.5 text-risk-safe">
                <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-risk-safe" />
                Connected
                {connector.connection?.account_label ? ` · ${connector.connection.account_label}` : ''}
              </span>
            ) : (
              <span>Not connected</span>
            )}
            {connector.connection?.last_sync_at ? (
              <span>· last sync {relativeTime(connector.connection.last_sync_at)}</span>
            ) : null}
            {!available ? (
              <Chip className="border-risk-review/30 bg-risk-review/10 text-risk-review">
                Not yet available
              </Chip>
            ) : null}
            {connector.signal_count > 0 ? <Chip>{connector.signal_count} signals</Chip> : null}
          </div>

          {connector.connection?.last_error ? (
            <InlineError>Last sync failed: {connector.connection.last_error}</InlineError>
          ) : null}

          <ConnectorCapabilities connector={connector} />

          {connector.connected ? (
            <section className="flex flex-col gap-5 border-t border-hairline pt-5">
              {available ? (
                <div>
                  <h3 className="text-[11px] uppercase tracking-wider text-ink-faint">Check it now</h3>
                  <p className="mb-3 mt-2 text-[12.5px] leading-relaxed text-ink-muted">
                    Reads {connector.name} right now instead of waiting for the next scheduled sync.
                  </p>
                  <ConnectorSync connectorKey={connector.key} />
                </div>
              ) : (
                <p className="text-[13px] leading-relaxed text-ink-muted">
                  This app is connected, but it has no working integration behind it, so there is nothing
                  to sync and nothing will be read from it. Disconnecting is the only action left here.
                </p>
              )}

              {available ? (
                replacing ? (
                  <div>
                    <ConnectPanel connector={connector} onConnected={() => setReplacing(false)} />
                    <button
                      type="button"
                      onClick={() => setReplacing(false)}
                      className="mt-3 text-[12px] text-ink-faint underline-offset-4 hover:text-ink-muted hover:underline"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div>
                    <h3 className="text-[11px] uppercase tracking-wider text-ink-faint">Credential</h3>
                    <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">
                      Held in the vault behind a reference. I am given the reference, never the secret.
                    </p>
                    <button
                      type="button"
                      onClick={() => setReplacing(true)}
                      className="mt-2 text-[12.5px] text-accent underline-offset-4 hover:underline"
                    >
                      Replace it
                    </button>
                  </div>
                )
              ) : null}
            </section>
          ) : !available ? (
            <ConnectorUnavailable connector={connector} />
          ) : (
            <section className="border-t border-hairline pt-5">
              <ConnectPanel key={connector.key} connector={connector} onConnected={onClose} />
            </section>
          )}

          {failure ? <InlineError>{apiErrorMessage(failure)}</InlineError> : null}
        </div>

        {connector.connected ? (
          <div className="mt-auto border-t border-hairline px-5 py-4">
            {confirmingDisconnect ? (
              <div className="flex flex-col gap-3">
                <p className="text-[12.5px] leading-relaxed text-ink-muted">
                  Disconnecting destroys the stored credential — it is erased from the vault, not just
                  unlinked. Reconnecting means pasting a new one.
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <Button variant="danger" disabled={busy} onClick={() => disconnect.mutate()}>
                    {disconnect.isPending ? (
                      <LoaderCircle size={14} className="animate-spin" />
                    ) : (
                      <Unplug size={14} />
                    )}
                    Destroy it and disconnect
                  </Button>
                  <Button variant="ghost" disabled={busy} onClick={() => setConfirmingDisconnect(false)}>
                    Keep it
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="secondary" disabled={busy} onClick={() => setConfirmingDisconnect(true)}>
                  <Unplug size={14} />
                  Disconnect
                </Button>
                <Button variant="ghost" disabled={busy} onClick={() => monitor.mutate(!watching)}>
                  <Eye size={14} />
                  {watching ? 'Stop watching' : 'Watch this'}
                </Button>
              </div>
            )}
          </div>
        ) : null}
      </aside>
    </div>
  );
}
