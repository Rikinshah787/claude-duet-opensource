'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { LoaderCircle, TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { InlineError } from '@/components/ui/States';
import { apiErrorMessage, duet } from '@/lib/api';
import { titleCase } from '@/lib/format';
import type { AdminResetResponse } from '@/lib/types';

/** Spelled out so nobody can click through this without having read it. */
const DESTROYS: readonly string[] = [
  'Every signal observed in every app.',
  'Every task, with its full step-by-step trace.',
  'Every approval — pending, approved, rejected and reverted alike.',
  'Every message in the thread, yours and mine.',
  'Everything I have remembered about how you work.',
  'Every app connection, and the credential behind each one, erased from the vault.',
];

function deletedSummary(result: AdminResetResponse): string {
  const parts = Object.entries(result.deleted)
    .filter(([, count]) => count > 0)
    .map(([name, count]) => `${count} ${titleCase(name).toLowerCase()}`);
  return parts.length > 0 ? `Deleted ${parts.join(', ')}.` : 'There was nothing left to delete.';
}

/**
 * `POST /api/admin/reset`, behind two deliberate steps.
 *
 * This lives in Settings and nowhere else. It is not a refresh and not a
 * sync: it empties the account and destroys stored credentials, so it is
 * never one click away from the header.
 */
export function ResetPanel() {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  const reset = useMutation<AdminResetResponse, Error>({
    mutationFn: () => duet.adminReset(),
    onSuccess: () => {
      setConfirming(false);
      void queryClient.invalidateQueries();
    },
  });

  return (
    <Card className="mt-10 border-risk-sensitive/30 p-5">
      <h2 className="serif flex items-center gap-2 text-[1.05rem] text-ink">
        <TriangleAlert size={15} className="text-risk-sensitive" aria-hidden />
        Erase everything
      </h2>
      <p className="mt-1.5 max-w-reading text-[12.5px] leading-relaxed text-ink-muted">
        Returns this account to the state it was in before you connected anything.
      </p>

      {confirming ? (
        <div className="mt-4">
          <p className="text-[13px] font-medium text-ink">This permanently destroys:</p>
          <ul className="mt-2 flex max-w-reading list-disc flex-col gap-1 pl-5 text-[12.5px] leading-relaxed text-ink-muted">
            {DESTROYS.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="mt-3 max-w-reading text-[12.5px] leading-relaxed text-risk-sensitive">
            It cannot be undone. There is no backup and no export first — once this runs, every app has
            to be reconnected with a freshly issued credential.
          </p>
          <p className="mt-2 max-w-reading text-[12px] leading-relaxed text-ink-faint">
            The audit log is the one thing kept, including the entry recording this reset.
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button variant="danger" disabled={reset.isPending} onClick={() => reset.mutate()}>
              {reset.isPending ? <LoaderCircle size={14} className="animate-spin" /> : null}
              Yes — erase it all permanently
            </Button>
            <Button variant="ghost" disabled={reset.isPending} onClick={() => setConfirming(false)}>
              Keep my data
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-4">
          <Button variant="danger" onClick={() => setConfirming(true)}>
            Erase everything…
          </Button>
        </div>
      )}

      {reset.isError ? (
        <InlineError className="mt-4">{apiErrorMessage(reset.error)}</InlineError>
      ) : null}

      {reset.isSuccess ? (
        <p role="status" className="mt-4 text-[12.5px] leading-relaxed text-ink-muted">
          {deletedSummary(reset.data)} Every app is disconnected.
        </p>
      ) : null}
    </Card>
  );
}
