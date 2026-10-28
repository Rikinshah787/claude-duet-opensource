'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Eye, EyeOff, LoaderCircle, PlugZap, ShieldAlert } from 'lucide-react';
import { useState } from 'react';
import { credentialSpec, type CredentialField } from '@/components/apps/credentials';
import { Button } from '@/components/ui/Button';
import { controlClass, Field } from '@/components/ui/Field';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import { cn } from '@/lib/cn';
import type { Connector } from '@/lib/types';

/**
 * Collects a credential and posts it to `POST /api/connectors/{key}/connect`.
 *
 * Rules this component exists to keep:
 *  - secrets live in local state and in the POST body, nowhere else;
 *  - no credential is ever logged, put in a URL, or written to storage;
 *  - on success the state is wiped, so nothing is left in the DOM to read.
 */
export function ConnectForm({
  connector,
  onConnected,
}: {
  connector: Connector;
  onConnected?: () => void;
}) {
  const spec = credentialSpec(connector);
  const queryClient = useQueryClient();

  const [values, setValues] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const field of spec.fields) if (field.value) initial[field.name] = field.value;
    return initial;
  });
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const [label, setLabel] = useState('');
  const [grantWrite, setGrantWrite] = useState(true);

  const connect = useMutation({
    mutationFn: () => {
      const credential: Record<string, string> = { type: spec.type };
      for (const field of spec.fields) {
        const value = values[field.name]?.trim();
        if (value) credential[field.name] = value;
      }
      return duet.connect(connector.key, {
        account_label: label.trim() || null,
        grant_write: grantWrite,
        credential,
      });
    },
    onSuccess: () => {
      // Drop every secret the moment it is no longer needed.
      setValues({});
      setShown({});
      setLabel('');
      void queryClient.invalidateQueries({ queryKey: qk.connectors });
      void queryClient.invalidateQueries({ queryKey: qk.me });
      onConnected?.();
    },
  });

  const missing = spec.fields.filter(
    (field) => !field.optional && !(values[field.name] ?? '').trim(),
  );
  const ready = missing.length === 0;

  const set = (name: string, value: string) =>
    setValues((previous) => ({ ...previous, [name]: value }));

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (ready && !connect.isPending) connect.mutate();
      }}
      className="flex flex-col gap-4"
    >
      <div>
        <h3 className="text-[11px] uppercase tracking-wider text-ink-faint">
          {connector.connected ? 'Replace the credential' : 'Connect'}
        </h3>
        <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">{spec.lead}</p>
        {spec.caveat ? (
          <p className="mt-2 flex gap-2 rounded-lg border border-risk-review/30 bg-risk-review/10 px-3 py-2 text-[12.5px] leading-relaxed text-risk-review">
            <ShieldAlert size={14} className="mt-0.5 shrink-0" aria-hidden />
            <span>{spec.caveat}</span>
          </p>
        ) : null}
      </div>

      {spec.fields.map((field) => (
        <CredentialInput
          key={field.name}
          field={field}
          value={values[field.name] ?? ''}
          shown={shown[field.name] ?? false}
          onChange={(value) => set(field.name, value)}
          onToggleShown={() =>
            setShown((previous) => ({ ...previous, [field.name]: !previous[field.name] }))
          }
        />
      ))}

      <Field label="Account label" hint="Optional — how this account is named in the audit log.">
        {(id) => (
          <input
            id={id}
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder={`${connector.name} (work)`}
            autoComplete="off"
            className={controlClass}
          />
        )}
      </Field>

      <label className="flex cursor-pointer gap-2.5 rounded-lg border border-hairline bg-raised px-3 py-2.5">
        <input
          type="checkbox"
          checked={grantWrite}
          onChange={(event) => setGrantWrite(event.target.checked)}
          className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-accent"
        />
        <span className="min-w-0">
          <span className="block text-[13px] text-ink">Let me act in {connector.name}</span>
          <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-faint">
            {grantWrite
              ? 'Write scopes are included. Every consequential action still waits for your approval.'
              : 'Read-only: I can watch this app and tell you what happened, but not act in it.'}
          </span>
        </span>
      </label>

      {connect.isError ? (
        <p
          role="alert"
          className="rounded-lg border border-risk-sensitive/30 bg-risk-sensitive/10 px-3 py-2 text-[12.5px] leading-relaxed text-risk-sensitive"
        >
          {apiErrorMessage(connect.error)}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" disabled={!ready || connect.isPending}>
          {connect.isPending ? (
            <LoaderCircle size={14} className="animate-spin" />
          ) : (
            <PlugZap size={14} />
          )}
          {connector.connected ? 'Save credential' : `Connect ${connector.name}`}
        </Button>
        {!ready ? (
          <span className="text-[12px] text-ink-faint">
            {missing.length === 1
              ? `${missing[0].label} is still empty.`
              : `${missing.length} fields still empty.`}
          </span>
        ) : null}
      </div>

      <p className="text-[11.5px] leading-relaxed text-ink-faint">
        Secrets go straight into the vault behind a reference. The assistant is handed the
        reference, never the secret, and disconnecting destroys it.
      </p>
    </form>
  );
}

function CredentialInput({
  field,
  value,
  shown,
  onChange,
  onToggleShown,
}: {
  field: CredentialField;
  value: string;
  shown: boolean;
  onChange: (value: string) => void;
  onToggleShown: () => void;
}) {
  const isSecret = field.kind === 'secret';
  const hint = field.optional ? [field.hint, 'Optional.'].filter(Boolean).join(' ') : field.hint;

  return (
    <Field label={field.label} hint={hint}>
      {(id) => (
        <div className="relative">
          <input
            id={id}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder={field.placeholder}
            type={isSecret ? (shown ? 'text' : 'password') : field.kind === 'number' ? 'number' : 'text'}
            inputMode={field.kind === 'number' ? 'numeric' : undefined}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
            className={cn(controlClass, isSecret && 'pr-10 font-mono text-[13px]')}
          />
          {isSecret ? (
            <button
              type="button"
              onClick={onToggleShown}
              aria-label={shown ? `Hide ${field.label}` : `Show ${field.label}`}
              aria-pressed={shown}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-ink-faint transition-colors hover:bg-surface hover:text-ink"
            >
              {shown ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          ) : null}
        </div>
      )}
    </Field>
  );
}
