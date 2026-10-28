'use client';

import { useState } from 'react';
import { AppShell } from '@/components/AppShell';
import type { ViewKey } from '@/components/nav';
import { ViewNavProvider } from '@/components/ViewNav';
import { ActivityView } from '@/components/views/ActivityView';
import { AppsView } from '@/components/views/AppsView';
import { ApprovalsView } from '@/components/views/ApprovalsView';
import { BriefingView } from '@/components/views/BriefingView';
import { DashboardView } from '@/components/views/DashboardView';
import { SettingsView } from '@/components/views/SettingsView';
import { TasksView } from '@/components/views/TasksView';
import { ThreadView } from '@/components/views/ThreadView';

export default function Page() {
  const [view, setView] = useState<ViewKey>('thread');
  const [taskId, setTaskId] = useState<string | null>(null);

  const openTask = (id: string) => {
    setTaskId(id);
    setView('tasks');
  };

  const render = () => {
    switch (view) {
      case 'thread':
        return <ThreadView onOpenTask={openTask} />;
      case 'briefing':
        return <BriefingView />;
      case 'tasks':
        return <TasksView taskId={taskId} onSelectTask={setTaskId} />;
      case 'approvals':
        return <ApprovalsView />;
      case 'apps':
        return <AppsView />;
      case 'dashboard':
        return <DashboardView />;
      case 'activity':
        return <ActivityView />;
      case 'settings':
        return <SettingsView />;
      default:
        return <ThreadView onOpenTask={openTask} />;
    }
  };

  return (
    <ViewNavProvider navigate={setView}>
      <AppShell view={view} onView={setView}>
        {render()}
      </AppShell>
    </ViewNavProvider>
  );
}
