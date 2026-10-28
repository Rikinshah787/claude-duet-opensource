'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, LoaderCircle, Plus, Workflow } from 'lucide-react';
import { useState } from 'react';
import { TaskTrace } from '@/components/views/TaskTrace';
import { Button } from '@/components/ui/Button';
import { ViewScroll } from '@/components/ui/Card';
import { StatusChip } from '@/components/ui/Chip';
import { EmptyState, ErrorState, InlineError, LoadingBlock } from '@/components/ui/States';
import { apiErrorMessage, duet, qk } from '@/lib/api';
import { cn } from '@/lib/cn';
import { relativeTime } from '@/lib/format';

export function TasksView({
  taskId,
  onSelectTask,
}: {
  taskId: string | null;
  onSelectTask: (id: string | null) => void;
}) {
  const queryClient = useQueryClient();
  const [intent, setIntent] = useState('');

  const tasks = useQuery({
    queryKey: qk.tasks,
    queryFn: () => duet.tasks(undefined, 50),
    refetchInterval: 8000,
  });

  const create = useMutation({
    mutationFn: (value: string) => duet.createTask(value),
    onSuccess: (data) => {
      setIntent('');
      onSelectTask(data.task.id);
      void queryClient.invalidateQueries({ queryKey: qk.tasks });
      void queryClient.invalidateQueries({ queryKey: qk.thread });
    },
  });

  const list = tasks.data?.tasks ?? [];
  const selected = taskId ?? null;

  return (
    <ViewScroll className="max-w-6xl">
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-hairline pb-6">
        <div>
          <h1 className="serif text-[1.75rem] leading-tight text-ink sm:text-[2rem]">Tasks</h1>
          <p className="mt-2 max-w-reading text-sm text-ink-muted">
            Multi-step work run by the specialist agents. Open one to watch the trace — every tool call,
            in order, with what it returned.
          </p>
        </div>
      </div>

      <form
        className="mt-6 flex flex-wrap items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          const trimmed = intent.trim();
          if (trimmed) create.mutate(trimmed);
        }}
      >
        <label htmlFor="task-intent" className="sr-only">
          New task intent
        </label>
        <input
          id="task-intent"
          value={intent}
          onChange={(event) => setIntent(event.target.value)}
          placeholder="Hand me something to run…"
          className="min-w-0 flex-1 rounded-lg border border-hairline bg-surface px-3 py-2 text-[13px] text-ink placeholder:text-ink-faint"
        />
        <Button type="submit" variant="secondary" disabled={create.isPending || !intent.trim()}>
          {create.isPending ? <LoaderCircle size={14} className="animate-spin" /> : <Plus size={14} />}
          Run it
        </Button>
      </form>

      {create.isError ? (
        <InlineError className="mt-3">{apiErrorMessage(create.error)}</InlineError>
      ) : null}

      <div className="mt-8 grid gap-6 lg:grid-cols-[21rem_minmax(0,1fr)]">
        <div className={cn(selected && 'hidden lg:block')}>
          {tasks.isLoading ? <LoadingBlock label="Loading tasks" /> : null}
          {tasks.isError ? (
            <ErrorState title="Tasks are unavailable" detail={apiErrorMessage(tasks.error)} />
          ) : null}
          {tasks.isSuccess && list.length === 0 ? (
            <EmptyState
              icon={<Workflow size={20} />}
              title="Nothing queued"
              hint="Tasks are the multi-step work I run for you — drafting, chasing, reconciling. Nothing is queued and nothing is running."
              footnote="Ask for something in the thread, or hand me an intent above."
            />
          ) : null}

          <ul className="flex flex-col gap-2">
            {list.map((task) => {
              const active = task.id === selected;
              return (
                <li key={task.id}>
                  <button
                    type="button"
                    onClick={() => onSelectTask(task.id)}
                    aria-current={active ? 'true' : undefined}
                    className={cn(
                      'group w-full rounded-xl border px-3.5 py-3 text-left transition-colors',
                      active
                        ? 'border-accent/40 bg-surface'
                        : 'border-hairline bg-surface/60 hover:border-hairline-strong',
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="min-w-0 text-[13.5px] font-medium leading-snug text-ink">{task.title}</p>
                      <ArrowRight
                        size={13}
                        className="mt-0.5 shrink-0 text-ink-faint opacity-0 transition-opacity group-hover:opacity-100"
                      />
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <StatusChip status={task.status} />
                      <span className="font-mono text-[11px] text-ink-faint">{task.lead_agent}</span>
                      <span className="text-[11px] text-ink-faint">{relativeTime(task.created_at)}</span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>

        <div className={cn(!selected && 'hidden lg:block')}>
          {selected ? (
            <>
              <button
                type="button"
                onClick={() => onSelectTask(null)}
                className="mb-3 text-[12px] text-ink-faint hover:text-ink lg:hidden"
              >
                ← Back to tasks
              </button>
              <TaskTrace taskId={selected} />
            </>
          ) : (
            <EmptyState
              icon={<Workflow size={20} />}
              title="Pick a task"
              hint="The trace shows each agent's step, what it called, how long it took, and the raw output."
              className="h-full"
            />
          )}
        </div>
      </div>
    </ViewScroll>
  );
}
