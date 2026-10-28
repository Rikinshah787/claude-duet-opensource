'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, KeyRound, LoaderCircle, ShieldCheck } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import type { Connector, OAuthStatus } from '@/lib/types';

/**
 * The API expires a pending authorization after ten minutes (STATE_TTL_S); once
 * it lapses a callback is refused, so there is nothing left to wait for.
 */
const SIGN_IN_WINDOW_MS = 10 * 60 * 1000;

/**
 * "Sign in with Google" — the connect path for apps that use OAuth.
 *
 * Why this is better than the credential form it replaces: an app password is
 * a long-lived secret with full mailbox access, typed by hand, where one wrong
 * digit produces a TCP timeout and no diagnosis. A grant is scoped, revocable
 * from the provider's own settings, and renews itself.
 *
 * The flow leaves this page: we open the provider in a new tab, the provider
 * redirects to the API's callback, and the API stores the grant. This tab never
 * sees the code, the token, or the secret — so there is nothing here to poll
 * for except the connection itself, which is what `waiting` watches.
 */
export function OAuthConnect({
  connector,
  status,
  onConnected,
}: {
  connector: Connector;
  status: Extract<OAuthStatus, { oauth: true }>;
  onConnected?: () => void;
}) {
  const queryClient = useQueryClient();
  const [waiting, setWaiting] = useState(false);
  const connectedRef = useRef(connector.connected);

  const start = useMutation({ mutationFn: () => duet.oauthStart(connector.key) });

  /**
   * Opens the provider. The tab is opened *synchronously inside the click*,
   * before the request that produces the URL: a `window.open` in a promise
   * callback is no longer attached to a user gesture and popup blockers eat
   * it, which looked exactly like the button doing nothing.
   *
   * `noopener` is deliberately omitted — it makes `window.open` return null,
   * and we need the handle to point the tab somewhere once the URL arrives.
   * The destination always comes from our own API and is a provider's HTTPS
   * authorize endpoint, so the opener reference it gains is not a concern.
   * If the browser blocks the tab anyway we fall back to this one, and the
   * callback page has a link back.
   */
  const beginSignIn = () => {
    const tab = window.open('about:blank', '_blank');
    setWaiting(true);
    start.mutate(undefined, {
      onSuccess: ({ url }) => {
        if (tab && !tab.closed) tab.location.href = url;
        else window.location.href = url;
      },
      onError: () => {
        setWaiting(false);
        tab?.close();
      },
    });
  };

  // The grant lands in the API, not in this tab, so the only way to notice is
  // to ask. Polling stops the moment the connection appears — or after the
  // state nonce has expired, because by then no callback can still succeed.
  useEffect(() => {
    if (!waiting) return;

    const deadline = Date.now() + SIGN_IN_WINDOW_MS;
    const poll = window.setInterval(() => {
      if (Date.now() > deadline) {
        setWaiting(false);
        return;
      }
      void queryClient.invalidateQueries({ queryKey: qk.connectors });
    }, 2000);

    // Coming back to this tab is the strongest hint that the user just
    // finished, so check immediately rather than waiting out the interval.
    const onFocus = () => void queryClient.invalidateQueries({ queryKey: qk.connectors });
    window.addEventListener('focus', onFocus);

    return () => {
      window.clearInterval(poll);
      window.removeEventListener('focus', onFocus);
    };
  }, [waiting, queryClient]);

  // Fires once, on the transition into connected — not on every render where
  // it happens to be true, which would re-close the sheet the user reopened.
  useEffect(() => {
    if (connector.connected && !connectedRef.current) {
      setWaiting(false);
      void queryClient.invalidateQueries({ queryKey: qk.me });
      onConnected?.();
    }
    connectedRef.current = connector.connected;
  }, [connector.connected, onConnected, queryClient]);

  if (!status.configured) {
    return <SetupNeeded connector={connector} status={status} />;
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h3 className="text-[11px] uppercase tracking-wider text-ink-faint">Connect</h3>
        <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">
          Sign in to {status.label} and approve the access below. No password is typed here, and
          you can revoke it from your {status.label} account at any time.
        </p>
      </div>

      <ul className="flex flex-col gap-1.5 rounded-lg border border-hairline bg-raised px-3 py-2.5">
        {status.scopes
          .filter((scope) => scope !== 'openid' && scope !== 'email')
          .map((scope) => (
            <li key={scope} className="flex items-start gap-2 text-[12.5px] leading-relaxed">
              <ShieldCheck size={13} className="mt-0.5 shrink-0 text-risk-safe" aria-hidden />
              <span className="min-w-0 text-ink-muted">{describeScope(scope)}</span>
            </li>
          ))}
      </ul>

      {start.isError ? (
        <p
          role="alert"
          className="rounded-lg border border-risk-sensitive/30 bg-risk-sensitive/10 px-3 py-2 text-[12.5px] leading-relaxed text-risk-sensitive"
        >
          {apiErrorMessage(start.error)}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" onClick={beginSignIn} disabled={start.isPending || waiting}>
          {start.isPending || waiting ? (
            <LoaderCircle size={14} className="animate-spin" />
          ) : (
            <KeyRound size={14} />
          )}
          {waiting ? `Waiting for ${status.label}…` : `Sign in with ${status.label}`}
        </Button>

        {waiting ? (
          <button
            type="button"
            onClick={() => setWaiting(false)}
            className="text-[12px] text-ink-faint underline-offset-4 hover:text-ink-muted hover:underline"
          >
            Cancel
          </button>
        ) : null}
      </div>

      {waiting ? (
        <p className="text-[12px] leading-relaxed text-ink-faint">
          {status.label} opened in a new tab. Approve the consent screen there and this panel
          updates on its own — if you closed the tab by mistake, cancel and start again.
        </p>
      ) : (
        <p className="text-[11.5px] leading-relaxed text-ink-faint">
          The grant goes straight into the vault. I am handed a reference, never the token, and
          disconnecting destroys it.
        </p>
      )}
    </div>
  );
}

/** Shown when the app supports signing in but this machine has no client yet. */
function SetupNeeded({
  connector,
  status,
}: {
  connector: Connector;
  status: Extract<OAuthStatus, { oauth: true }>;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div>
        <h3 className="text-[11px] uppercase tracking-wider text-ink-faint">
          One-time setup on this machine
        </h3>
        <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">
          {connector.name} connects by signing in to {status.label}, which needs an OAuth client of
          your own. It is your project, under your account, with your consent screen — so this part
          cannot be done for you. It takes about three minutes, once.
        </p>
      </div>

      <ol className="flex flex-col gap-2 text-[12.5px] leading-relaxed text-ink-muted">
        <li className="flex gap-2">
          <Step n={1} />
          <span className="min-w-0">
            Create a client at{' '}
            <a
              href={status.setup_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-accent underline-offset-4 hover:underline"
            >
              {new URL(status.setup_url).host}
              <ExternalLink size={11} aria-hidden />
            </a>
          </span>
        </li>
        <li className="flex gap-2">
          <Step n={2} />
          <span className="min-w-0">
            Register this exact redirect URI — the host has to match character for character:
            <code className="mt-1 block break-all rounded-md border border-hairline bg-raised px-2 py-1 font-mono text-[11.5px] text-ink">
              {status.redirect_uri}
            </code>
          </span>
        </li>
        <li className="flex gap-2">
          <Step n={3} />
          <span className="min-w-0">
            Put the id and secret in <code className="font-mono text-[11.5px]">apps/api/.env</code>,
            then restart the API:
            <code className="mt-1 block break-all rounded-md border border-hairline bg-raised px-2 py-1 font-mono text-[11.5px] text-ink">
              {status.client_id_env}=…
              <br />
              {status.client_secret_env}=…
            </code>
          </span>
        </li>
      </ol>

      <p className="text-[12px] leading-relaxed text-ink-faint">{status.setup_hint}</p>
    </div>
  );
}

function Step({ n }: { n: number }) {
  return (
    <span
      aria-hidden
      className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-hairline-strong text-[10px] text-ink-faint"
    >
      {n}
    </span>
  );
}

/**
 * Scope strings are not for reading. This says what each one lets me do, in
 * the first person, because consent you cannot understand is not consent.
 */
function describeScope(scope: string): string {
  const known: Record<string, string> = {
    'https://www.googleapis.com/auth/gmail.readonly': 'Read your mail, so I can tell you what needs you.',
    'https://www.googleapis.com/auth/gmail.compose':
      'Write drafts and send replies — every send still waits for your approval.',
    'https://www.googleapis.com/auth/calendar.readonly': 'Read your calendar, to know what today looks like.',
    'https://www.googleapis.com/auth/calendar.events': 'Create and move events, once you have approved it.',
    'channels:history': 'Read channel history, to catch what was addressed to you.',
    'groups:history': 'Read private channel history.',
    'im:history': 'Read direct messages, where things actually get asked.',
    'users:read': 'Look up who people are, so names mean something.',
    'chat:write': 'Post messages — each one waits for your approval first.',
  };
  return known[scope] ?? scope;
}
