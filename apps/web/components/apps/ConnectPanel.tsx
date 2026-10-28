'use client';

import { useState } from 'react';
import { ConnectForm } from '@/components/apps/ConnectForm';
import { OAuthConnect } from '@/components/apps/OAuthConnect';
import type { Connector } from '@/lib/types';

/**
 * Picks how an app gets connected: by signing in, or by pasting a credential.
 *
 * The choice is not cosmetic. The Gmail adapter speaks to the Gmail API with a
 * grant and nothing else, so an app password pasted there would be accepted by
 * the vault and then fail on first use — a connection that reports success and
 * reads no mail. Apps like that say `oauth.required`, and for them the form is
 * not offered at all. Slack takes either, so it gets both with sign-in first.
 */
export function ConnectPanel({
  connector,
  onConnected,
}: {
  connector: Connector;
  onConnected?: () => void;
}) {
  const oauth = connector.oauth?.oauth ? connector.oauth : null;
  const [mode, setMode] = useState<'auto' | 'token' | 'signin'>('auto');

  if (!oauth) {
    return <ConnectForm key={connector.key} connector={connector} onConnected={onConnected} />;
  }

  // Signing in is the only path that works. Showing a token box beside it
  // would be offering a choice where one of the options is broken.
  if (oauth.required) {
    return <OAuthConnect connector={connector} status={oauth} onConnected={onConnected} />;
  }

  // Sign-in is possible but this machine has no client yet, so the credential
  // form is the path that works today. Lead with it and keep setup one click
  // away rather than blocking on a three-minute errand.
  const signInReady = oauth.configured || mode === 'signin';

  if (!signInReady || mode === 'token') {
    return (
      <div className="flex flex-col gap-3">
        <ConnectForm key={connector.key} connector={connector} onConnected={onConnected} />
        <button
          type="button"
          onClick={() => setMode(mode === 'token' ? 'auto' : 'signin')}
          className="self-start text-[12px] text-ink-faint underline-offset-4 hover:text-ink-muted hover:underline"
        >
          {mode === 'token'
            ? `Sign in with ${oauth.label} instead`
            : `Prefer to sign in with ${oauth.label}? Set it up once`}
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <OAuthConnect connector={connector} status={oauth} onConnected={onConnected} />
      <button
        type="button"
        onClick={() => setMode('token')}
        className="self-start text-[12px] text-ink-faint underline-offset-4 hover:text-ink-muted hover:underline"
      >
        Paste a token instead
      </button>
    </div>
  );
}
