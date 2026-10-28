/**
 * What to ask for when connecting an app.
 *
 * `POST /api/connectors/{key}/connect` takes an opaque `credential` object and
 * hands it straight to the vault, so the shape is ours to choose. We ask for
 * the thing the app actually issues — and we say where to find it, because a
 * form that just says "token" is a form nobody can fill in.
 *
 * Apps that can be connected by signing in never reach this file - see
 * `ConnectPanel`, which routes them to `OAuthConnect` instead. What is left
 * here is the apps where a pasted credential is genuinely the way in, so the
 * specs below say plainly that there is no sign-in button for them rather than
 * rendering one that would go nowhere.
 */

export type CredentialFieldKind = 'text' | 'secret' | 'number' | 'url';

export type CredentialField = {
  /** Key inside the submitted `credential` object. */
  name: string;
  label: string;
  kind: CredentialFieldKind;
  placeholder?: string;
  hint?: string;
  /** Optional fields may be left blank. Everything else is required. */
  optional?: boolean;
  /** Prefilled, and still editable. */
  value?: string;
};

export type CredentialSpec = {
  /** Recorded in the vault so a future adapter knows what it is holding. */
  type: string;
  /** One or two sentences: what to paste, and where it comes from. */
  lead: string;
  /** Shown as a plain warning — used to admit the missing OAuth flow. */
  caveat?: string;
  fields: CredentialField[];
};

const secret = (
  name: string,
  label: string,
  placeholder?: string,
  hint?: string,
): CredentialField => ({ name, label, kind: 'secret', placeholder, hint });

const text = (
  name: string,
  label: string,
  placeholder?: string,
  extra: Partial<CredentialField> = {},
): CredentialField => ({ name, label, kind: 'text', placeholder, ...extra });

const NO_OAUTH =
  'Signing in is wired up for Google and Slack only. For this app there is no sign-in ' +
  'redirect to run, so paste a token you have issued yourself and it goes straight to the vault.';

/** Host/port/username/app-password, for anything reached over IMAP. */
function imap(host: string, provider: string, where: string): CredentialSpec {
  return {
    type: 'imap',
    lead: `${provider} over IMAP, with an app password rather than your account password.`,
    caveat: `${NO_OAUTH} ${where}`,
    fields: [
      text('host', 'IMAP host', host, { value: host }),
      { name: 'port', label: 'Port', kind: 'number', placeholder: '993', value: '993' },
      text('username', 'Username', 'you@example.com'),
      secret('app_password', 'App password', '••••-••••-••••-••••'),
    ],
  };
}

/** A single pasted token, the shape most API-key connectors want. */
function token(label: string, placeholder: string, hint: string, type = 'api_token'): CredentialSpec {
  return {
    type,
    lead: hint,
    fields: [secret('token', label, placeholder)],
  };
}

/**
 * Per-connector specs. Only connectors where we can be concrete and correct
 * are listed; everything else falls through to the `auth`-based default.
 */
