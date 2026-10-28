'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LoaderCircle, Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { Card, ViewScroll } from '@/components/ui/Card';
import { Chip } from '@/components/ui/Chip';
import { SelectField, TextField } from '@/components/ui/Field';
import { ErrorState, InlineError, LoadingBlock } from '@/components/ui/States';
import { ResetPanel } from '@/components/views/ResetPanel';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import { hour12 } from '@/lib/format';
import type { RuleKind } from '@/lib/types';

const RULE_KINDS: RuleKind[] = ['vip', 'mute', 'boost', 'never'];

const RULE_HELP: Record<string, string> = {
  vip: 'Always reach me when this matches.',
  mute: 'Score it down — I will keep it for the brief.',
  boost: 'Nudge the score up without forcing a ping.',
  never: 'Never surface this, at any score.',
};

export function SettingsView() {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: qk.settings, queryFn: () => duet.settings() });

  const [start, setStart] = useState(21);
  const [end, setEnd] = useState(7);
  const [briefingHour, setBriefingHour] = useState(7);
  const [kind, setKind] = useState<RuleKind>('vip');
  const [pattern, setPattern] = useState('');
  const [weight, setWeight] = useState('');
  const [note, setNote] = useState('');

  useEffect(() => {
    const data = settings.data;
    if (!data) return;
    setStart(data.quiet_hours.start);
    setEnd(data.quiet_hours.end);
    setBriefingHour(data.briefing_hour);
  }, [settings.data]);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: qk.settings });
    void queryClient.invalidateQueries({ queryKey: qk.me });
  };

  const save = useMutation({
    mutationFn: () => duet.updateSettings({ quiet_hours: { start, end }, briefing_hour: briefingHour }),
    onSuccess: refresh,
  });

  const addRule = useMutation({
    mutationFn: () =>
      duet.addRule({
        kind,
        pattern: pattern.trim(),
        weight: weight ? Number(weight) : undefined,
        note: note.trim() || undefined,
      }),
    onSuccess: () => {
      setPattern('');
      setWeight('');
      setNote('');
      refresh();
    },
  });

  const removeRule = useMutation({
    mutationFn: (id: string) => duet.deleteRule(id),
    onSuccess: refresh,
  });

  const rules = settings.data?.rules ?? [];

  return (
    <ViewScroll>
      <div className="border-b border-hairline pb-6">
        <h1 className="serif text-[1.75rem] leading-tight text-ink sm:text-[2rem]">Settings</h1>
        <p className="mt-2 max-w-reading text-sm text-ink-muted">
          When I am allowed to interrupt, when the brief lands, and what counts as important.
        </p>
      </div>

      {settings.isLoading ? <LoadingBlock label="Loading settings" /> : null}
      {settings.isError ? (
        <div className="mt-6">
          <ErrorState title="Settings are unavailable" detail={apiErrorMessage(settings.error)} />
        </div>
      ) : null}

      <Card className="mt-7 p-5">
        <h2 className="serif text-[1.05rem] text-ink">Rhythm</h2>
        <p className="mt-1.5 text-[12.5px] text-ink-muted">
          Quiet from {hour12(start)} to {hour12(end)} · brief at {hour12(briefingHour)}.
        </p>
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <TextField
            label="Quiet hours start"
            type="number"
            min={0}
            max={23}
            value={start}
            onChange={(event) => setStart(Number(event.target.value))}
            hint="24-hour clock"
          />
          <TextField
            label="Quiet hours end"
            type="number"
            min={0}
            max={23}
            value={end}
            onChange={(event) => setEnd(Number(event.target.value))}
            hint="24-hour clock"
          />
          <TextField
            label="Briefing hour"
            type="number"
            min={0}
            max={23}
            value={briefingHour}
            onChange={(event) => setBriefingHour(Number(event.target.value))}
            hint="when the brief lands"
          />
        </div>
        <div className="mt-4 flex items-center gap-3">
          <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? <LoaderCircle size={14} className="animate-spin" /> : null}
            Save rhythm
          </Button>
          {save.isSuccess ? <span className="text-[12px] text-risk-safe">Saved.</span> : null}
          {save.isError ? (
            <span className="text-[12px] text-risk-sensitive">{apiErrorMessage(save.error)}</span>
          ) : null}
        </div>
      </Card>

      <section className="mt-10">
        <h2 className="serif text-[1.05rem] text-ink">Priority rules</h2>
        <p className="mt-1.5 max-w-reading text-[12.5px] text-ink-muted">
          Patterns match a sender, a channel, or a subject. {RULE_HELP[kind]}
        </p>

        <form
          className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-[8rem_minmax(0,1fr)_6rem_auto] sm:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            if (pattern.trim()) addRule.mutate();
          }}
        >
          <SelectField label="Kind" value={kind} onChange={(event) => setKind(event.target.value as RuleKind)}>
            {RULE_KINDS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </SelectField>
          <TextField
            label="Pattern"
            value={pattern}
            placeholder="ceo@acme.com, #incidents, invoice"
            onChange={(event) => setPattern(event.target.value)}
          />
          <TextField
            label="Weight"
            type="number"
            value={weight}
            placeholder="0"
            onChange={(event) => setWeight(event.target.value)}
          />
          <Button type="submit" variant="secondary" disabled={addRule.isPending || !pattern.trim()}>
            {addRule.isPending ? <LoaderCircle size={14} className="animate-spin" /> : <Plus size={14} />}
            Add rule
          </Button>
        </form>

        <TextField
          label="Note"
          wrapperClassName="mt-3 max-w-reading"
          value={note}
          placeholder="Why this rule exists (optional)"
          onChange={(event) => setNote(event.target.value)}
        />

        {addRule.isError ? (
          <InlineError className="mt-2">{apiErrorMessage(addRule.error)}</InlineError>
        ) : null}

        <ul className="mt-6 divide-y divide-hairline border-t border-hairline">
          {rules.length === 0 ? (
            <li className="py-5 text-[13px] text-ink-faint">No rules yet — I am scoring on signal alone.</li>
          ) : null}
          {rules.map((rule) => (
            <li key={rule.id} className="flex items-start gap-3 py-3.5">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Chip
                    className={
                      rule.kind === 'never' || rule.kind === 'mute'
                        ? 'border-risk-sensitive/30 bg-risk-sensitive/10 text-risk-sensitive'
                        : 'border-accent/30 bg-accent/10 text-accent'
                    }
                  >
                    {rule.kind}
                  </Chip>
                  <span className="font-mono text-[12.5px] text-ink">{rule.pattern}</span>
                  <span className="text-[11px] text-ink-faint">weight {rule.weight}</span>
                  {!rule.enabled ? <Chip>disabled</Chip> : null}
                </div>
                {rule.note ? <p className="mt-1 text-[12px] text-ink-faint">{rule.note}</p> : null}
              </div>
              <button
                type="button"
                aria-label={`Delete rule ${rule.pattern}`}
                onClick={() => removeRule.mutate(rule.id)}
                disabled={removeRule.isPending}
                className="rounded-lg p-1.5 text-ink-faint transition-colors hover:bg-raised hover:text-risk-sensitive"
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
        {removeRule.isError ? (
          <InlineError className="mt-3">{apiErrorMessage(removeRule.error)}</InlineError>
        ) : null}
      </section>

      <ResetPanel />
    </ViewScroll>
  );
}
