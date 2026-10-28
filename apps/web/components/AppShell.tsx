'use client';

import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { HeaderBar } from '@/components/HeaderBar';
import { NAV_ITEMS, type NavItem, type ViewKey } from '@/components/nav';
import { duet, qk } from '@/lib/api';
import { cn } from '@/lib/cn';
import type { MeCounts } from '@/lib/types';

function badgeCount(item: NavItem, counts: MeCounts | undefined): number {
  if (!item.badge || !counts) return 0;
  return counts[item.badge] ?? 0;
}

export function AppShell({
  view,
  onView,
  children,
}: {
  view: ViewKey;
  onView: (next: ViewKey) => void;
  children: ReactNode;
}) {
  const me = useQuery({ queryKey: qk.me, queryFn: () => duet.me() });
  const counts = me.data?.counts;

  return (
    <div className="flex h-dvh overflow-hidden bg-canvas">
      {/* Desktop rail */}
      <nav
        aria-label="Primary"
        className="hidden w-[13.5rem] shrink-0 flex-col border-r border-hairline bg-surface/40 px-3 py-5 md:flex"
      >
        <p className="px-2 pb-4 text-[11px] uppercase tracking-[0.18em] text-ink-faint">Duet</p>
        <ul className="flex flex-col gap-0.5">
          {NAV_ITEMS.map((item) => {
            const active = item.key === view;
            const count = badgeCount(item, counts);
            return (
              <li key={item.key}>
                <button
                  type="button"
                  aria-current={active ? 'page' : undefined}
                  onClick={() => onView(item.key)}
                  className={cn(
                    'group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors',
                    active
                      ? 'bg-raised text-ink'
                      : 'text-ink-muted hover:bg-raised/60 hover:text-ink',
                  )}
                >
                  <item.icon
                    size={16}
                    strokeWidth={1.75}
                    className={active ? 'text-accent' : 'text-ink-faint group-hover:text-ink-muted'}
                  />
                  <span className="min-w-0 flex-1 truncate text-left">{item.label}</span>
                  {count > 0 ? (
                    <span className="rounded-md bg-accent/15 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-accent">
                      {count}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
        <div className="mt-auto px-2 pt-6 text-[11px] leading-relaxed text-ink-faint">
          <p className="text-ink-muted">{me.data?.name ?? 'Signed out'}</p>
          <p className="truncate">{me.data?.email ?? 'local-first · no cloud sync'}</p>
        </div>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        <HeaderBar />
        <main className="flex min-h-0 flex-1 flex-col">{children}</main>

        {/* Mobile tab bar: horizontally scrollable so every view stays reachable */}
        <nav
          aria-label="Primary"
          className="duet-scroll flex shrink-0 items-stretch overflow-x-auto border-t border-hairline bg-surface/80 backdrop-blur md:hidden"
        >
          {NAV_ITEMS.map((item) => {
            const active = item.key === view;
            const count = badgeCount(item, counts);
            return (
              <button
                key={item.key}
                type="button"
                aria-current={active ? 'page' : undefined}
                onClick={() => onView(item.key)}
                className={cn(
                  'relative flex min-w-[4.25rem] flex-1 flex-col items-center gap-1 py-2 text-[10px] transition-colors',
                  active ? 'text-ink' : 'text-ink-faint',
                )}
              >
                <item.icon size={18} strokeWidth={1.75} className={active ? 'text-accent' : undefined} />
                <span>{item.short}</span>
                {count > 0 ? (
                  <span className="absolute right-[18%] top-1.5 h-1.5 w-1.5 rounded-full bg-accent" />
                ) : null}
              </button>
            );
          })}
        </nav>
      </div>
    </div>
  );
}
