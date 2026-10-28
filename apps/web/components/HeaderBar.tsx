'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LoaderCircle, RefreshCw, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import { cn } from '@/lib/cn';
import type { SyncAllResponse } from '@/lib/types';

/**
 * "4 apps checked · 12 new", plus the count of apps that refused.
 *
 * Kept to one short line on purpose: the per-app error text belongs in that
 * app's sheet, where there is room to read it, not truncated in the header.
 */
function syncSummary(result: SyncAllResponse): string {
  const checked = `${result.connections_swept} app${result.connections_swept === 1 ? '' : 's'} checked`;
  const parts = [checked, `${result.new_signals} new`];
  if (result.errors.length > 0) parts.push(`${result.errors.length} failed`);
  return parts.join(' · ');
}

const NOTHING_CONNECTED = 'Connect an app first — there is nothing to check yet.';

export function HeaderBar() {
  const queryClient = useQueryClient();
  const [note, setNote] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const me = useQuery({ queryKey: qk.me, queryFn: () => duet.me() });
  const health = useQuery({ queryKey: qk.health, queryFn: () => duet.health(), refetchInterval: 30000 });

  const sync = useMutation({
    mutationFn: () => duet.sync(),
    onSuccess: (result) => {
      setFailed(result.errors.length > 0);
      setNote(syncSummary(result));
      void queryClient.invalidateQueries();
    },
    onError: (error: unknown) => {
      setFailed(true);
      setNote(apiErrorMessage(error));
    },
  });

  const connections = me.data?.counts.connections;
  const llmOk = health.data?.llm.ok ?? false;
  const reachable = health.isSuccess;
  // Only claim there is nothing to sync once /api/me has actually said so.
  const nothingConnected = me.isSuccess && connections === 0;

  return (
    <header className="z-20 shrink-0 border-b border-hairline bg-canvas/95 backdrop-blur">
      <div className="flex items-center gap-3 px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            aria-hidden
            className="grid h-7 w-7 place-items-center rounded-lg border border-accent/30 bg-accent/10 text-accent"
          >
            <Sparkles size={15} strokeWidth={1.8} />
          </span>
          <div className="min-w-0 leading-tight">
            <p className="serif truncate text-[15px] text-ink">Claude Duet</p>
            <p className="truncate text-[11px] text-ink-faint">
              {reachable ? (
                <>
                  {connections ?? 0} app{connections === 1 ? '' : 's'} connected
                  {health.data ? ` · ${llmOk ? health.data.llm.provider : 'offline model'}` : ''}
                </>
              ) : (
                'backend unreachable'
              )}
            </p>
          </div>
        </div>

        <span
          aria-hidden
          className={cn(
            'ml-1 h-1.5 w-1.5 shrink-0 rounded-full',
            reachable ? (llmOk ? 'bg-risk-safe' : 'bg-risk-review') : 'bg-risk-sensitive',
          )}
        />
        <span className="sr-only">
          {reachable ? (llmOk ? 'Backend healthy' : 'Backend up, model offline') : 'Backend unreachable'}
        </span>

        <div className="ml-auto flex items-center gap-2">
          {/* The title lives on the wrapper: a disabled button fires no mouse
              events, so a tooltip on it would never appear. */}
          <span title={nothingConnected ? NOTHING_CONNECTED : undefined}>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => sync.mutate()}
              disabled={sync.isPending || nothingConnected}
              aria-describedby={nothingConnected ? 'sync-blocked' : undefined}
            >
              {sync.isPending ? (
                <LoaderCircle size={14} className="animate-spin" />
              ) : (
                <RefreshCw size={14} />
              )}
              <span className="hidden sm:inline">{sync.isPending ? 'Syncing…' : 'Sync now'}</span>
            </Button>
          </span>
          {nothingConnected ? (
            <span id="sync-blocked" className="sr-only">
              {NOTHING_CONNECTED}
            </span>
          ) : null}
        </div>
      </div>

      {note ? (
        <div
          className={cn(
            'flex items-start gap-2 border-t border-hairline px-4 py-2 text-[12px] sm:px-6',
            failed ? 'text-risk-sensitive' : 'text-ink-muted',
          )}
          role={failed ? 'alert' : 'status'}
        >
          <span className="min-w-0 flex-1 truncate">{note}</span>
          <button
            type="button"
            onClick={() => {
              setNote(null);
              setFailed(false);
            }}
            className="shrink-0 text-ink-faint hover:text-ink"
          >
            Dismiss
          </button>
        </div>
      ) : null}
    </header>
  );
}
