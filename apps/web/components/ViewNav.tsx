'use client';

import { createContext, useContext, type ReactNode } from 'react';
import { Button, type ButtonVariant } from '@/components/ui/Button';
import type { ViewKey } from '@/components/nav';

type Navigate = (view: ViewKey) => void;

const ViewNavContext = createContext<Navigate | null>(null);

/**
 * Lets a view send someone somewhere else — in practice, to Apps.
 *
 * Empty states are only honest if they also say what to do next, and "connect
 * an app" is the answer in six of the eight views. Routing lives in `page.tsx`,
 * so it is handed down here rather than threaded through every view's props.
 */
export function ViewNavProvider({ navigate, children }: { navigate: Navigate; children: ReactNode }) {
  return <ViewNavContext.Provider value={navigate}>{children}</ViewNavContext.Provider>;
}

export function useViewNav(): Navigate {
  const navigate = useContext(ViewNavContext);
  // No provider means no shell; swallowing the click beats crashing a view.
  return navigate ?? (() => undefined);
}

/** The one call to action the empty product has. */
export function ConnectAppsButton({
  label = 'Connect your first app',
  variant = 'primary',
}: {
  label?: string;
  variant?: ButtonVariant;
}) {
  const navigate = useViewNav();
  return (
    <Button variant={variant} onClick={() => navigate('apps')}>
      {label}
    </Button>
  );
}
