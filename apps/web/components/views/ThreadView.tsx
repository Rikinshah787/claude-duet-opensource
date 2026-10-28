'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { MessageSquare } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Composer } from '@/components/thread/Composer';
import { FirstRun } from '@/components/thread/FirstRun';
import { MessageRow } from '@/components/thread/MessageRow';
import { ConnectAppsButton } from '@/components/ViewNav';
import { EmptyState, ErrorState, InlineError, LoadingBlock } from '@/components/ui/States';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import { timeValue } from '@/lib/format';
import type { Approval, BriefingSection, ThreadMessage, ThreadResponse } from '@/lib/types';

function draft(text: string): ThreadMessage {
  return {
    id: `optimistic-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    role: 'user',
    kind: 'text',
    content: text,
    task_id: null,
    approval_id: null,
    signal_id: null,
    proactive: false,
    meta: null,
    created_at: new Date().toISOString(),
  };
}

function pickSuggestionSection(sections: BriefingSection[]): BriefingSection | undefined {
  const first = sections.find((s) => /what i.?d do first|do first|next step/i.test(s.title));
  return first ?? sections[sections.length - 1] ?? sections[0];
}

export function ThreadView({ onOpenTask }: { onOpenTask: (taskId: string) => void }) {
  const queryClient = useQueryClient();
  const scroller = useRef<HTMLDivElement | null>(null);
  const [optimisticIds, setOptimisticIds] = useState<string[]>([]);

  const thread = useQuery({
    queryKey: qk.thread,
    queryFn: () => duet.thread(100),
    refetchInterval: 5000,
  });

  const me = useQuery({ queryKey: qk.me, queryFn: () => duet.me() });

  const approvalsQuery = useQuery({
    queryKey: qk.approvals('pending'),
    queryFn: () => duet.approvals('pending'),
    refetchInterval: 10000,
  });

  const briefing = useQuery({ queryKey: qk.briefing, queryFn: () => duet.latestBriefing() });

  const send = useMutation({
    mutationFn: (text: string) => duet.say(text),
    onMutate: async (text: string) => {
      await queryClient.cancelQueries({ queryKey: qk.thread });
      const message = draft(text);
      setOptimisticIds((prev) => [...prev, message.id]);
      queryClient.setQueryData<ThreadResponse>(qk.thread, (prev) =>
        prev ? { messages: [...prev.messages, message] } : { messages: [message] },
      );
      return { id: message.id };
    },
    onSettled: (_data, _error, _vars, context) => {
      const id = (context as { id?: string } | undefined)?.id;
      if (id) setOptimisticIds((prev) => prev.filter((value) => value !== id));
      void queryClient.invalidateQueries({ queryKey: qk.thread });
      void queryClient.invalidateQueries({ queryKey: qk.tasks });
      void queryClient.invalidateQueries({ queryKey: qk.me });
      void queryClient.invalidateQueries({ queryKey: ['approvals'] });
    },
  });

  const messages = useMemo(() => {
    const list = thread.data?.messages ?? [];
    return [...list].sort((a, b) => timeValue(a.created_at) - timeValue(b.created_at));
  }, [thread.data]);

  const approvals = useMemo(() => {
    const map = new Map<string, Approval>();
    for (const approval of approvalsQuery.data?.approvals ?? []) map.set(approval.id, approval);
    return map;
  }, [approvalsQuery.data]);

  const suggestions = useMemo(() => {
    const section = pickSuggestionSection(briefing.data?.briefing?.sections ?? []);
    return (section?.items ?? []).slice(0, 3).map((item) => item.action || item.title);
  }, [briefing.data]);

  useEffect(() => {
    const node = scroller.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages.length, optimisticIds.length]);

  const optimistic = new Set(optimisticIds);
  const firstRun = me.isSuccess && me.data.counts.connections === 0;
  const emptyThread = thread.isSuccess && messages.length === 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={scroller} className="duet-scroll min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-reading flex-col gap-7 px-4 py-7 sm:px-6 sm:py-9">
          {firstRun ? <FirstRun /> : null}

          {thread.isLoading ? <LoadingBlock label="Opening the thread" /> : null}

          {thread.isError ? (
            <ErrorState
              title="I can't reach the assistant"
              detail={`/api/thread did not answer. ${apiErrorMessage(thread.error)}`}
            />
          ) : null}

          {emptyThread && !firstRun ? (
            <EmptyState
              icon={<MessageSquare size={20} />}
              title="No messages yet"
              hint="I write here when something needs you — a decision waiting, a deadline slipping, a person stuck on your reply. Quiet means nothing in your connected apps has crossed that bar."
              action={<ConnectAppsButton label="Connect another app" variant="secondary" />}
              footnote="Or ask me something below."
            />
          ) : null}

          {emptyThread && firstRun ? (
            <EmptyState
              icon={<MessageSquare size={20} />}
              title="No messages yet"
              hint="Once an app is connected I will message you here the moment something needs you, and stay quiet the rest of the time."
              action={<ConnectAppsButton label="Go to Apps" variant="secondary" />}
            />
          ) : null}

          {messages.map((message) => (
            <MessageRow
              key={message.id}
              message={message}
              approvals={approvals}
              optimistic={optimistic.has(message.id)}
              onOpenTask={onOpenTask}
            />
          ))}

          {send.isError ? <InlineError>{apiErrorMessage(send.error)}</InlineError> : null}
        </div>
      </div>

      <Composer
        sending={send.isPending}
        suggestions={suggestions}
        onSend={(text) => send.mutate(text)}
      />
    </div>
  );
}