const BY_KEY: Record<string, CredentialSpec> = {
  // --- email over IMAP ---------------------------------------------------
  gmail: imap(
    'imap.gmail.com',
    'Gmail',
    'Generate one at myaccount.google.com → Security → App passwords (requires 2-Step Verification).',
  ),
  outlook: imap(
    'outlook.office365.com',
    'Outlook',
    'Generate one at account.microsoft.com → Security → App passwords.',
  ),

  // --- the ones the brief calls out -------------------------------------
  slack: token(
    'Bot token',
    'xoxb-…',
    'Slack app → OAuth & Permissions → Bot User OAuth Token. It starts with xoxb-.',
    'bot_token',
  ),
  github: token(
    'Personal access token',
    'github_pat_… or ghp_…',
    'GitHub → Settings → Developer settings → Personal access tokens. A fine-grained token needs read access to the repositories you want watched; a classic one needs repo and read:org.',
    'personal_access_token',
  ),
  notion: token(
    'Internal integration token',
    'ntn_…',
    'Create an internal integration at notion.so/my-integrations, then share the pages you want watched with it. New tokens start ntn_, older ones secret_.',
    'integration_token',
  ),
  linear: token(
    'Personal API key',
    'lin_api_…',
    'Linear → Settings → Security & access → Personal API keys. It starts lin_api_.',
    'api_key',
  ),
  stripe: token(
    'Secret key',
    'sk_live_… or rk_live_…',
    'Stripe → Developers → API keys. Prefer a restricted key with read access only; grant writes separately below.',
    'secret_key',
  ),

  // --- other connectors where the token is unambiguous ------------------
  gitlab: token(
    'Personal access token',
    'glpat-…',
    'GitLab → Preferences → Access tokens, with the read_api scope. It starts glpat-.',
    'personal_access_token',
  ),
  figma: token(
    'Personal access token',
    'figd_…',
    'Figma → Settings → Security → Personal access tokens.',
    'personal_access_token',
  ),
  airtable: token(
    'Personal access token',
    'pat…',
    'Airtable → Builder hub → Personal access tokens, scoped to the bases you want read.',
    'personal_access_token',
  ),
  shopify: token(
    'Admin API access token',
    'shpat_…',
    'Shopify admin → Settings → Apps and sales channels → Develop apps → your app → API credentials.',
    'admin_api_token',
  ),
  hubspot: token(
    'Private app access token',
    'pat-…',
    'HubSpot → Settings → Integrations → Private apps → your app → Auth.',
    'private_app_token',
  ),
  clickup: token(
    'API token',
    'pk_…',
    'ClickUp → Settings → Apps → API token.',
    'api_token',
  ),
  sentry: token(
    'Auth token',
    'sntrys_… or sntryu_…',
    'Sentry → Settings → Auth tokens, with project:read and event:read.',
    'auth_token',
  ),
  vercel: token(
    'Access token',
    '',
    'Vercel → Account Settings → Tokens. Scope it to the team whose projects you want watched.',
    'access_token',
  ),
  discord: token(
    'Bot token',
    '',
    'Discord Developer Portal → your application → Bot → Reset Token. The bot must be invited to the servers you want watched.',
    'bot_token',
  ),
  intercom: token(
    'Access token',
    '',
    'Intercom → Settings → Developers → your app → Authentication.',
    'access_token',
  ),
  calendly: token(
    'Personal access token',
    '',
    'Calendly → Integrations → API & webhooks → Personal access tokens.',
    'personal_access_token',
  ),
  todoist: token(
    'API token',
    '',
    'Todoist → Settings → Integrations → Developer → API token.',
    'api_token',
  ),
  asana: token(
    'Personal access token',
    '',
    'Asana → My settings → Apps → Manage developer apps → Personal access tokens.',
    'personal_access_token',
  ),
  n8n: token(
    'API key',
    '',
    'n8n → Settings → n8n API → Create an API key. Self-hosted instances need the public API enabled.',
    'api_key',
  ),
  zapier: token(
    'API key',
    '',
    'Zapier → Settings → Developer, or the key issued by the private app you want to drive.',
    'api_key',
  ),

  // --- multi-field specifics --------------------------------------------
  jira: {
    type: 'atlassian_api_token',
    lead: 'Atlassian signs API requests with your account email plus an API token, not a password.',
    fields: [
      text('site', 'Site URL', 'https://your-team.atlassian.net', { kind: 'url' }),
      text('email', 'Account email', 'you@example.com'),
      secret('token', 'API token', '', 'Create one at id.atlassian.com → Security → API tokens.'),
    ],
  },
  confluence: {
    type: 'atlassian_api_token',
    lead: 'Atlassian signs API requests with your account email plus an API token, not a password.',
    fields: [
      text('site', 'Site URL', 'https://your-team.atlassian.net/wiki', { kind: 'url' }),
      text('email', 'Account email', 'you@example.com'),
      secret('token', 'API token', '', 'Create one at id.atlassian.com → Security → API tokens.'),
    ],
  },
  zendesk: {
    type: 'zendesk_api_token',
    lead: 'Zendesk pairs an agent email with an API token, scoped to one subdomain.',
    fields: [
      text('subdomain', 'Subdomain', 'your-team', { hint: 'The part before .zendesk.com.' }),
      text('email', 'Agent email', 'you@example.com'),
      secret('token', 'API token', '', 'Zendesk Admin Center → Apps and integrations → Zendesk API.'),
    ],
  },
  trello: {
    type: 'trello_key_token',
    lead: 'Trello needs both halves: the app key and a token issued against it.',
    fields: [
      text('key', 'API key', '', { hint: 'From trello.com/power-ups/admin.' }),
      secret('token', 'Token', '', 'Generated from the same page, against that key.'),
    ],
  },
  mailchimp: {
    type: 'api_key',
    lead: 'Mailchimp keys carry their data centre as a suffix, and the API host has to match it.',
    fields: [
      secret('api_key', 'API key', '…-us21', 'Mailchimp → Account → Extras → API keys.'),
      text('server_prefix', 'Server prefix', 'us21', {
        hint: 'The bit after the dash in the key.',
      }),
    ],
  },
  postgres: {
    type: 'postgres',
    lead: 'A read-only role is enough, and safer than connecting as the owner.',
    fields: [
      text('host', 'Host', 'db.internal.example.com'),
      { name: 'port', label: 'Port', kind: 'number', placeholder: '5432', value: '5432' },
      text('database', 'Database', 'app_production'),
      text('username', 'Username', 'duet_readonly'),
      secret('password', 'Password', ''),
    ],
  },
  custom_api: {
    type: 'api_key',
    lead: 'Point at the base URL and give the key it expects; the header name is up to you.',
    fields: [
      text('base_url', 'Base URL', 'https://api.example.com/v1', { kind: 'url' }),
      secret('api_key', 'API key', ''),
      text('header', 'Header name', 'Authorization', {
        optional: true,
        hint: 'Defaults to Authorization: Bearer <key>.',
      }),
    ],
  },
};

