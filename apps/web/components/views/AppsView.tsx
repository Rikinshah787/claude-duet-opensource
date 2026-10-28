'use client';

import { useQuery } from '@tanstack/react-query';
import { LayoutGrid, Search } from 'lucide-react';
import { useMemo, useState } from 'react';
import { ConnectorGrid } from '@/components/apps/ConnectorTile';
import { ConnectorSheet } from '@/components/apps/ConnectorSheet';
import { ViewScroll } from '@/components/ui/Card';
import { EmptyState, ErrorState, LoadingBlock } from '@/components/ui/States';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import { cn } from '@/lib/cn';
import { connectedCount, isAvailable } from '@/lib/connectors';
import type { Connector } from '@/lib/types';

export function AppsView() {
  const connectors = useQuery({ queryKey: qk.connectors, queryFn: () => duet.connectors() });
  const [category, setCategory] = useState<string>('all');
  const [query, setQuery] = useState('');
  const [connectedOnly, setConnectedOnly] = useState(false);
  const [openKey, setOpenKey] = useState<string | null>(null);

  const all = connectors.data?.connectors ?? [];
  const categories = connectors.data?.categories ?? [];

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return all.filter((connector) => {
      if (category !== 'all' && connector.category !== category) return false;
      if (connectedOnly && !connector.connected) return false;
      if (!needle) return true;
      return (
        connector.name.toLowerCase().includes(needle) ||
        connector.key.toLowerCase().includes(needle) ||
        connector.category.toLowerCase().includes(needle) ||
        connector.summary.toLowerCase().includes(needle)
      );
    });
  }, [all, category, connectedOnly, query]);

  const [ready, waiting] = useMemo(() => {
    const yes: Connector[] = [];
    const no: Connector[] = [];
    for (const connector of filtered) (isAvailable(connector) ? yes : no).push(connector);
    return [yes, no];
  }, [filtered]);

  const open = openKey ? all.find((connector) => connector.key === openKey) : undefined;
  const connected = connectedCount(all);
  const availableTotal = all.filter(isAvailable).length;

  return (
    <>
      <ViewScroll className="max-w-6xl">
        <div className="flex flex-wrap items-end justify-between gap-6 border-b border-hairline pb-6">
          <div className="min-w-0">
            <h1 className="serif text-[1.75rem] leading-tight text-ink sm:text-[2rem]">Apps</h1>
            <p className="mt-2 max-w-reading text-sm text-ink-muted">
              Connect an app and I start reading it on the next sync. Everything I ever tell you comes
              from here — so this page is where the product actually begins.
            </p>
          </div>
          <div className="shrink-0 rounded-xl border border-hairline bg-surface px-4 py-3">
            <p className="text-[11px] uppercase tracking-wider text-ink-faint">Connected</p>
            <p
              className={cn(
                'serif mt-1 text-[2rem] leading-none tabular-nums',
                connected > 0 ? 'text-accent' : 'text-ink-muted',
              )}
            >
              {connected}
            </p>
            <p className="mt-1 text-[11px] text-ink-faint">
              of {availableTotal} available · {all.length} in the catalogue
            </p>
          </div>
        </div>

        {connectors.isSuccess && connected === 0 ? (
          <p className="mt-6 rounded-xl border border-accent/30 bg-accent/10 px-4 py-3 text-[13px] leading-relaxed text-ink">
            Nothing is connected yet, so every other view is empty. Pick an app below, paste the
            credential it issues, and I start reading it on the next sync.
          </p>
        ) : null}

        <div className="mt-6 flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[13rem] flex-1">
              <Search
                size={14}
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
              />
              <label htmlFor="app-search" className="sr-only">
                Search apps
              </label>
              <input
                id="app-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search apps"
                className="w-full rounded-lg border border-hairline bg-surface py-2 pl-9 pr-3 text-[13px] text-ink placeholder:text-ink-faint"
              />
            </div>
            <button
              type="button"
              aria-pressed={connectedOnly}
              onClick={() => setConnectedOnly((v) => !v)}
              className={cn(
                'rounded-lg border px-3 py-2 text-[12.5px] transition-colors',
                connectedOnly
                  ? 'border-accent/40 bg-accent/10 text-accent'
                  : 'border-hairline bg-surface text-ink-muted hover:text-ink',
              )}
            >
              Connected only
            </button>
          </div>

          <ul className="duet-scroll flex gap-1.5 overflow-x-auto pb-1">
            {['all', ...categories].map((name) => (
              <li key={name} className="shrink-0">
                <button
                  type="button"
                  aria-pressed={category === name}
                  onClick={() => setCategory(name)}
                  className={cn(
                    'rounded-lg border px-2.5 py-1.5 text-[12px] capitalize transition-colors',
                    category === name
                      ? 'border-hairline-strong bg-raised text-ink'
                      : 'border-hairline bg-transparent text-ink-faint hover:text-ink-muted',
                  )}
                >
                  {name === 'all' ? 'All' : name}
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="mt-7">
          {connectors.isLoading ? <LoadingBlock label="Loading the catalogue" /> : null}
          {connectors.isError ? (
            <ErrorState
              title="The app catalogue is unavailable"
              detail={apiErrorMessage(connectors.error)}
            />
          ) : null}
          {connectors.isSuccess && filtered.length === 0 ? (
            <EmptyState
              icon={<LayoutGrid size={20} />}
              title="No apps match"
              hint="Nothing in the catalogue matches that search and filter. Try a different category, or clear the search."
            />
          ) : null}

          {ready.length > 0 ? (
            <section>
              <div className="flex items-baseline justify-between gap-3 pb-4">
                <h2 className="serif text-lg text-ink">Ready to connect</h2>
                <span className="text-[11px] uppercase tracking-wider text-ink-faint">
                  {ready.length} app{ready.length === 1 ? '' : 's'}
                </span>
              </div>
              <ConnectorGrid connectors={ready} onOpen={setOpenKey} />
            </section>
          ) : null}

          {waiting.length > 0 ? (
            <section className={cn(ready.length > 0 && 'mt-12 border-t border-hairline pt-8')}>
              <div className="flex items-baseline justify-between gap-3 pb-1">
                <h2 className="serif text-lg text-ink-muted">Not yet available</h2>
                <span className="text-[11px] uppercase tracking-wider text-ink-faint">
                  {waiting.length} app{waiting.length === 1 ? '' : 's'}
                </span>
              </div>
              <p className="max-w-reading pb-4 text-[12.5px] leading-relaxed text-ink-faint">
                These are in the catalogue because they are apps I intend to support. There is no working
                integration behind them today, so they cannot be connected and I cannot read them.
              </p>
              <ConnectorGrid connectors={waiting} onOpen={setOpenKey} />
            </section>
          ) : null}
        </div>
      </ViewScroll>

      {open ? <ConnectorSheet connector={open} onClose={() => setOpenKey(null)} /> : null}
    </>
  );
}
