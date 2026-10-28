/**
 * Real brand marks for the connector catalogue.
 *
 * `simple-icons` exposes one named export per brand (`siSlack`, `siGithub`…).
 * We import only the 47 the catalogue actually needs: the package declares
 * `sideEffects: false`, so webpack tree-shakes the other ~3,170 icons out of
 * the client bundle instead of shipping the whole 5 MB set.
 *
 * Ten of the 57 connector keys have no simple-icons entry in 13.16.0 — the
 * whole Microsoft family (Outlook, Teams, Outlook Calendar, Microsoft 365,
 * OneDrive, SharePoint) plus monday.com, DocuSign, Attio and custom_api.
 * `getBrand` returns `null` for those and callers fall back to the letter
 * glyph the API already sends. The slug table below still records the slug we
 * *would* use, so a future simple-icons release lights them up for free.
 */

import type { SimpleIcon } from 'simple-icons';
import {
  si1password,
  siAdobe,
  siAirtable,
  siAsana,
  siBasecamp,
  siBox,
  siCalendly,
  siCanva,
  siClickup,
  siConfluence,
  siDiscord,
  siDropbox,
  siFigma,
  siGithub,
  siGitlab,
  siGmail,
  siGooglecalendar,
  siGoogledrive,
  siGooglesheets,
  siGusto,
  siHubspot,
  siIntercom,
  siJira,
  siLinear,
  siLinkedin,
  siMailchimp,
  siMiro,
  siN8n,
  siNotion,
  siOkta,
  siPaypal,
  siPostgresql,
  siQuickbooks,
  siSalesforce,
  siSentry,
  siShopify,
  siSlack,
  siSquare,
  siStripe,
  siTiktok,
  siTodoist,
  siTrello,
  siVercel,
  siXero,
  siZapier,
  siZendesk,
  siZoom,
} from 'simple-icons/icons';

/** Connector key (from `GET /api/connectors`) to simple-icons slug. */
export const BRAND_SLUGS: Record<string, string> = {
  // communication
  gmail: 'gmail',
  outlook: 'microsoftoutlook', // absent in 13.16.0
  slack: 'slack',
  teams: 'microsoftteams', // absent in 13.16.0
  discord: 'discord',
  intercom: 'intercom',
  zendesk: 'zendesk',
  // calendar
  google_calendar: 'googlecalendar',
  outlook_calendar: 'microsoftoutlook', // absent in 13.16.0
  calendly: 'calendly',
  // productivity
  notion: 'notion',
  confluence: 'confluence',
  google_sheets: 'googlesheets',
  microsoft_365: 'microsoftoffice', // absent in 13.16.0
  todoist: 'todoist',
  // design
  miro: 'miro',
  figma: 'figma',
  canva: 'canva',
  adobe: 'adobe',
  // crm and marketing
  hubspot: 'hubspot',
  salesforce: 'salesforce',
  attio: 'attio', // not in simple-icons
  linkedin: 'linkedin',
  mailchimp: 'mailchimp',
  tiktok: 'tiktok',
  // project
  jira: 'jira',
  linear: 'linear',
  asana: 'asana',
  clickup: 'clickup',
  monday: 'mondaydotcom', // absent in 13.16.0
  trello: 'trello',
  airtable: 'airtable',
  basecamp: 'basecamp',
  // finance, commerce, hr
  stripe: 'stripe',
  quickbooks: 'quickbooks',
  xero: 'xero',
  paypal: 'paypal',
  square: 'square',
  gusto: 'gusto',
  shopify: 'shopify',
  docusign: 'docusign', // absent in 13.16.0
  // storage
  google_drive: 'googledrive',
  onedrive: 'microsoftonedrive', // absent in 13.16.0
  dropbox: 'dropbox',
  box: 'box',
  sharepoint: 'microsoftsharepoint', // absent in 13.16.0
  // development
  github: 'github',
  gitlab: 'gitlab',
  vercel: 'vercel',
  sentry: 'sentry',
  // automation, security, data
  zapier: 'zapier',
  n8n: 'n8n',
  '1password': '1password',
  okta: 'okta',
  zoom: 'zoom',
  postgres: 'postgresql',
  custom_api: '', // deliberately none: it is not a brand
};

