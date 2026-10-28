'use client';

import { LoaderCircle, Send } from 'lucide-react';
import { useState, type KeyboardEvent } from 'react';

export function Composer({
  onSend,
  suggestions,
  sending,
}: {
  onSend: (text: string) => void;
  suggestions: string[];
  sending: boolean;
}) {
  const [text, setText] = useState('');

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setText('');
    onSend(trimmed);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="shrink-0 border-t border-hairline bg-canvas/95 backdrop-blur">
      <div className="mx-auto w-full max-w-reading px-4 py-3 sm:px-6 sm:py-4">
        {suggestions.length > 0 ? (
          <ul className="duet-scroll mb-2.5 flex gap-2 overflow-x-auto pb-1">
            {suggestions.slice(0, 3).map((suggestion) => (
              <li key={suggestion} className="shrink-0">
                <button
                  type="button"
                  disabled={sending}
                  onClick={() => onSend(suggestion)}
                  className="max-w-[18rem] truncate rounded-lg border border-hairline bg-surface px-2.5 py-1.5 text-left text-[12px] text-ink-muted transition-colors hover:border-accent/40 hover:text-ink"
                  title={suggestion}
                >
                  {suggestion}
                </button>
              </li>
            ))}
          </ul>
        ) : null}

        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <label htmlFor="duet-composer" className="sr-only">
            Message Duet
          </label>
          <textarea
            id="duet-composer"
            rows={1}
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask for something, or tell me what matters today…"
            className="duet-scroll max-h-40 min-h-[2.75rem] w-full resize-y rounded-xl border border-hairline bg-surface px-3.5 py-3 text-[14px] leading-snug text-ink placeholder:text-ink-faint focus:border-hairline-strong"
          />
          <button
            type="submit"
            disabled={sending || text.trim().length === 0}
            aria-label="Send message"
            className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-accent text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {sending ? <LoaderCircle size={16} className="animate-spin" /> : <Send size={16} />}
          </button>
        </form>
        <p className="pt-2 text-[11px] text-ink-faint">
          Enter sends · Shift+Enter adds a line. Every consequential action waits for your approval.
        </p>
      </div>
    </div>
  );
}
