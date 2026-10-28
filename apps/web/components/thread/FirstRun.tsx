'use client';

import { LayoutGrid } from 'lucide-react';
import { ConnectAppsButton } from '@/components/ViewNav';

/**
 * Shown in the thread while `GET /api/me` reports no connections.
 *
 * One calm block, not a modal and not a wizard: what this is, and the single
 * action worth taking. It disappears the moment an app is connected.
 */
export function FirstRun() {
  return (
    <section
      aria-labelledby="first-run-title"
      className="animate-rise rounded-xl border border-hairline bg-surface px-5 py-6 sm:px-6"
    >
      <p className="flex items-center gap-2 text-[11px] uppercase tracking-[0.16em] text-ink-faint">
        <LayoutGrid size={13} className="text-accent" />
        Nothing connected yet
      </p>
      <h2 id="first-run-title" className="serif mt-3 text-[1.5rem] leading-tight text-ink sm:text-[1.7rem]">
        Connect an app and I start watching it
      </h2>
      <p className="mt-3 max-w-reading text-[13.5px] leading-relaxed text-ink-muted">
        I read the apps you connect, score what turns up, and write to you here only when something
        genuinely needs you. Everything else waits for the morning brief. Nothing consequential happens
        without your approval.
      </p>
      <p className="mt-2 max-w-reading text-[13.5px] leading-relaxed text-ink-muted">
        Until an app is connected there is nothing to read — no signals, no tasks, no brief. This is the
        only thing left to do.
      </p>
      <div className="mt-5">
        <ConnectAppsButton />
      </div>
    </section>
  );
}
