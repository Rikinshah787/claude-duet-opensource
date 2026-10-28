import type { Metadata, Viewport } from 'next';
import type { ReactNode } from 'react';
import './globals.css';
import { Providers } from './providers';

export const metadata: Metadata = {
  title: 'Claude Duet',
  description:
    'A local-first proactive executive assistant: it watches your tools, tells you only what matters, and asks before it acts.',
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: [
    { media: '(prefers-color-scheme: dark)', color: '#0F0F0E' },
    { media: '(prefers-color-scheme: light)', color: '#FAF9F6' },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="h-full bg-canvas font-sans text-ink antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
