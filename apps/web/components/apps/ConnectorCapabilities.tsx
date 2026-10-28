import { Chip, RiskChip } from '@/components/ui/Chip';
import { titleCase } from '@/lib/format';
import type { Connector } from '@/lib/types';

/** What an app lets me see and what it lets me do, straight from its spec. */
export function ConnectorCapabilities({ connector }: { connector: Connector }) {
  return (
    <>
      <section>
        <h3 className="text-[11px] uppercase tracking-wider text-ink-faint">What I watch</h3>
        {connector.signal_kinds.length === 0 ? (
          <p className="mt-2 text-[13px] text-ink-faint">Nothing — this one is write-only.</p>
        ) : (
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {connector.signal_kinds.map((kind) => (
              <li key={kind}>
                <Chip className="normal-case tracking-normal">{kind}</Chip>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3 className="text-[11px] uppercase tracking-wider text-ink-faint">What I can do</h3>
        {connector.writes.length === 0 ? (
          <p className="mt-2 text-[13px] text-ink-faint">Read-only. I can look, but not touch.</p>
        ) : (
          <ul className="mt-2 divide-y divide-hairline">
            {connector.writes.map((write) => (
              <li key={write.key} className="py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-[13px] font-medium text-ink">{write.label || titleCase(write.key)}</p>
                  <RiskChip tier={write.risk} />
                  <Chip className="bg-transparent">{write.reversible ? 'reversible' : 'one-way'}</Chip>
                </div>
                {write.note ? (
                  <p className="mt-1 text-[12.5px] leading-relaxed text-ink-faint">{write.note}</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