/**
 * Only the icons we import, keyed by slug. A slug that is missing — mistyped,
 * or belonging to a brand that left simple-icons — just misses the lookup.
 * Nothing throws.
 */
const ICONS: Record<string, SimpleIcon> = {
  '1password': si1password,
  adobe: siAdobe,
  airtable: siAirtable,
  asana: siAsana,
  basecamp: siBasecamp,
  box: siBox,
  calendly: siCalendly,
  canva: siCanva,
  clickup: siClickup,
  confluence: siConfluence,
  discord: siDiscord,
  dropbox: siDropbox,
  figma: siFigma,
  github: siGithub,
  gitlab: siGitlab,
  gmail: siGmail,
  googlecalendar: siGooglecalendar,
  googledrive: siGoogledrive,
  googlesheets: siGooglesheets,
  gusto: siGusto,
  hubspot: siHubspot,
  intercom: siIntercom,
  jira: siJira,
  linear: siLinear,
  linkedin: siLinkedin,
  mailchimp: siMailchimp,
  miro: siMiro,
  n8n: siN8n,
  notion: siNotion,
  okta: siOkta,
  paypal: siPaypal,
  postgresql: siPostgresql,
  quickbooks: siQuickbooks,
  salesforce: siSalesforce,
  sentry: siSentry,
  shopify: siShopify,
  slack: siSlack,
  square: siSquare,
  stripe: siStripe,
  tiktok: siTiktok,
  todoist: siTodoist,
  trello: siTrello,
  vercel: siVercel,
  xero: siXero,
  zapier: siZapier,
  zendesk: siZendesk,
  zoom: siZoom,
};

export type Brand = {
  /** 24x24 SVG path data. */
  path: string;
  /** `#RRGGBB`, as the brand publishes it. */
  hex: string;
  /** Brand name, for the `<title>` inside the mark. */
  title: string;
  slug: string;
};

/** The brand mark for a connector key, or `null` when there isn't one. */
export function getBrand(connectorKey: string): Brand | null {
  // `hasOwn` rather than a bare lookup, so a key like `constructor` or
  // `__proto__` misses instead of finding something off the prototype.
  if (!connectorKey || !Object.hasOwn(BRAND_SLUGS, connectorKey)) return null;
  const slug = BRAND_SLUGS[connectorKey];
  if (!slug || !Object.hasOwn(ICONS, slug)) return null;
  const icon = ICONS[slug];
  if (!icon) return null;
  return { path: icon.path, hex: `#${icon.hex}`, title: icon.title, slug };
}

/* ------------------------------------------------------------------------- *
 * Legibility
 *
 * Several marks are near-black (github #181717, notion/tiktok/vercel #000000,
 * square #3E4348) and a few are near-white-bright (intercom #6AFDEF,
 * mailchimp #FFE01B). Painted straight onto the theme's surface they vanish.
 * So we measure the mark against the tile it will sit on and, when it falls
 * under the WCAG 2.1 SC 1.4.11 "non-text contrast" floor of 3:1, walk its HSL
 * lightness toward the legible side until it clears. Hue and saturation are
 * untouched, so the mark still reads as the brand. A mark with almost no
 * chroma has no brand tone to lighten, so it drops to the theme's ink colour.
 *
 * Both themes are resolved here, at render time, and handed to CSS as custom
 * properties, so the component never has to know which theme is live.
 * ------------------------------------------------------------------------- */

type Rgb = readonly [number, number, number];

/** `--surface` in each theme: #1A1917 and #FFFFFF. */
const SURFACE_DARK: Rgb = [26, 25, 23];
const SURFACE_LIGHT: Rgb = [255, 255, 255];
/** `--text` in each theme, for marks with no usable chroma. */
const INK_DARK: Rgb = [245, 244, 241];
const INK_LIGHT: Rgb = [31, 30, 27];

/** The tile stays the brand colour at 12% over the surface, as before. */
const TINT_ALPHA = 0.12;
/** WCAG 2.1 SC 1.4.11 minimum for a graphical object. */
const MIN_CONTRAST = 3;
/** Below this saturation there is no brand hue left to push around. */
const MIN_CHROMA = 0.12;