/** Fallbacks keyed on the catalogue's `auth` value. */
function byAuth(auth: string, name: string): CredentialSpec {
  switch (auth) {
    case 'api_key':
      return {
        type: 'api_key',
        lead: `Paste the API key ${name} issued you. It goes to the vault, never to the model.`,
        fields: [secret('api_key', 'API key', '')],
      };
    case 'basic':
      return {
        type: 'basic',
        lead: `${name} authenticates with a host and a username and password pair.`,
        fields: [
          text('host', 'Host', 'host.example.com'),
          text('username', 'Username', ''),
          secret('password', 'Password', ''),
        ],
      };
    case 'app_password':
      return {
        type: 'app_password',
        lead: `${name} issues a per-application password, separate from your account password.`,
        fields: [
          text('username', 'Username', 'you@example.com'),
          secret('app_password', 'App password', ''),
          text('host', 'Host', '', { optional: true }),
          { name: 'port', label: 'Port', kind: 'number', placeholder: '', optional: true },
        ],
      };
    case 'oauth2':
    default:
      return {
        type: 'access_token',
        lead: `${name} normally authorises over OAuth.`,
        caveat: NO_OAUTH,
        fields: [
          secret(
            'access_token',
            'Access token',
            '',
            `Issue one from the ${name} developer settings with read access to what you want watched.`,
          ),
        ],
      };
  }
}

/** The form to render for a connector. Never empty, never a dead button. */
export function credentialSpec(connector: { key: string; name: string; auth: string }): CredentialSpec {
  return BY_KEY[connector.key] ?? byAuth(connector.auth, connector.name);
}
