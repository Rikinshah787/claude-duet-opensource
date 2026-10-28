'use client';

import { BrandTile } from '@/components/apps/BrandMark';
import { cn } from '@/lib/cn';
import { isAvailable } from '@/lib/connectors';
import { relativeTime } from '@/lib/format';
import type { Connector } from '@/lib/types';

/**
 * One app in the gallery.
 *
 * An app with no working integration is drawn deliberately quieter — dimmed
 * mark, muted label, and the words "Not yet available" under it — so the grid
 * never implies that connecting it would do something.
 */
export function ConnectorTile({
  connector,
  onOpen,
}: {
  connector: Connector;
  onOpen: () => void;
}) {
  const available = isAvailable(connector);
  const monitored = connector.connection?.monitored ?? connector.monitored;

  const caption = connector.connected
    ? connector.connection?.last_sync_at
      ? `synced ${relativeTime(connector.connection.last_sync_at)}`
      : 'connected'
    : available
      ? connector.category
      : 'Not yet available';

  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="group flex w-full flex-col items-start gap-2 text-left"
        aria-label={`${connector.name}${
          connector.connected ? ', connected' : available ? '' : ', not yet available'
        }`}
      >
        <BrandTile
          connectorKey={connector.key}
          glyph={connector.glyph}
          color={connector.color}
          className={cn(
            'aspect-square w-full rounded-xl border text-[1.15rem] transition-all',
            'border-hairline group-hover:border-hairline-strong group-hover:-translate-y-0.5',
            !available && 'opacity-40 saturate-50 group-hover:opacity-70',
          )}
        >
          {connector.connected ? (
            <span
              className={cn(
                'absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full',
                monitored ? 'bg-risk-safe' : 'bg-ink-faint',
              )}
            />
          ) : null}
        </BrandTile>
        <span className="w-full min-w-0">
          <span className={cn('block truncate text-[12.5px]', available ? 'text-ink' : 'text-ink-muted')}>
            {connector.name}
          </span>
          <span className="block truncate text-[10.5px] text-ink-faint">{caption}</span>
        </span>
      </button>
    </li>
  );
}

export function ConnectorGrid({
  connectors,
  onOpen,
}: {
  connectors: readonly Connector[];
  onOpen: (key: string) => void;
}) {
  return (
    <ul className="grid grid-cols-3 gap-x-3 gap-y-5 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-7 xl:grid-cols-8">
      {connectors.map((connector) => (
        <ConnectorTile
          key={connector.key}
          connector={connector}
          onOpen={() => onOpen(connector.key)}
        />
      ))}
    </ul>
  );
}
