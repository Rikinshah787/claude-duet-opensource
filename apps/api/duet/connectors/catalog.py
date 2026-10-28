"""The connector catalog.

One entry per app the assistant can link. The catalog is static metadata only -
it declares what an app *is*, what it can be read for, and which of its write
actions need a human. Runtime adapters are resolved in `registry.py`; an app
without one resolves to `UnavailableConnector` and returns nothing rather than
inventing data. `has_adapter` says whether a real integration exists today.

Risk tiers on write actions are the contract the approval gate enforces. They are
declared here, next to the action, so adding a connector forces the author to say
what the dangerous verbs are.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import RiskTier


@dataclass(frozen=True)
class WriteAction:
    """A mutating verb an adapter exposes, and what it costs to get it wrong."""

    key: str
    label: str
    risk: RiskTier
    reversible: bool = True
    note: str = ""


@dataclass(frozen=True)
class ConnectorSpec:
    key: str
    name: str
    category: str
    color: str          # brand hex, used by the UI tile
    glyph: str          # short fallback mark when no logo asset is present
    summary: str
    auth: str = "oauth2"            # oauth2 | api_key | basic | app_password
    read_scopes: tuple[str, ...] = ()
    write_scopes: tuple[str, ...] = ()
    signal_kinds: tuple[str, ...] = ()
    writes: tuple[WriteAction, ...] = ()
    monitored: bool = True          # included in the proactive sweep
    has_adapter: bool = False       # a REAL integration exists (see registry.REAL)
    docs: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


# Shorthand so the table below stays readable.
_SAFE = RiskTier.SAFE
_REVIEW = RiskTier.REVIEW
_SENSITIVE = RiskTier.SENSITIVE
_FORBIDDEN = RiskTier.FORBIDDEN


CATALOG: tuple[ConnectorSpec, ...] = (
    # ------------------------------------------------------------------ #
    # Communication
    # ------------------------------------------------------------------ #
    ConnectorSpec(
        key="gmail",
        name="Gmail",
        category="communication",
        color="#EA4335",
        glyph="M",
        summary="Read threads, spot what you missed, draft replies for approval.",
        read_scopes=("gmail.readonly", "gmail.metadata"),
        write_scopes=("gmail.compose", "gmail.send", "gmail.modify"),
        signal_kinds=("email", "thread_waiting", "vip_email"),
        writes=(
            WriteAction("create_draft", "Save a draft", _SAFE),
            WriteAction("send_email", "Send an email", _REVIEW, reversible=False),
            WriteAction("archive", "Archive a thread", _REVIEW),
            WriteAction("delete_thread", "Delete a thread", _SENSITIVE, reversible=False),
        ),
        has_adapter=True,
        tags=("email", "inbox"),
    ),
    ConnectorSpec(
        key="outlook",
        name="Outlook",
        category="communication",
        color="#0078D4",
        glyph="O",
        summary="Microsoft 365 mail: triage, drafts, and follow-up tracking.",
        read_scopes=("Mail.Read",),
        write_scopes=("Mail.Send", "Mail.ReadWrite"),
        signal_kinds=("email", "thread_waiting"),
        writes=(
            WriteAction("create_draft", "Save a draft", _SAFE),
            WriteAction("send_email", "Send an email", _REVIEW, reversible=False),
        ),
        tags=("email", "microsoft"),
    ),
    ConnectorSpec(
        key="imap",
        name="Email (IMAP)",
        category="communication",
        color="#5B6B7C",
        glyph="@",
        summary=(
            "Any mailbox over IMAP - Gmail with an app password, Fastmail, "
            "self-hosted. The no-OAuth path to real email."
        ),
        auth="app_password",
        read_scopes=("mail.read",),
        write_scopes=(),
        signal_kinds=("email", "thread_waiting"),
        # Read-only by design. Sending over SMTP is a separate credential and a
        # separate risk conversation; this connector watches, it does not write.
        writes=(),
        has_adapter=True,
        tags=("email", "no-oauth"),
    ),
    ConnectorSpec(
        key="slack",
        name="Slack",
        category="communication",
        color="#4A154B",
        glyph="#",
        summary="Catch direct mentions and unanswered threads across workspaces.",
        read_scopes=("channels:history", "groups:history", "im:history", "users:read"),
        write_scopes=("chat:write", "files:write"),
        signal_kinds=("mention", "dm", "thread_waiting", "escalation"),
        writes=(
            WriteAction("post_message", "Post a message", _REVIEW, reversible=False),
            WriteAction("set_status", "Update your status", _SAFE),
        ),
        has_adapter=True,
        tags=("chat",),
    ),
    ConnectorSpec(
        key="teams",
        name="Microsoft Teams",
        category="communication",
        color="#6264A7",
        glyph="T",
        summary="Channel mentions, chats, and meeting chatter in Microsoft 365.",
        read_scopes=("Chat.Read", "ChannelMessage.Read.All"),
        write_scopes=("ChatMessage.Send",),
        signal_kinds=("mention", "dm", "meeting"),
        writes=(WriteAction("post_message", "Post a message", _REVIEW, reversible=False),),
        tags=("chat", "microsoft"),
    ),
    ConnectorSpec(
        key="discord",
        name="Discord",
        category="communication",
        color="#5865F2",
        glyph="D",
        summary="Community and team server mentions.",
        read_scopes=("messages.read",),
        write_scopes=("messages.write",),
        signal_kinds=("mention", "dm"),
        writes=(WriteAction("post_message", "Post a message", _REVIEW, reversible=False),),
        tags=("chat",),
    ),
    ConnectorSpec(
        key="intercom",
        name="Intercom",
        category="support",
        color="#1F8DED",
        glyph="I",
        summary="Customer conversations, with escalation detection.",
        read_scopes=("read_conversations",),
        write_scopes=("write_conversations",),
        signal_kinds=("escalation", "customer_message"),
        writes=(WriteAction("reply", "Reply to a customer", _REVIEW, reversible=False),),
        tags=("support", "customers"),
    ),
    ConnectorSpec(
        key="zendesk",
        name="Zendesk",
        category="support",
        color="#03363D",
        glyph="Z",
        summary="Support tickets, SLA breaches, and angry-customer detection.",
        read_scopes=("tickets:read",),
        write_scopes=("tickets:write",),
        signal_kinds=("ticket", "escalation", "sla_breach"),
        writes=(
            WriteAction("comment", "Add a ticket comment", _REVIEW, reversible=False),
            WriteAction("update_ticket", "Change ticket status", _REVIEW),
        ),
        tags=("support",),
    ),
    # ------------------------------------------------------------------ #
    # Calendar and productivity
    # ------------------------------------------------------------------ #
    ConnectorSpec(
        key="google_calendar",
        name="Google Calendar",
        category="calendar",
        color="#4285F4",
        glyph="31",
        summary="Conflicts, travel gaps, and prep for what's coming.",
        read_scopes=("calendar.readonly",),
        write_scopes=("calendar.events",),
        signal_kinds=("meeting", "conflict", "prep_needed"),
        writes=(
            WriteAction("create_event", "Create an event", _REVIEW),
            WriteAction("move_event", "Reschedule an event", _REVIEW),
            WriteAction("cancel_event", "Cancel an event", _SENSITIVE, reversible=False),
        ),
        tags=("calendar", "google"),
    ),
    ConnectorSpec(
        key="outlook_calendar",
        name="Outlook Calendar",
        category="calendar",
        color="#0F6CBD",
        glyph="31",
        summary="Microsoft 365 scheduling and conflict detection.",
        read_scopes=("Calendars.Read",),
        write_scopes=("Calendars.ReadWrite",),
        signal_kinds=("meeting", "conflict"),
        writes=(
            WriteAction("create_event", "Create an event", _REVIEW),
            WriteAction("cancel_event", "Cancel an event", _SENSITIVE, reversible=False),
        ),
        tags=("calendar", "microsoft"),
    ),
    ConnectorSpec(
        key="calendly",
        name="Calendly",
        category="calendar",
        color="#006BFF",
        glyph="C",
        summary="Incoming bookings and no-shows.",
        read_scopes=("scheduled_events:read",),
        write_scopes=("scheduled_events:write",),
        signal_kinds=("meeting", "booking"),
        writes=(WriteAction("cancel_booking", "Cancel a booking", _REVIEW, reversible=False),),
        tags=("calendar", "scheduling"),
    ),
    ConnectorSpec(
        key="notion",
        name="Notion",
        category="productivity",
        color="#000000",
        glyph="N",
        summary="Docs, wikis, and databases as working memory.",
        read_scopes=("read_content",),
        write_scopes=("update_content", "insert_content"),
        signal_kinds=("doc_update", "mention"),
        writes=(
            WriteAction("create_page", "Create a page", _SAFE),
            WriteAction("update_page", "Edit a page", _REVIEW),
            WriteAction("delete_page", "Delete a page", _SENSITIVE),
        ),
        has_adapter=True,
        tags=("docs", "wiki"),
    ),
    ConnectorSpec(
        key="confluence",
        name="Confluence",
        category="productivity",
        color="#172B4D",
        glyph="C",
        summary="Atlassian team wiki and documentation.",
        read_scopes=("read:content",),
        write_scopes=("write:content",),
        signal_kinds=("doc_update", "mention"),
        writes=(WriteAction("create_page", "Create a page", _SAFE),),
        tags=("docs", "atlassian"),
    ),
    ConnectorSpec(
        key="google_sheets",
        name="Google Sheets",
        category="productivity",
        color="#0F9D58",
        glyph="S",
        summary="Spreadsheet analysis and forecast writeback.",
        read_scopes=("spreadsheets.readonly",),
        write_scopes=("spreadsheets",),
        signal_kinds=("doc_update",),
        writes=(WriteAction("write_range", "Write cells", _REVIEW),),
        tags=("spreadsheet", "google"),
    ),
    ConnectorSpec(
        key="microsoft_365",
        name="Microsoft 365",
        category="productivity",
        color="#D83B01",
        glyph="365",
        summary="Word, Excel, and the Graph surface behind them.",
        read_scopes=("Files.Read.All",),
        write_scopes=("Files.ReadWrite.All",),
        signal_kinds=("doc_update",),
        writes=(WriteAction("update_file", "Edit a file", _REVIEW),),
        tags=("microsoft", "office"),
    ),
    ConnectorSpec(
        key="todoist",
        name="Todoist",
        category="productivity",
        color="#E44332",
        glyph="T",
        summary="Personal task list, kept in sync with what the assistant commits to.",
        read_scopes=("data:read",),
        write_scopes=("data:read_write",),
        signal_kinds=("task_due",),
        writes=(WriteAction("create_task", "Create a task", _SAFE),),
        tags=("tasks",),
    ),
    ConnectorSpec(
        key="miro",
        name="Miro",
        category="design",
        color="#FFD02F",
        glyph="M",
        summary="Whiteboards for planning sessions.",
        read_scopes=("boards:read",),
        write_scopes=("boards:write",),
        signal_kinds=("mention",),
        writes=(WriteAction("create_board", "Create a board", _SAFE),),
        monitored=False,
        tags=("design", "whiteboard"),
    ),
    ConnectorSpec(
        key="figma",
        name="Figma",
        category="design",
        color="#F24E1E",
        glyph="F",
        summary="Design files and comment threads awaiting a reply.",
        read_scopes=("files:read",),
        write_scopes=("file_comments:write",),
        signal_kinds=("mention", "doc_update"),
        writes=(WriteAction("comment", "Comment on a file", _REVIEW, reversible=False),),
        tags=("design",),
    ),
    ConnectorSpec(
        key="canva",
        name="Canva",
        category="design",
        color="#00C4CC",
        glyph="C",
        summary="Turn a launch into a social campaign.",
        read_scopes=("design:meta:read",),
        write_scopes=("design:content:write",),
        signal_kinds=(),
        writes=(WriteAction("create_design", "Create a design", _SAFE),),
        monitored=False,
        tags=("design", "marketing"),
    ),
    ConnectorSpec(
        key="adobe",
        name="Adobe Creative Cloud",
        category="design",
        color="#FF0000",
        glyph="A",
        summary="Creative assets, PDFs, and Express documents.",
        read_scopes=("asset:read",),
        write_scopes=("asset:write",),
        signal_kinds=(),
        writes=(WriteAction("export_asset", "Export an asset", _SAFE),),
        monitored=False,
        tags=("design", "pdf"),
    ),
    # ------------------------------------------------------------------ #
    # CRM and sales
    # ------------------------------------------------------------------ #
    ConnectorSpec(
        key="hubspot",
        name="HubSpot",
        category="crm",
        color="#FF7A59",
        glyph="H",
        summary="Deal stages, stale pipeline, and contact history for follow-ups.",
        read_scopes=("crm.objects.contacts.read", "crm.objects.deals.read"),
        write_scopes=("crm.objects.contacts.write", "crm.objects.deals.write"),
        signal_kinds=("deal_stale", "deal_risk", "contact_activity"),
        writes=(
            WriteAction("log_activity", "Log an activity", _SAFE),
            WriteAction("update_deal", "Update a deal", _REVIEW),
            WriteAction("delete_record", "Delete a record", _SENSITIVE, reversible=False),
        ),
        tags=("crm", "sales"),
    ),
    ConnectorSpec(
        key="salesforce",
        name="Salesforce",
        category="crm",
        color="#00A1E0",
        glyph="SF",
        summary="Enterprise pipeline, accounts, and opportunity risk.",
        read_scopes=("api", "refresh_token"),
        write_scopes=("api",),
        signal_kinds=("deal_stale", "deal_risk"),
        writes=(
            WriteAction("update_opportunity", "Update an opportunity", _REVIEW),
            WriteAction("delete_record", "Delete a record", _SENSITIVE, reversible=False),
        ),
        tags=("crm", "sales"),
    ),
    ConnectorSpec(
        key="attio",
        name="Attio",
        category="crm",
        color="#1A1A1A",
        glyph="A",
        summary="Relationship-first CRM records.",
        read_scopes=("record:read",),
        write_scopes=("record:write",),
        signal_kinds=("contact_activity",),
        writes=(WriteAction("update_record", "Update a record", _REVIEW),),
        tags=("crm",),
    ),
    ConnectorSpec(
        key="linkedin",
        name="LinkedIn",
        category="marketing",
        color="#0A66C2",
        glyph="in",
        summary="Post job openings and company updates once you approve them.",
        read_scopes=("r_organization_social",),
        write_scopes=("w_member_social", "w_organization_social"),
        signal_kinds=("mention",),
        writes=(WriteAction("publish_post", "Publish a post", _REVIEW, reversible=False),),
        tags=("social", "hiring"),
    ),
    ConnectorSpec(
        key="mailchimp",
        name="Mailchimp",
        category="marketing",
        color="#FFE01B",
        glyph="MC",
        summary="Campaign performance and list health.",
        read_scopes=("campaigns:read",),
        write_scopes=("campaigns:write",),
        signal_kinds=("campaign_result",),
        writes=(
            WriteAction("create_campaign", "Draft a campaign", _SAFE),
            WriteAction("send_campaign", "Send to a list", _SENSITIVE, reversible=False,
                        note="Reaches every subscriber; cannot be recalled."),
        ),
        tags=("marketing", "email"),
    ),
    ConnectorSpec(
        key="tiktok",
        name="TikTok",
        category="marketing",
        color="#000000",
        glyph="TT",
        summary="Content performance for brand accounts.",
        read_scopes=("video.list",),
        write_scopes=("video.publish",),
        signal_kinds=("campaign_result",),
        writes=(WriteAction("publish_video", "Publish a video", _REVIEW, reversible=False),),
        monitored=False,
        tags=("social",),
    ),
    # ------------------------------------------------------------------ #
    # Project management
    # ------------------------------------------------------------------ #
    ConnectorSpec(
        key="jira",
        name="Jira",
        category="project",
        color="#0052CC",
        glyph="J",
        summary="Sprint risk, blocked issues, and overdue work.",
        read_scopes=("read:jira-work",),
        write_scopes=("write:jira-work",),
        signal_kinds=("ticket", "blocker", "deadline"),
        writes=(
            WriteAction("create_issue", "Create an issue", _SAFE),
            WriteAction("transition_issue", "Move an issue", _REVIEW),
            WriteAction("delete_issue", "Delete an issue", _SENSITIVE, reversible=False),
        ),
        tags=("project", "atlassian"),
    ),
    ConnectorSpec(
        key="linear",
        name="Linear",
        category="project",
        color="#5E6AD2",
        glyph="L",
        summary="Issue cycles, blockers, and what slipped.",
        read_scopes=("read",),
        write_scopes=("write",),
        signal_kinds=("ticket", "blocker", "deadline"),
        writes=(
            WriteAction("create_issue", "Create an issue", _SAFE),
            WriteAction("update_issue", "Update an issue", _REVIEW),
        ),
        has_adapter=True,
        tags=("project",),
    ),
    ConnectorSpec(
        key="asana",
        name="Asana",
        category="project",
        color="#F06A6A",
        glyph="A",
        summary="Project tasks, due dates, and workload.",
        read_scopes=("tasks:read",),
        write_scopes=("tasks:write",),
        signal_kinds=("task_due", "deadline"),
        writes=(WriteAction("create_task", "Create a task", _SAFE),),
        tags=("project",),
    ),
    ConnectorSpec(
        key="clickup",
        name="ClickUp",
        category="project",
        color="#7B68EE",
        glyph="CU",
        summary="Tasks, docs, and goals in one workspace.",
        read_scopes=("task:read",),
        write_scopes=("task:write",),
        signal_kinds=("task_due", "deadline"),
        writes=(WriteAction("create_task", "Create a task", _SAFE),),
        tags=("project",),
    ),
    ConnectorSpec(
        key="monday",
        name="monday.com",
        category="project",
        color="#FF3D57",
        glyph="M",
        summary="Boards, timelines, and team workload.",
        read_scopes=("boards:read",),
        write_scopes=("boards:write",),
        signal_kinds=("task_due", "deadline"),
        writes=(WriteAction("create_item", "Create an item", _SAFE),),
        tags=("project",),
    ),
    ConnectorSpec(
        key="trello",
        name="Trello",
        category="project",
        color="#0079BF",
        glyph="T",
        summary="Card movement and stalled columns.",
        read_scopes=("read",),
        write_scopes=("write",),
        signal_kinds=("task_due",),
        writes=(WriteAction("create_card", "Create a card", _SAFE),),
        tags=("project", "atlassian"),
    ),
    ConnectorSpec(
        key="airtable",
        name="Airtable",
        category="productivity",
        color="#18BFFF",
        glyph="AT",
        summary="Structured operational data and lightweight backends.",
        read_scopes=("data.records:read",),
        write_scopes=("data.records:write",),
        signal_kinds=("doc_update",),
        writes=(WriteAction("create_record", "Create a record", _SAFE),),
        tags=("database",),
    ),
    ConnectorSpec(
        key="basecamp",
        name="Basecamp",
        category="project",
        color="#1D2D35",
        glyph="BC",
        summary="Project threads and to-dos.",
        read_scopes=("read",),
        write_scopes=("write",),
        signal_kinds=("mention", "task_due"),
        writes=(WriteAction("post_message", "Post a message", _REVIEW, reversible=False),),
        tags=("project",),
    ),
    # ------------------------------------------------------------------ #
    # Finance - the highest-consequence category
    # ------------------------------------------------------------------ #
    ConnectorSpec(
        key="stripe",
        name="Stripe",
        category="finance",
        color="#635BFF",
        glyph="S",
        summary="Failed payments, disputes, and revenue movement.",
        read_scopes=("rak_charge_read", "rak_invoice_read"),
        write_scopes=("rak_invoice_write", "rak_refund_write"),
        signal_kinds=("payment_failed", "dispute", "invoice_overdue", "revenue_risk"),
        writes=(
            WriteAction("draft_invoice", "Draft an invoice", _SAFE),
            WriteAction("send_invoice", "Send an invoice", _REVIEW, reversible=False),
            WriteAction("issue_refund", "Issue a refund", _SENSITIVE, reversible=False,
                        note="Moves money out. Always requires explicit approval."),
            WriteAction("transfer_balance", "Transfer between accounts", _FORBIDDEN,
                        reversible=False, note="Blocked by policy, even with approval."),
        ),
        has_adapter=True,
        tags=("payments", "revenue"),
    ),
    ConnectorSpec(
        key="quickbooks",
        name="QuickBooks",
        category="finance",
        color="#2CA01C",
        glyph="QB",
        summary="Overdue invoices, cash position, and bills coming due.",
        read_scopes=("com.intuit.quickbooks.accounting.read",),
        write_scopes=("com.intuit.quickbooks.accounting",),
        signal_kinds=("invoice_overdue", "bill_due", "cash_risk"),
        writes=(
            WriteAction("draft_invoice", "Draft an invoice", _SAFE),
            WriteAction("send_invoice", "Send an invoice", _REVIEW, reversible=False),
            WriteAction("pay_bill", "Pay a bill", _SENSITIVE, reversible=False),
        ),
        tags=("accounting",),
    ),
    ConnectorSpec(
        key="xero",
        name="Xero",
        category="finance",
        color="#13B5EA",
        glyph="X",
        summary="Accounting ledger, invoices, and reconciliation.",
        read_scopes=("accounting.transactions.read",),
        write_scopes=("accounting.transactions",),
        signal_kinds=("invoice_overdue", "bill_due"),
        writes=(
            WriteAction("draft_invoice", "Draft an invoice", _SAFE),
            WriteAction("send_invoice", "Send an invoice", _REVIEW, reversible=False),
        ),
        tags=("accounting",),
    ),
    ConnectorSpec(
        key="paypal",
        name="PayPal",
        category="finance",
        color="#003087",
        glyph="PP",
        summary="Payment activity and disputes.",
        read_scopes=("transactions.read",),
        write_scopes=("payments.write",),
        signal_kinds=("payment_failed", "dispute"),
        writes=(
            WriteAction("issue_refund", "Issue a refund", _SENSITIVE, reversible=False),
            WriteAction("send_payment", "Send a payment", _FORBIDDEN, reversible=False),
        ),
        tags=("payments",),
    ),
    ConnectorSpec(
        key="square",
        name="Square",
        category="finance",
        color="#3E4348",
        glyph="SQ",
        summary="In-person sales, daily takings, and refunds.",
        read_scopes=("PAYMENTS_READ", "ORDERS_READ"),
        write_scopes=("PAYMENTS_WRITE",),
        signal_kinds=("revenue_risk", "payment_failed"),
        writes=(WriteAction("issue_refund", "Issue a refund", _SENSITIVE, reversible=False),),
        tags=("payments", "pos"),
    ),
    ConnectorSpec(
        key="gusto",
        name="Gusto",
        category="hr",
        color="#F45D48",
        glyph="G",
        summary="Payroll runs, hours, and reimbursements - prepared, never submitted alone.",
        read_scopes=("payrolls:read", "employees:read"),
        write_scopes=("payrolls:write",),
        signal_kinds=("payroll_due", "deadline"),
        writes=(
            WriteAction("prepare_payroll", "Prepare a payroll run", _SAFE,
                        note="Fills hours and bonuses; does not submit."),
            WriteAction("submit_payroll", "Submit payroll", _SENSITIVE, reversible=False,
                        note="Moves money to employees."),
        ),
        tags=("payroll", "hr"),
    ),
    ConnectorSpec(
        key="shopify",
        name="Shopify",
        category="commerce",
        color="#96BF48",
        glyph="SH",
        summary="Orders, inventory, and storefront changes.",
        read_scopes=("read_orders", "read_products"),
        write_scopes=("write_products", "write_orders"),
        signal_kinds=("order", "inventory_low", "revenue_risk"),
        writes=(
            WriteAction("update_product", "Update a product", _REVIEW),
            WriteAction("publish_theme", "Publish a storefront change", _SENSITIVE,
                        note="Changes what customers see immediately."),
        ),
        tags=("ecommerce",),
    ),
    ConnectorSpec(
        key="docusign",
        name="Docusign",
        category="finance",
        color="#D4AF37",
        glyph="DS",
        summary="Agreements out for signature and ones waiting on you.",
        read_scopes=("signature.read",),
        write_scopes=("signature",),
        signal_kinds=("signature_pending", "deadline"),
        writes=(
            WriteAction("prepare_envelope", "Prepare an envelope", _SAFE),
            WriteAction("send_for_signature", "Send for signature", _REVIEW, reversible=False),
            WriteAction("sign_document", "Sign a document", _SENSITIVE, reversible=False,
                        note="Legally binding."),
        ),
        tags=("legal", "signature"),
    ),
    # ------------------------------------------------------------------ #
    # Storage
    # ------------------------------------------------------------------ #
    ConnectorSpec(
        key="google_drive",
        name="Google Drive",
        category="storage",
        color="#FBBC04",
        glyph="GD",
        summary="Files, shared docs, and what changed since yesterday.",
        read_scopes=("drive.readonly",),
        write_scopes=("drive.file",),
        signal_kinds=("doc_update", "share_request"),
        writes=(
            WriteAction("create_file", "Create a file", _SAFE),
            WriteAction("share_file", "Share a file", _SENSITIVE,
                        note="Changes who can see the contents."),
            WriteAction("delete_file", "Delete a file", _SENSITIVE),
        ),
        tags=("files", "google"),
    ),
    ConnectorSpec(
        key="onedrive",
        name="OneDrive",
        category="storage",
        color="#0364B8",
        glyph="OD",
        summary="Microsoft file storage and sharing.",
        read_scopes=("Files.Read.All",),
        write_scopes=("Files.ReadWrite.All",),
        signal_kinds=("doc_update", "share_request"),
        writes=(WriteAction("share_file", "Share a file", _SENSITIVE),),
        tags=("files", "microsoft"),
    ),
    ConnectorSpec(
        key="dropbox",
        name="Dropbox",
        category="storage",
        color="#0061FF",
        glyph="DB",
        summary="Shared folders and file activity.",
        read_scopes=("files.metadata.read",),
        write_scopes=("files.content.write", "sharing.write"),
        signal_kinds=("doc_update", "share_request"),
        writes=(WriteAction("share_file", "Share a file", _SENSITIVE),),
        tags=("files",),
    ),
    ConnectorSpec(
        key="box",
        name="Box",
        category="storage",
        color="#0061D5",
        glyph="B",
        summary="Enterprise content and governance.",
        read_scopes=("root_readonly",),
        write_scopes=("root_readwrite",),
        signal_kinds=("doc_update", "share_request"),
        writes=(WriteAction("share_file", "Share a file", _SENSITIVE),),
        tags=("files",),
    ),
    ConnectorSpec(
        key="sharepoint",
        name="SharePoint",
        category="storage",
        color="#038387",
        glyph="SP",
        summary="Team sites and document libraries.",
        read_scopes=("Sites.Read.All",),
        write_scopes=("Sites.ReadWrite.All",),
        signal_kinds=("doc_update",),
        writes=(WriteAction("update_file", "Edit a file", _REVIEW),),
        tags=("files", "microsoft"),
    ),
    # ------------------------------------------------------------------ #
    # Development
    # ------------------------------------------------------------------ #
    ConnectorSpec(
        key="github",
        name="GitHub",
        category="development",
        color="#181717",
        glyph="GH",
        summary="Review requests, failing CI, and stale pull requests.",
        read_scopes=("repo:read", "notifications"),
        write_scopes=("repo:write",),
        signal_kinds=("review_request", "ci_failure", "mention"),
        writes=(
            WriteAction("comment", "Comment on a PR or issue", _REVIEW, reversible=False),
            WriteAction("open_pr", "Open a pull request", _REVIEW),
            WriteAction("merge_pr", "Merge a pull request", _SENSITIVE),
        ),
        has_adapter=True,
        tags=("code",),
    ),
    ConnectorSpec(
        key="gitlab",
        name="GitLab",
        category="development",
        color="#FC6D26",
        glyph="GL",
        summary="Merge requests and pipeline health.",
        read_scopes=("read_api",),
        write_scopes=("api",),
        signal_kinds=("review_request", "ci_failure"),
        writes=(WriteAction("comment", "Comment on an MR", _REVIEW, reversible=False),),
        tags=("code",),
    ),
    ConnectorSpec(
        key="vercel",
        name="Vercel",
        category="development",
        color="#000000",
        glyph="V",
        summary="Deployment status and production errors.",
        read_scopes=("read",),
        write_scopes=("read-write",),
        signal_kinds=("ci_failure", "incident"),
        writes=(
            WriteAction("rollback", "Roll back a deployment", _SENSITIVE,
                        note="Changes what is live in production."),
        ),
        tags=("deploy", "infra"),
    ),
    ConnectorSpec(
        key="sentry",
        name="Sentry",
        category="development",
        color="#362D59",
        glyph="SN",
        summary="Error spikes and regressions worth waking up for.",
        read_scopes=("project:read", "event:read"),
        write_scopes=("event:write",),
        signal_kinds=("incident", "ci_failure"),
        writes=(WriteAction("resolve_issue", "Resolve an issue", _REVIEW),),
        tags=("errors", "infra"),
    ),
    # ------------------------------------------------------------------ #
    # Platform and automation
    # ------------------------------------------------------------------ #
    ConnectorSpec(
        key="zapier",
        name="Zapier",
        category="automation",
        color="#FF4F00",
        glyph="Z",
        summary="Reach anything else through an existing Zap.",
        auth="api_key",
        read_scopes=("zap:read",),
        write_scopes=("zap:run",),
        signal_kinds=("automation_failed",),
        writes=(
            WriteAction(
                "run_zap",
                "Run a Zap",
                _REVIEW,
                note="Effect depends on the Zap; treated as a send.",
            ),
        ),
        tags=("automation",),
    ),
    ConnectorSpec(
        key="n8n",
        name="n8n",
        category="automation",
        color="#EA4B71",
        glyph="n8",
        summary="Self-hosted workflow automation.",
        auth="api_key",
        read_scopes=("workflow:read",),
        write_scopes=("workflow:execute",),
        signal_kinds=("automation_failed",),
        writes=(WriteAction("run_workflow", "Run a workflow", _REVIEW),),
        tags=("automation", "self-hosted"),
    ),
    ConnectorSpec(
        key="1password",
        name="1Password",
        category="security",
        color="#1A8CFF",
        glyph="1P",
        summary="Sign in to sites with saved logins - the agent never sees the secret.",
        auth="app_password",
        read_scopes=("vault:item_usage",),
        write_scopes=(),
        signal_kinds=("credential_expiry",),
        writes=(
            WriteAction("use_login", "Use a saved login", _SENSITIVE, reversible=False,
                        note="Credential is injected in the sandbox, never shown to the model."),
        ),
        monitored=False,
        tags=("security", "credentials"),
    ),
    ConnectorSpec(
        key="okta",
        name="Okta",
        category="security",
        color="#007DC1",
        glyph="OK",
        summary="Identity events and access reviews.",
        read_scopes=("okta.logs.read",),
        write_scopes=("okta.users.manage",),
        signal_kinds=("security_alert", "credential_expiry"),
        writes=(
            WriteAction("change_permissions", "Change access", _SENSITIVE,
                        note="Alters who can reach what."),
        ),
        tags=("security", "identity"),
    ),
    ConnectorSpec(
        key="zoom",
        name="Zoom",
        category="calendar",
        color="#2D8CFF",
        glyph="ZM",
        summary="Meeting links, recordings, and transcripts for notes.",
        read_scopes=("meeting:read", "recording:read"),
        write_scopes=("meeting:write",),
        signal_kinds=("meeting", "recording_ready"),
        writes=(WriteAction("create_meeting", "Create a meeting", _REVIEW),),
        tags=("meetings",),
    ),
    ConnectorSpec(
        key="postgres",
        name="PostgreSQL",
        category="data",
        color="#4169E1",
        glyph="PG",
        summary="Query a business database directly for reports and forecasts.",
        auth="basic",
        read_scopes=("select",),
        write_scopes=("insert", "update"),
        signal_kinds=("metric_anomaly",),
        writes=(
            WriteAction("run_query", "Run a read query", _SAFE),
            WriteAction("run_mutation", "Modify production data", _SENSITIVE, reversible=False),
        ),
        monitored=False,
        tags=("database",),
    ),
    ConnectorSpec(
        key="custom_api",
        name="Custom API",
        category="data",
        color="#6B7280",
        glyph="{}",
        summary="Any HTTP endpoint, described by an OpenAPI spec.",
        auth="api_key",
        read_scopes=("GET",),
        write_scopes=("POST", "PUT", "DELETE"),
        signal_kinds=("metric_anomaly",),
        writes=(
            WriteAction("call_read", "Call a read endpoint", _SAFE),
            WriteAction("call_write", "Call a write endpoint", _REVIEW),
        ),
        monitored=False,
        tags=("api", "extensible"),
    ),
)


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
BY_KEY: dict[str, ConnectorSpec] = {c.key: c for c in CATALOG}

CATEGORIES: tuple[str, ...] = (
    "communication",
    "calendar",
    "productivity",
    "crm",
    "project",
    "finance",
    "commerce",
    "hr",
    "storage",
    "development",
    "design",
    "marketing",
    "support",
    "security",
    "automation",
    "data",
)


def get(key: str) -> ConnectorSpec | None:
    return BY_KEY.get(key)


def by_category(category: str) -> list[ConnectorSpec]:
    return [c for c in CATALOG if c.category == category]


def monitored_keys() -> list[str]:
    return [c.key for c in CATALOG if c.monitored]


def write_action(connector_key: str, action_key: str) -> WriteAction | None:
    spec = BY_KEY.get(connector_key)
    if not spec:
        return None
    for action in spec.writes:
        if action.key == action_key:
            return action
    return None


def risk_for(connector_key: str, action_key: str) -> RiskTier:
    """Unknown verbs on known connectors are treated as sensitive, not safe.

    Defaulting an unrecognised action to SAFE would mean a typo silently skips
    the approval gate.
    """
    action = write_action(connector_key, action_key)
    return action.risk if action else RiskTier.SENSITIVE
