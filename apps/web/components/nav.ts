import {
  Gauge,
  LayoutGrid,
  MessageSquare,
  ScrollText,
  Settings,
  ShieldCheck,
  Sunrise,
  Workflow,
  type LucideIcon,
} from 'lucide-react';

export type ViewKey =
  | 'thread'
  | 'briefing'
  | 'tasks'
  | 'approvals'
  | 'apps'
  | 'dashboard'
  | 'activity'
  | 'settings';

export type NavItem = {
  key: ViewKey;
  label: string;
  short: string;
  icon: LucideIcon;
  badge?: 'pending_approvals' | 'running_tasks';
};

export const NAV_ITEMS: readonly NavItem[] = [
  { key: 'thread', label: 'Thread', short: 'Thread', icon: MessageSquare },
  { key: 'briefing', label: 'Briefing', short: 'Brief', icon: Sunrise },
  { key: 'tasks', label: 'Tasks', short: 'Tasks', icon: Workflow, badge: 'running_tasks' },
  { key: 'approvals', label: 'Approvals', short: 'Approve', icon: ShieldCheck, badge: 'pending_approvals' },
  { key: 'apps', label: 'Apps', short: 'Apps', icon: LayoutGrid },
  { key: 'dashboard', label: 'Dashboard', short: 'Metrics', icon: Gauge },
  { key: 'activity', label: 'Activity', short: 'Log', icon: ScrollText },
  { key: 'settings', label: 'Settings', short: 'Settings', icon: Settings },
];


