'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LoaderCircle, Sunrise } from 'lucide-react';
import { BriefingSectionList } from '@/components/briefing/BriefingSections';
import { ConnectAppsButton } from '@/components/ViewNav';
import { Button } from '@/components/ui/Button';
import { ViewScroll } from '@/components/ui/Card';
import { EmptyState, ErrorState, InlineError, LoadingBlock } from '@/components/ui/States';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import { dayStamp, relativeTime } from '@/lib/format';

export function BriefingView() {
  const queryClient = useQueryClient();
  const briefing = useQuery({ queryKey: qk.briefing, queryFn: () => duet.latestBriefing() });
  const me = useQuery({ queryKey: qk.me, queryFn: () => duet.me() });

  const generate = useMutation({
    mutationFn: () => duet.generateBriefing(),
    onSuccess: (data) => {
      queryClient.setQueryData(qk.briefing, { briefing: data.briefing });
      void queryClient.invalidateQueries({ queryKey: qk.thread });
      void queryClient.invalidateQueries({ queryKey: qk.me });
    },
  });

  const brief = briefing.data?.briefing ?? null;
  const nothingConnected = me.isSuccess && me.data.counts.connections === 0;
  const noBrief = briefing.isSuccess && !brief;

  return (
    <ViewScroll>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-[11px] uppercase tracking-[0.16em] text-ink-faint">
            <Sunrise size={13} className="text-accent" />
            Morning brief
          </p>
          <p className="mt-1.5 text-[13px] text-ink-muted">
            {brief ? dayStamp(brief.for_date) : 'No brief yet'}
            {brief ? ` · written ${relativeTime(brief.created_at)}` : ''}
          </p>
        </div>
        {/* Title on the wrapper: a disabled button fires no mouse events. */}
        <span
          title={
            nothingConnected ? 'Connect an app first — there is nothing to summarise yet.' : undefined
          }
        >
          <Button
            variant="primary"
            onClick={() => generate.mutate()}
            disabled={generate.isPending || nothingConnected}
          >
            {generate.isPending ? <LoaderCircle size={15} className="animate-spin" /> : null}
            Write one now
          </Button>
        </span>
      </div>

      {brief ? (
        <h1 className="serif mt-7 max-w-reading text-[1.9rem] leading-[1.2] text-ink sm:text-[2.4rem]">
          {brief.headline}
        </h1>
      ) : null}

      {generate.isError ? <InlineError className="mt-6">{apiErrorMessage(generate.error)}</InlineError> : null}

      <div className="mt-9">
        {briefing.isLoading ? <LoadingBlock label="Reading this morning's brief" /> : null}

        {briefing.isError ? (
          <ErrorState
            title="The brief is unavailable"
            detail={apiErrorMessage(briefing.error)}
          />
        ) : null}

        {noBrief && nothingConnected ? (
          <EmptyState
            icon={<Sunrise size={20} />}
            title="No brief yet"
            hint="Every morning I write one of these from what your connected apps turned up overnight — who needs an answer, what is about to slip, what can wait. With nothing connected there is nothing to summarise."
            action={<ConnectAppsButton />}
          />
        ) : null}

        {noBrief && !nothingConnected ? (
          <EmptyState
            icon={<Sunrise size={20} />}
            title="No brief yet"
            hint="I write one each morning from everything your connected apps turned up overnight. None has been written for today — you can have one now."
            action={
              <Button variant="secondary" onClick={() => generate.mutate()} disabled={generate.isPending}>
                Write today's brief
              </Button>
            }
          />
        ) : null}

        {brief && brief.sections.length === 0 ? (
          <EmptyState
            title="Quiet morning"
            hint="The brief was written, but nothing your apps reported crossed the bar for a section today."
          />
        ) : null}

        {brief && brief.sections.length > 0 ? <BriefingSectionList sections={brief.sections} /> : null}
      </div>
    </ViewScroll>
  );
}
