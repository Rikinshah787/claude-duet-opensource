import { asStringList, prettyJson, titleCase } from '@/lib/format';
import type { ApprovalPreview } from '@/lib/types';

const EMAIL_KEYS = new Set(['to', 'cc', 'bcc', 'subject', 'body', 'text']);

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3 border-b border-hairline py-1.5 text-[13px] last:border-b-0">
      <span className="w-20 shrink-0 text-ink-faint">{label}</span>
      <span className="min-w-0 flex-1 break-words text-ink">{value}</span>
    </div>
  );
}

function Quoted({ label, body }: { label: string; body: string }) {
  return (
    <figure className="mt-2">
      <figcaption className="pb-1.5 text-[11px] uppercase tracking-wider text-ink-faint">{label}</figcaption>
      <blockquote className="whitespace-pre-wrap border-l-2 border-accent/40 pl-3 text-[13px] leading-relaxed text-ink-muted">
        {body}
      </blockquote>
    </figure>
  );
}

/**
 * Renders an approval preview so a human can actually read it. Email-shaped
 * payloads get To / Subject / quoted Body; anything else falls back to a
 * readable key/value list with JSON for nested values.
 */
export function PreviewBlock({
  actionType,
  preview,
}: {
  actionType: string;
  preview: ApprovalPreview | null | undefined;
}) {
  const entries = Object.entries(preview ?? {}).filter(([, v]) => v !== null && v !== '');
  if (entries.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-hairline px-3 py-3 text-[13px] text-ink-faint">
        No preview was attached to this action.
      </p>
    );
  }

  const emailish =
    actionType.includes('email') || actionType.includes('mail') || 'subject' in (preview ?? {});
  const to = asStringList(preview?.to).join(', ');
  const cc = asStringList(preview?.cc).join(', ');
  const body = typeof preview?.body === 'string' ? preview.body : preview?.text;
  const rest = entries.filter(([k]) => !emailish || !EMAIL_KEYS.has(k));

  return (
    <div className="rounded-lg border border-hairline bg-canvas/60 px-3.5 py-3">
      {emailish ? (
        <div>
          {to ? <Row label="To" value={to} /> : null}
          {cc ? <Row label="Cc" value={cc} /> : null}
          {typeof preview?.subject === 'string' ? <Row label="Subject" value={preview.subject} /> : null}
        </div>
      ) : null}

      {emailish && typeof body === 'string' ? <Quoted label="Body" body={body} /> : null}

      {rest.length > 0 ? (
        <div className={emailish ? 'mt-3 border-t border-hairline pt-2' : ''}>
          {rest.map(([key, value]) => {
            const text = prettyJson(value);
            const multiline = text.includes('\n') || text.length > 90;
            if (multiline) {
              return <Quoted key={key} label={titleCase(key)} body={text} />;
            }
            return <Row key={key} label={titleCase(key)} value={text} />;
          })}
        </div>
      ) : null}
    </div>
  );
}
