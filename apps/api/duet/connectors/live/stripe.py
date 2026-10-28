"""Stripe.

The highest-consequence adapter in the set, so two things are explicit here:

**Minor units.** Stripe counts in cents. Every amount crossing out of this
module is divided by 100 before it reaches a hint, a summary, or the priority
engine - `financial_factor` buckets at $1k/$5k/$20k/$50k, so a missed division
turns a $184 failure into a "$18,400 at stake" interrupt. Amounts crossing *in*
(a refund) are the mirror of the same hazard, which is why `issue_refund`
accepts `amount_minor` verbatim and treats a plain `amount` as dollars.

**`transfer_balance` never calls Stripe.** The catalog marks it FORBIDDEN. This
adapter refuses it before any client is constructed, so the policy holds even if
an approval record somehow arrives marked approved.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ..base import ActionResult, BaseConnector, FetchContext, RawSignal
from . import (
    TIMEOUT,
    ConnectorError,
    now_iso,
    parse_epoch,
    raise_for_auth,
    resolve_secret,
)

API = "https://api.stripe.com/v1"

# Per-resource caps for one sweep. Stripe paginates with `starting_after`, but a
# proactive sweep wants "what is newly wrong", not the whole ledger.
CHARGE_LIMIT = 20
DISPUTE_LIMIT = 20
INVOICE_LIMIT = 30

# Stripe's unit of account is the smallest currency unit.
MINOR_UNITS = 100


def _major(minor: Any) -> float:
    """Cents -> dollars. The one conversion the rest of the system depends on."""
    try:
        return round(float(minor) / MINOR_UNITS, 2)
    except (TypeError, ValueError):
        return 0.0


class StripeConnector(BaseConnector):
    key = "stripe"

    # ------------------------------------------------------------------ #
    def _secret_key(self, ctx: FetchContext) -> str | None:
        return resolve_secret(ctx, "secret_key", "api_key", "token")

    def _headers(self, secret_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {secret_key}"}

    def _get(
        self, client: httpx.Client, secret_key: str, path: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        """One list call. `None` means throttled - the caller keeps what it has."""
        resp = client.get(f"{API}{path}", headers=self._headers(secret_key), params=params)
        if resp.status_code == 429:
            return None
        raise_for_auth(self.key, resp.status_code, resp.text)
        if resp.status_code >= 400:
            raise ConnectorError(f"Stripe {path} failed (HTTP {resp.status_code})")
        return resp.json()

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    def fetch(self, ctx: FetchContext) -> tuple[list[RawSignal], dict[str, Any]]:
        secret_key = self._secret_key(ctx)
        if not secret_key:
            return [], ctx.cursor

        cursor = dict(ctx.cursor)
        signals: list[RawSignal] = []

        with httpx.Client(timeout=TIMEOUT) as client:
            # 1. Failed charges. Watermarked on `created`, because a charge's
            #    failure is fixed at creation and never changes afterwards.
            charges = self._get(
                client,
                secret_key,
                "/charges",
                self._created_params(cursor, ctx, "charges_created_after", CHARGE_LIMIT),
            )
            if charges is None:
                return signals, cursor
            newest = cursor.get("charges_created_after")
            for charge in charges.get("data", []):
                created = charge.get("created")
                if created and (newest is None or int(created) > int(newest)):
                    newest = int(created)
                if charge.get("status") != "failed":
                    continue
                signals.append(self._charge_signal(charge))
            if newest:
                cursor["charges_created_after"] = newest

            # 2. Open disputes.
            disputes = self._get(
                client,
                secret_key,
                "/disputes",
                self._created_params(cursor, ctx, "disputes_created_after", DISPUTE_LIMIT),
            )
            if disputes is None:
                return signals, cursor
            newest = cursor.get("disputes_created_after")
            for dispute in disputes.get("data", []):
                created = dispute.get("created")
                if created and (newest is None or int(created) > int(newest)):
                    newest = int(created)
                signals.append(self._dispute_signal(dispute))
            if newest:
                cursor["disputes_created_after"] = newest

            # 3. Overdue invoices. Deliberately *not* watermarked on `created`:
            #    an invoice raised in March goes overdue in April, so a created
            #    filter would hide exactly the ones worth reporting. The open
            #    set is small and capped instead.
            invoices = self._get(
                client,
                secret_key,
                "/invoices",
                {"status": "open", "limit": INVOICE_LIMIT},
            )
            if invoices is None:
                return signals, cursor
            now = datetime.now(timezone.utc)
            for invoice in invoices.get("data", []):
                signal = self._invoice_signal(invoice, now)
                if signal:
                    signals.append(signal)

        cursor["last_fetch"] = now_iso()
        return signals, cursor

    def _created_params(
        self, cursor: dict[str, Any], ctx: FetchContext, key: str, limit: int
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        after = cursor.get(key)
        if after is None and ctx.since is not None:
            after = int(ctx.since.timestamp())
        if after is not None:
            # `gt`, not `gte` - otherwise every sweep re-reports the newest row.
            params["created[gt]"] = int(after)
        return params

    # ------------------------------------------------------------------ #
    def _charge_signal(self, charge: dict[str, Any]) -> RawSignal:
        amount = _major(charge.get("amount"))
        currency = str(charge.get("currency", "usd")).upper()
        who = _charge_actor(charge)
        reason = charge.get("failure_message") or charge.get("failure_code") or "declined"

        hints: dict[str, Any] = {"amount": amount}
        if charge.get("customer"):
            hints["customer"] = True

        return RawSignal(
            external_id=charge["id"],
            kind="payment_failed",
            title=f"Payment failed: {amount:,.2f} {currency} - {reason}",
            body=(
                f"{charge.get('description') or 'Charge'} for {amount:,.2f} {currency} "
                f"failed: {reason}."
            ),
            actor=who,
            url=f"https://dashboard.stripe.com/payments/{charge['id']}",
            occurred_at=parse_epoch(charge.get("created")),
            raw={
                "amount_minor": charge.get("amount"),
                "currency": charge.get("currency"),
                "failure_code": charge.get("failure_code"),
                "customer": charge.get("customer"),
            },
            hints=hints,
        )

    def _dispute_signal(self, dispute: dict[str, Any]) -> RawSignal:
        amount = _major(dispute.get("amount"))
        currency = str(dispute.get("currency", "usd")).upper()
        reason = dispute.get("reason") or "unspecified"

        # A dispute is a customer escalating past us to their bank, and the
        # evidence window is a hard deadline with money attached to missing it.
        hints: dict[str, Any] = {"amount": amount, "escalation": True}
        if dispute.get("customer") or dispute.get("charge"):
            hints["customer"] = True
        due_by = (dispute.get("evidence_details") or {}).get("due_by")
        deadline = parse_epoch(due_by)
        if deadline:
            hints["deadline"] = deadline.isoformat()

        return RawSignal(
            external_id=dispute["id"],
            kind="dispute",
            title=f"Dispute opened: {amount:,.2f} {currency} ({reason})",
            body=(
                f"Status {dispute.get('status', 'unknown')}. Reason: {reason}. "
                f"Evidence due {deadline.date().isoformat() if deadline else 'unknown'}."
            ),
            actor=str(dispute.get("charge") or dispute.get("customer") or ""),
            url=f"https://dashboard.stripe.com/disputes/{dispute['id']}",
            occurred_at=parse_epoch(dispute.get("created")),
            raw={
                "amount_minor": dispute.get("amount"),
                "status": dispute.get("status"),
                "reason": reason,
                "evidence_due_by": due_by,
            },
            hints=hints,
        )

    def _invoice_signal(self, invoice: dict[str, Any], now: datetime) -> RawSignal | None:
        due_at = parse_epoch(invoice.get("due_date"))
        if due_at is None or due_at >= now:
            return None  # open but not yet late; nothing to say about it

        days_overdue = int((now - due_at).total_seconds() // 86400)
        # `amount_remaining` is what is actually still owed after part payments;
        # `amount_due` is the original ask.
        outstanding = _major(invoice.get("amount_remaining", invoice.get("amount_due")))
        currency = str(invoice.get("currency", "usd")).upper()
        who = invoice.get("customer_email") or invoice.get("customer_name") or ""
        number = invoice.get("number") or invoice["id"]

        hints: dict[str, Any] = {
            "amount": outstanding,
            "days_overdue": days_overdue,
            "customer": True,
        }

        return RawSignal(
            external_id=invoice["id"],
            kind="invoice_overdue",
            title=(
                f"Invoice {number} is {days_overdue} days overdue "
                f"({outstanding:,.2f} {currency})"
            ),
            body=(
                f"{outstanding:,.2f} {currency} outstanding from "
                f"{who or 'the customer'}, due {due_at.date().isoformat()}."
            ),
            actor=who,
            url=invoice.get("hosted_invoice_url")
            or f"https://dashboard.stripe.com/invoices/{invoice['id']}",
            occurred_at=due_at,
            raw={
                "number": invoice.get("number"),
                "amount_remaining_minor": invoice.get("amount_remaining"),
                "currency": invoice.get("currency"),
                "customer": invoice.get("customer"),
                "due_date": invoice.get("due_date"),
            },
            hints=hints,
        )

    # ------------------------------------------------------------------ #
    # Write
    # ------------------------------------------------------------------ #
    def execute(self, action: str, payload: dict[str, Any], ctx: FetchContext) -> ActionResult:
        # Checked before the credential, before any client: a FORBIDDEN action
        # must not be able to reach Stripe by any path through this method.
        if action == "transfer_balance":
            return ActionResult(
                ok=False,
                summary="Transferring a Stripe balance is blocked by policy.",
                error="forbidden",
            )

        secret_key = self._secret_key(ctx)
        if not secret_key:
            return ActionResult(
                ok=False, summary="Stripe is not connected", error="no_credential"
            )

        if action == "draft_invoice":
            return self._draft_invoice(secret_key, payload)
        if action == "send_invoice":
            return self._send_invoice(secret_key, payload)
        if action == "issue_refund":
            return self._issue_refund(secret_key, payload)
        return super().execute(action, payload, ctx)

    def _draft_invoice(self, secret_key: str, payload: dict[str, Any]) -> ActionResult:
        customer = payload.get("customer") or payload.get("customer_id")
        if not customer:
            return ActionResult(
                ok=False,
                summary="Stripe needs a customer to draft an invoice for",
                error="missing_customer",
            )

        form: dict[str, Any] = {
            "customer": customer,
            "collection_method": payload.get("collection_method", "send_invoice"),
            # Explicitly off: auto_advance would let Stripe finalise and charge
            # this draft on its own, turning a SAFE action into a REVIEW one.
            "auto_advance": "false",
        }
        if form["collection_method"] == "send_invoice":
            form["days_until_due"] = int(payload.get("days_until_due", 30))
        if payload.get("description"):
            form["description"] = payload["description"]

        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(f"{API}/invoices", headers=self._headers(secret_key), data=form)
            result = self._guard(resp, "draft an invoice")
            if isinstance(result, ActionResult):
                return result
            invoice = result

        return ActionResult(
            ok=True,
            summary=f"Drafted Stripe invoice {invoice.get('number') or invoice.get('id')} "
            f"(not sent)",
            data={"id": invoice.get("id"), "status": invoice.get("status")},
            external_id=invoice.get("id"),
        )

    def _send_invoice(self, secret_key: str, payload: dict[str, Any]) -> ActionResult:
        invoice_id = payload.get("invoice_id") or payload.get("id")
        if not invoice_id:
            return ActionResult(
                ok=False,
                summary="Stripe needs an invoice id to send",
                error="missing_invoice_id",
            )

        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(
                f"{API}/invoices/{invoice_id}/send", headers=self._headers(secret_key)
            )
            result = self._guard(resp, "send that invoice")
            if isinstance(result, ActionResult):
                return result
            invoice = result

        total = _major(invoice.get("amount_due"))
        currency = str(invoice.get("currency", "usd")).upper()
        return ActionResult(
            ok=True,
            summary=(
                f"Sent Stripe invoice {invoice.get('number') or invoice_id} "
                f"for {total:,.2f} {currency}"
            ),
            data={"id": invoice.get("id"), "status": invoice.get("status")},
            external_id=invoice.get("id") or str(invoice_id),
        )

    def _issue_refund(self, secret_key: str, payload: dict[str, Any]) -> ActionResult:
        charge = payload.get("charge") or payload.get("charge_id")
        intent = payload.get("payment_intent") or payload.get("payment_intent_id")
        if not charge and not intent:
            return ActionResult(
                ok=False,
                summary="Stripe needs a charge or payment intent to refund",
                error="missing_charge",
            )

        form: dict[str, Any] = {}
        if charge:
            form["charge"] = charge
        else:
            form["payment_intent"] = intent
        if payload.get("reason"):
            form["reason"] = payload["reason"]

        # Two spellings on purpose. `amount_minor` is passed through untouched
        # for a caller that already speaks Stripe; a bare `amount` is read as
        # dollars, because that is what a human typing into an approval means.
        if payload.get("amount_minor") is not None:
            form["amount"] = int(payload["amount_minor"])
        elif payload.get("amount") is not None:
            form["amount"] = int(round(float(payload["amount"]) * MINOR_UNITS))

        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(f"{API}/refunds", headers=self._headers(secret_key), data=form)
            result = self._guard(resp, "issue that refund")
            if isinstance(result, ActionResult):
                return result
            refund = result

        amount = _major(refund.get("amount"))
        currency = str(refund.get("currency", "usd")).upper()
        return ActionResult(
            ok=True,
            summary=f"Refunded {amount:,.2f} {currency} on {charge or intent}",
            data={
                "id": refund.get("id"),
                "status": refund.get("status"),
                "amount_minor": refund.get("amount"),
            },
            external_id=refund.get("id"),
        )

    # ------------------------------------------------------------------ #
    def _guard(self, resp: httpx.Response, what: str) -> dict[str, Any] | ActionResult:
        raise_for_auth(self.key, resp.status_code, resp.text)
        if resp.status_code >= 400:
            message = ""
            try:
                message = (resp.json().get("error") or {}).get("message", "")
            except ValueError:
                message = resp.text[:200]
            return ActionResult(
                ok=False,
                summary=f"Stripe refused to {what}: {message or resp.status_code}",
                error=f"http_{resp.status_code}",
            )
        return resp.json()


def _charge_actor(charge: dict[str, Any]) -> str:
    """Best available identity for a failed charge, without extra API calls."""
    details = charge.get("billing_details") or {}
    return (
        charge.get("receipt_email")
        or details.get("email")
        or details.get("name")
        or str(charge.get("customer") or "")
    )
