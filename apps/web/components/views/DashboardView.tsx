'use client';

import { useQuery } from '@tanstack/react-query';
import { Gauge } from 'lucide-react';
import { BarChartSvg, LineChartSvg } from '@/components/charts/Charts';
import { Card, ViewScroll } from '@/components/ui/Card';
import { toneText } from '@/components/ui/Chip';
import { SignalsPanel } from '@/components/views/SignalsPanel';
import { ConnectAppsButton } from '@/components/ViewNav';
import { EmptyState, ErrorState, InlineError, LoadingBlock } from '@/components/ui/States';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import { cn } from '@/lib/cn';
import { duration, relativeTime } from '@/lib/format';
import type { DashboardSeries, DashboardTile } from '@/lib/types';

function Tile({ tile }: { tile: DashboardTile }) {
  return (
    <Card className="p-4">
      <p className="text-[11px] uppercase tracking-wider text-ink-faint">{tile.label}</p>
      <p className={cn('serif mt-2 text-[1.9rem] leading-none', toneText(tile.tone))}>{tile.value}</p>
      <p className="mt-2 text-[12px] leading-relaxed text-ink-muted">{tile.detail}</p>
      <p className="mt-2 text-[10.5px] uppercase tracking-wider text-ink-faint">
        {String(tile.tone) === 'ok' ? 'healthy' : String(tile.tone) === 'warn' ? 'watch' : 'needs action'}
      </p>
    </Card>
  );
}

function SeriesCard({ series }: { series: DashboardSeries }) {
  return (
    <Card className="p-5">
      <h3 className="serif text-[1.05rem] text-ink">{series.label}</h3>
      <div className="mt-4">
        {String(series.kind) === 'line' ? (
          <LineChartSvg data={series.data} label={series.label} />
        ) : (
          <BarChartSvg data={series.data} label={series.label} />
        )}
      </div>
      {series.note ? (
        <p className="mt-3 border-t border-hairline pt-3 text-[12px] leading-relaxed text-ink-muted">
          {series.note}
        </p>
      ) : null}
    </Card>
  );
}

export function DashboardView() {
  const dashboard = useQuery({ queryKey: qk.dashboard, queryFn: () => duet.dashboard() });
  const agents = useQuery({ queryKey: qk.agents, queryFn: () => duet.agents() });
  const me = useQuery({ queryKey: qk.me, queryFn: () => duet.me() });

  const tiles = dashboard.data?.tiles ?? [];
  const series = dashboard.data?.series ?? [];
  const nothingConnected = me.isSuccess && me.data.counts.connections === 0;
  // The tiles are always four, even when every one of them is a zero: they are
  // derived from observed signals, so an empty chart series means there is
  // genuinely nothing to show and four zeros would be noise.
  const observations = series.reduce((total, entry) => total + entry.data.length, 0);
  const noNumbers = dashboard.isSuccess && observations === 0;

  return (
    <ViewScroll className="max-w-6xl">
      <div className="border-b border-hairline pb-6">
        <h1 className="serif text-[1.75rem] leading-tight text-ink sm:text-[2rem]">Executive dashboard</h1>
        <p className="mt-2 max-w-reading text-sm text-ink-muted">
          How much got caught, how much needed you, and how the specialists are holding up.
        </p>
      </div>

      {dashboard.isLoading ? <LoadingBlock label="Loading the numbers" /> : null}
      {dashboard.isError ? (
        <div className="mt-6">
          <ErrorState
            title="The dashboard is unavailable"
            detail={apiErrorMessage(dashboard.error)}
          />
        </div>
      ) : null}

      {noNumbers ? (
        <div className="mt-7">
          <EmptyState
            icon={<Gauge size={20} />}
            title="No data yet"
            hint="Every tile and chart here is computed from signals I actually observed in your apps — nothing is estimated and nothing is filled in for illustration. With no observations there is nothing to count."
            action={nothingConnected ? <ConnectAppsButton /> : undefined}
            footnote={
              nothingConnected
                ? undefined
                : 'Numbers appear after the first sync of a connected app finds something.'
            }
          />
        </div>
      ) : null}

      {tiles.length > 0 && !noNumbers ? (
        <div className="mt-7 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {tiles.map((tile, i) => (
            <Tile key={`${tile.label}-${i}`} tile={tile} />
          ))}
        </div>
      ) : null}

      {series.length > 0 && !noNumbers ? (
        <div className="mt-8 grid grid-cols-1 gap-4 xl:grid-cols-2">
          {series.map((entry, i) => (
            <SeriesCard key={`${entry.label}-${i}`} series={entry} />
          ))}
        </div>
      ) : null}

      <section className="mt-12 border-t border-hairline pt-8">
        <h2 className="serif text-lg text-ink">Specialists</h2>
        <p className="mt-1.5 text-[13px] text-ink-muted">
          The agents that do the work. Each one only gets the tools its job needs.
        </p>
        {agents.isLoading ? <LoadingBlock label="Loading the specialists" /> : null}
        {agents.isError ? (
          <InlineError className="mt-4">{apiErrorMessage(agents.error)}</InlineError>
        ) : null}
        <ul className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {(agents.data?.agents ?? []).map((agent) => (
            <li key={agent.name} className="rounded-xl border border-hairline bg-surface p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="font-mono text-[12.5px] text-accent">{agent.name}</p>
                <span className="text-[11px] text-ink-faint">{agent.status}</span>
              </div>
              <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-muted">{agent.role}</p>
              <p className="mt-2 text-[11px] text-ink-faint">
                {agent.runs} run{agent.runs === 1 ? '' : 's'} · avg {duration(agent.avg_ms)}
                {agent.last_run ? ` · last ${relativeTime(agent.last_run)}` : ''}
              </p>
              {agent.capabilities.length > 0 ? (
                <p className="mt-2 font-mono text-[10.5px] leading-relaxed text-ink-faint">
                  {agent.capabilities.join(' · ')}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      </section>

      <section className="mt-12 border-t border-hairline pt-8">
        <h2 className="serif text-lg text-ink">Latest signals</h2>
        <p className="mt-1.5 text-[13px] text-ink-muted">
          Everything the last sync picked up, scored before it reached you.
        </p>
        <div className="mt-4">
          <SignalsPanel />
        </div>
      </section>
    </ViewScroll>
  );
}
