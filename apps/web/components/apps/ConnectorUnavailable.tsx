'use client';

import { PlugZap } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import type { Connector } from '@/lib/types';

/**
 * What the sheet says about an app the catalogue lists but cannot connect.
 *
 * It states plainly that nothing is behind it, and leaves the Connect button
 * visible but disabled rather than hiding it — the affordance has to look
 * unavailable, not absent, or the page just seems to be missing a button.
 */
export function ConnectorUnavailable({ connector }: { connector: Connector }) {
  return (
    <section className="border-t border-hairline pt-5">
      <h3 className="text-[11px] uppercase tracking-wider text-ink-faint">
        Why you cannot connect this
      </h3>
      <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">
        {connector.name} is listed because it is an app I am meant to support, but no integration has
        been built for it. There is nothing behind the button: no credential would be accepted, nothing
        would be read, and no signal would ever arrive.
      </p>
      <p className="mt-2 text-[12.5px] leading-relaxed text-ink-faint">
        What you see above is the shape the integration will take, not something running today. It moves
        to &ldquo;Ready to connect&rdquo; once it exists.
      </p>
      {/* Title on the wrapper: a disabled button fires no mouse events. */}
      <span className="mt-4 inline-block" title={`There is no integration behind ${connector.name} yet.`}>
        <Button variant="primary" disabled>
          <PlugZap size={14} />
          Connect {connector.name}
        </Button>
      </span>
    </section>
  );
}