function parseHex(hex: string | null | undefined): Rgb | null {
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

function channelLuminance(channel: number): number {
  const c = channel / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function luminance(rgb: Rgb): number {
  return (
    0.2126 * channelLuminance(rgb[0]) +
    0.7152 * channelLuminance(rgb[1]) +
    0.0722 * channelLuminance(rgb[2])
  );
}

function contrast(a: Rgb, b: Rgb): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

function over(fg: Rgb, bg: Rgb, alpha: number): Rgb {
  return [
    Math.round(fg[0] * alpha + bg[0] * (1 - alpha)),
    Math.round(fg[1] * alpha + bg[1] * (1 - alpha)),
    Math.round(fg[2] * alpha + bg[2] * (1 - alpha)),
  ];
}

function toHsl(rgb: Rgb): [number, number, number] {
  const r = rgb[0] / 255;
  const g = rgb[1] / 255;
  const b = rgb[2] / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  if (d === 0) return [0, 0, l];
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
  else if (max === g) h = ((b - r) / d + 2) / 6;
  else h = ((r - g) / d + 4) / 6;
  return [h, s, l];
}

function hueToChannel(p: number, q: number, t: number): number {
  let x = t;
  if (x < 0) x += 1;
  if (x > 1) x -= 1;
  if (x < 1 / 6) return p + (q - p) * 6 * x;
  if (x < 1 / 2) return q;
  if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
  return p;
}

function fromHsl(h: number, s: number, l: number): Rgb {
  if (s === 0) {
    const v = Math.round(l * 255);
    return [v, v, v];
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return [
    Math.round(hueToChannel(p, q, h + 1 / 3) * 255),
    Math.round(hueToChannel(p, q, h) * 255),
    Math.round(hueToChannel(p, q, h - 1 / 3) * 255),
  ];
}

/**
 * The brand colour, pushed just far enough along its own lightness ramp to
 * clear `MIN_CONTRAST` against `tile`. Falls back to `ink` when the hue has no
 * chroma to work with, or when even the end of the ramp is not enough.
 */
function legible(brand: Rgb, tile: Rgb, ink: Rgb): Rgb {
  if (contrast(brand, tile) >= MIN_CONTRAST) return brand;

  const [h, s, l] = toHsl(brand);
  if (s < MIN_CHROMA) return ink;

  // Push away from the tile: lighter on a dark tile, darker on a light one.
  const lighten = luminance(tile) < luminance([128, 128, 128]);
  const limit = lighten ? 0.97 : 0.06;
  const step = lighten ? 0.04 : -0.04;

  for (let next = l + step; lighten ? next <= limit : next >= limit; next += step) {
    const candidate = fromHsl(h, s, next);
    if (contrast(candidate, tile) >= MIN_CONTRAST) return candidate;
  }
  return ink;
}

function css(rgb: Rgb): string {
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

export type BrandPaint = {
  /** Mark colour, dark theme. */
  fgDark: string;
  /** Tile colour, dark theme. */
  bgDark: string;
  /** Mark colour, light theme. */
  fgLight: string;
  /** Tile colour, light theme. */
  bgLight: string;
};

/**
 * Resolve a brand hex into the four colours a tile needs. The tile keeps the
 * brand's own 12% tint in both themes; only the mark moves, and only when it
 * has to.
 */
export function brandPaint(hex: string | null | undefined): BrandPaint {
  const brand = parseHex(hex);
  if (!brand) {
    return {
      fgDark: css(INK_DARK),
      bgDark: css(over(INK_DARK, SURFACE_DARK, TINT_ALPHA)),
      fgLight: css(INK_LIGHT),
      bgLight: css(over(INK_LIGHT, SURFACE_LIGHT, TINT_ALPHA)),
    };
  }

  const tileDark = over(brand, SURFACE_DARK, TINT_ALPHA);
  const tileLight = over(brand, SURFACE_LIGHT, TINT_ALPHA);

  return {
    fgDark: css(legible(brand, tileDark, INK_DARK)),
    bgDark: css(tileDark),
    fgLight: css(legible(brand, tileLight, INK_LIGHT)),
    bgLight: css(tileLight),
  };
}
