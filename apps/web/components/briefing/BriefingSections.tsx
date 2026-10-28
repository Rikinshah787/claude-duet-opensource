import { ExternalLink } from 'lucide-react';
import { ConnectorChip } from '@/components/apps/ConnectorChip';
import { Chip, RiskChip, ScoreBadge } from '@/components/ui/Chip';
import { cn } from '@/lib/cn';
import { relativeTime } from '@/lib/format';
import type { BriefingItem, BriefingSection } from '@/lib/types';

export function BriefingItemRow({ item }: { item: BriefingItem }) {
  return (
    <li className="flex gap-4 py-4">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <h4 className="text-[14px] font-medium leading-snug text-ink">{item.title}</h4>
          {item.when ? <span className="text-[11px] text-ink-faint">{relativeTime(item.when)}</span> : null}
        </div>

        {item.detail ? (
          <p className="mt-1 text-[13px] leading-relaxed text-ink-muted">{item.detail}</p>
        ) : null}

        {item.why ? (
          <p className="mt-1.5 text-[12px] italic leading-relaxed text-ink-faint">{item.why}</p>
        ) : null}

        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <ConnectorChip connectorKey={item.connector} />
          {item.actor ? <Chip className="normal-case tracking-normal">{item.actor}</Chip> : null}
          {item.kind ? <Chip className="bg-transparent">{item.kind}</Chip> : null}
          {item.risk ? <RiskChip tier={item.risk} /> : null}
          {item.action ? <Chip className="border-accent/30 bg-accent/10 text-accent">{item.action}</Chip> : null}
          {item.url ? (
            <a
              href={item.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-[11px] text-ink-faint underline-offset-4 hover:text-accent hover:underline"
            >
              Open <ExternalLink size={10} />
            </a>
          ) : null}
        </div>
      </div>

      <div className="shrink-0 pt-0.5 text-right">
        <ScoreBadge score={item.score} />
      </div>
    </li>
  );
}

export function BriefingSectionBlock({
  section,
  className,
}: {
  section: BriefingSection;
  className?: string;
}) {
  return (
    <section className={cn('border-t border-hairline pt-6', className)}>
      <h3 className="serif text-[1.1rem] text-ink">{section.title}</h3>
      {section.lead ? (
        <p className="mt-1 max-w-reading text-[13px] leading-relaxed text-ink-muted">{section.lead}</p>
      ) : null}

      {section.items.length === 0 ? (
        <p className="mt-3 text-[13px] text-ink-faint">Nothing here today.</p>
      ) : (
        <ul className="mt-2 divide-y divide-hairline">
          {section.items.map((item, i) => (
            <BriefingItemRow key={item.id ?? `${i}-${item.title}`} item={item} />
          ))}
        </ul>
      )}

      {section.truncated ? (
        <p className="pt-2 text-[12px] text-ink-faint">Trimmed — more below the fold in Signals.</p>
      ) : null}
    </section>
  );
}

export function BriefingSectionList({ sections }: { sections: BriefingSection[] }) {
  return (
    <div className="flex flex-col gap-8">
      {sections.map((section, i) => (
        <BriefingSectionBlock key={`${section.title}-${i}`} section={section} />
      ))}
    </div>
  );
}
