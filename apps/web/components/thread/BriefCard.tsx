'use client';

import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Sunrise } from 'lucide-react';
import { useState } from 'react';
import { BriefingSectionList } from '@/components/briefing/BriefingSections';
import { duet, qk } from '@/lib/api';
import { dayStamp } from '@/lib/format';

/** The `kind:"brief"` thread message: a card that opens into the full brief. */
export function BriefCard({ content }: { content: string }) {
  const [open, setOpen] = useState(false);
  const briefing = useQuery({ queryKey: qk.briefing, queryFn: () => duet.latestBriefing() });
  const brief = briefing.data?.briefing ?? null;
  const sections = brief?.sections ?? [];
  const itemCount = sections.reduce((sum, section) => sum + section.items.length, 0);

  return (
    <div className="rounded-xl border border-hairline bg-surface">
      <div className="px-4 pt-4 sm:px-5">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-ink-faint">
          <Sunrise size={13} className="text-accent" />
          Morning brief
          {brief?.for_date ? <span className="normal-case tracking-normal">· {dayStamp(brief.for_date)}</span> : null}
        </div>
        <h3 className="serif mt-2 text-[1.2rem] leading-snug text-ink">
          {brief?.headline || 'Your brief is ready.'}
        </h3>
        {content ? (
          <p className="mt-2 whitespace-pre-wrap text-[13px] leading-relaxed text-ink-muted">{content}</p>
        ) : null}
      </div>

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="mt-3 flex w-full items-center gap-1.5 border-t border-hairline px-4 py-2.5 text-[12px] text-ink-muted transition-colors hover:text-ink sm:px-5"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {open ? 'Collapse brief' : `Read the full brief${itemCount ? ` · ${itemCount} items` : ''}`}
      </button>

      {open ? (
        <div className="px-4 pb-5 sm:px-5">
          {sections.length === 0 ? (
            <p className="pt-4 text-[13px] text-ink-faint">
              {briefing.isLoading ? 'Loading the brief…' : 'No brief has been generated yet.'}
            </p>
          ) : (
            <BriefingSectionList sections={sections} />
          )}
        </div>
      ) : null}
    </div>
  );
}
