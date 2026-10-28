/** Formatting helpers shared across views. All of them tolerate bad input. */

/**
 * The API sends naive ISO timestamps (no offset) that are actually UTC.
 * JavaScript reads those as local time, so pin them to UTC before parsing.
 */
function parse(value: string | null | undefined): Date | null {
  if (!value) return null;
  const naive = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(value);
  const d = new Date(naive ? `${value.replace(' ', 'T')}Z` : value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * Milliseconds since the epoch, for sorting. Always use this rather than
 * `new Date(x).getTime()` on an API timestamp.
 *
 * The thread holds two shapes at once: naive strings from the API, which are
 * UTC, and the offset-bearing string an optimistic draft makes locally. A raw
 * `new Date` reads the first as local time and the second as UTC, so a message
 * sent now sorted hours away from the identical one the server echoed back -
 * which looked like sending a message and getting nothing.
 *
 * Unparseable input sorts to the epoch, which keeps the order stable instead of
 * handing `NaN` to a comparator.
 */
export function timeValue(value: string | null | undefined): number {
  const d = parse(value);
  return d ? d.getTime() : 0;
}

export function relativeTime(value: string | null | undefined): string {
  const d = parse(value);
  if (!d) return '';
  const diff = Date.now() - d.getTime();
  const abs = Math.abs(diff);
  const min = Math.round(abs / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return diff >= 0 ? `${min}m ago` : `in ${min}m`;
  const hrs = Math.round(min / 60);
  if (hrs < 24) return diff >= 0 ? `${hrs}h ago` : `in ${hrs}h`;
  const days = Math.round(hrs / 24);
  if (days < 7) return diff >= 0 ? `${days}d ago` : `in ${days}d`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function clockTime(value: string | null | undefined): string {
  const d = parse(value);
  if (!d) return '';
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

export function dayStamp(value: string | null | undefined): string {
  const d = parse(value);
  if (!d) return '';
  return d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
}

export function dateTimeStamp(value: string | null | undefined): string {
  const d = parse(value);
  if (!d) return '';
  return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} · ${clockTime(value)}`;
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`;
  const mins = Math.floor(ms / 60000);
  const secs = Math.round((ms % 60000) / 1000);
  return `${mins}m ${secs}s`;
}

export function hour12(hour: number): string {
  const h = ((Math.round(hour) % 24) + 24) % 24;
  const suffix = h < 12 ? 'am' : 'pm';
  const display = h % 12 === 0 ? 12 : h % 12;
  return `${display}${suffix}`;
}

/** Turns a `#RRGGBB` brand colour into an rgba() string at the given alpha. */
export function tint(hex: string | null | undefined, alpha: number): string {
  const rgb = hexToRgb(hex);
  if (!rgb) return `rgb(var(--text-3) / ${alpha})`;
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
}

export function solid(hex: string | null | undefined): string {
  const rgb = hexToRgb(hex);
  if (!rgb) return 'rgb(var(--text-2))';
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

function hexToRgb(hex: string | null | undefined): [number, number, number] | null {
  if (!hex) return null;
  let value = hex.trim().replace('#', '');
  if (value.length === 3) {
    value = value
      .split('')
      .map((c) => c + c)
      .join('');
  }
  if (!/^[0-9a-fA-F]{6}$/.test(value)) return null;
  const int = Number.parseInt(value, 16);
  return [(int >> 16) & 255, (int >> 8) & 255, int & 255];
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function prettyJson(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function asStringList(value: string | string[] | undefined | null): string[] {
  if (!value) return [];
  return Array.isArray(value) ? value.filter((v) => typeof v === 'string') : [value];
}
