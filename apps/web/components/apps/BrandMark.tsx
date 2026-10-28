import type { CSSProperties, ReactNode } from 'react';
import { brandPaint, getBrand } from '@/lib/brand';
import { cn } from '@/lib/cn';

/** CSS custom properties are not in React's `CSSProperties`, so widen it. */
type PaintStyle = CSSProperties & Record<`--${string}`, string>;

/**
 * Both themes are resolved up front and handed to CSS as custom properties.
 * `.brand-tile` / `.brand-mark` in globals.css pick the right pair, so the
 * markup is identical on the server and in either theme.
 */
function paint(hex: string | null | undefined): PaintStyle {
  const p = brandPaint(hex);
  return {
    '--brand-fg-dark': p.fgDark,
    '--brand-bg-dark': p.bgDark,
    '--brand-fg-light': p.fgLight,
    '--brand-bg-light': p.bgLight,
  };
}

function Mark({ path, title, className }: { path: string; title: string; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      role="img"
      fill="currentColor"
      className={cn('shrink-0', className)}
      xmlns="http://www.w3.org/2000/svg"
    >
      <title>{title}</title>
      <path d={path} />
    </svg>
  );
}

export type BrandTileProps = {
  /** Connector key from `GET /api/connectors`, e.g. `google_sheets`. */
  connectorKey: string;
  /** Letter glyph the API sends, shown when there is no brand mark. */
  glyph?: string;
  /** Brand colour the API sends, used for the glyph fallback. */
  color?: string | null;
  /** Geometry: size, radius, border, hover. */
  className?: string;
  /** Size of the mark inside the tile. */
  markClassName?: string;
  /** Extra content, e.g. the connected dot. */
  children?: ReactNode;
  /**
   * Defaults to decorative: everywhere we use this the connector's name is
   * already in the accessible name of the button, heading or chip beside it.
   */
  decorative?: boolean;
};

/**
 * A connector's square: the real brand mark where simple-icons has one, and
 * the catalogue's letter glyph where it does not. Never blank, never broken.
 */
export function BrandTile({
  connectorKey,
  glyph,
  color,
  className,
  markClassName = 'h-[42%] w-[42%]',
  children,
  decorative = true,
}: BrandTileProps) {
  const brand = getBrand(connectorKey);

  return (
    <span
      aria-hidden={decorative || undefined}
      className={cn('brand-tile relative grid place-items-center', className)}
      style={paint(brand?.hex ?? color)}
    >
      {brand ? (
        <Mark path={brand.path} title={brand.title} className={markClassName} />
      ) : (
        // Catalogue glyphs are not all one character: "365", "DS", "{}".
        <span
          className={cn(
            'font-semibold leading-none',
            (glyph?.length ?? 1) > 2 && 'text-[0.62em]',
            glyph?.length === 2 && 'text-[0.78em]',
          )}
        >
          {glyph || connectorKey.slice(0, 1).toUpperCase()}
        </span>
      )}
      {children}
    </span>
  );
}

/**
 * Just the mark, coloured for legibility but with no tile behind it — for
 * chips and other dense places. Returns `null` when there is no brand mark,
 * so callers fall back to their plain text label.
 */
export function BrandGlyph({
  connectorKey,
  className = 'h-3 w-3',
  decorative = true,
}: {
  connectorKey: string;
  className?: string;
  decorative?: boolean;
}) {
  const brand = getBrand(connectorKey);
  if (!brand) return null;
  return (
    <span aria-hidden={decorative || undefined} className="brand-mark inline-flex" style={paint(brand.hex)}>
      <Mark path={brand.path} title={brand.title} className={className} />
    </span>
  );
}
