"""The OAuth 2.0 sign-in flow.

These tests guard the three things that would be quietly catastrophic if they
broke: that a `state` we did not issue is never accepted, that the refresh
token never leaves the vault, and that a credential which is *not* an OAuth
grant is refused rather than used.

They also pin four bugs that were real:

* `is_configured` read `os.environ`, but pydantic-settings loads `.env` into
  the settings object and never back into the process environment. A client id
  sitting in `.env` was invisible, so the flow reported itself unconfigured
  while looking perfectly set up on disk.
* `_store` defaulted a missing `expires_in` to one hour. Slack bot tokens do
  not expire and carry no refresh token, so the invented expiry turned a
  working token into "this connection expired" sixty minutes later.
* `github` advertised sign-in, but the GitHub adapter shells out to `gh` and
  never reads a stored credential, so the button would have banked a token
  nothing could use and reported success.
* Gmail's adapter accepts nothing but a grant, so the UI must be told not to
  offer a paste-a-token form beside the sign-in button.

Nothing here touches the network: `httpx.post` is replaced for the duration of
each test that needs it.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from duet.core import oauth
from duet.core.vault import get_vault


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


@pytest.fixture
def google(monkeypatch):
    """A configured Google client, set the way a real deployment sets it."""
    from duet.config import settings

    monkeypatch.setattr(settings, "google_client_id", "test-client-id", raising=False)
    monkeypatch.setattr(settings, "google_client_secret", "test-client-secret", raising=False)
    oauth._pending.clear()
    yield oauth.PROVIDERS["google"]
    oauth._pending.clear()


@pytest.fixture
def captured_post(monkeypatch):
    """Record every token-endpoint call and reply with a scripted payload."""
    calls: list[dict[str, Any]] = []
    reply: dict[str, Any] = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append({"url": url, "data": dict(data or {})})
        return FakeResponse(dict(reply))

    monkeypatch.setattr(oauth.httpx, "post", fake_post)

    def will_return(payload: dict[str, Any]) -> None:
        reply.clear()
        reply.update(payload)

    return SimpleNamespace(calls=calls, will_return=will_return)


def _handle_for(user_id: str, connector_key: str):
    """A handle for a credential already in the vault, without rewriting it.

    `vault.put` would return a handle but also overwrite what is stored, which
    is the thing under test here. The ref derivation mirrors `Vault.put`.
    """
    import hashlib

    from duet.connectors.base import CredentialHandle

    ref = hashlib.sha256(f"{user_id}:{connector_key}".encode()).hexdigest()[:32]
    return CredentialHandle(ref=ref, connector_key=connector_key)


# --------------------------------------------------------------------------- #
# what the UI is told
# --------------------------------------------------------------------------- #
class TestStatus:
    def test_configured_reads_the_env_file_not_the_process_environment(self, google, monkeypatch):
        """The bug: a client id in `.env` reached settings but never os.environ."""
        import os

        from duet.config import settings

        monkeypatch.delenv("DUET_GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("DUET_GOOGLE_CLIENT_SECRET", raising=False)
        assert "DUET_GOOGLE_CLIENT_ID" not in os.environ

        # Values live only on the settings object, exactly as .env loading does.
        assert settings.google_client_id == "test-client-id"
        assert oauth.is_configured(google) is True

    def test_an_exported_variable_still_works(self, monkeypatch):
        """A container sets real env vars; that path must not regress."""
        from duet.config import settings

        monkeypatch.setattr(settings, "slack_client_id", "", raising=False)
        monkeypatch.setattr(settings, "slack_client_secret", "", raising=False)
        monkeypatch.setenv("DUET_SLACK_CLIENT_ID", "from-the-environment")
        monkeypatch.setenv("DUET_SLACK_CLIENT_SECRET", "also-from-the-environment")

        assert oauth.is_configured(oauth.PROVIDERS["slack"]) is True

    def test_unconfigured_provider_is_reported_as_such(self, monkeypatch):
        from duet.config import settings

        monkeypatch.setattr(settings, "google_client_id", "", raising=False)
        monkeypatch.setattr(settings, "google_client_secret", "", raising=False)
        monkeypatch.delenv("DUET_GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("DUET_GOOGLE_CLIENT_SECRET", raising=False)

        status = oauth.status("gmail")
        assert status["oauth"] is True
        assert status["configured"] is False
        # Unconfigured is a solvable problem, so the payload has to carry the fix.
        assert status["setup_url"]
        assert status["client_id_env"] == "DUET_GOOGLE_CLIENT_ID"
        assert "127.0.0.1" in status["redirect_uri"]

    def test_gmail_is_sign_in_only(self):
        """Its adapter accepts no other credential, so no form may be offered."""
        assert oauth.status("gmail")["required"] is True

    def test_slack_accepts_either(self):
        """A pasted bot token works for Slack, so both paths are legitimate."""
        assert oauth.status("slack")["required"] is False

    def test_github_does_not_advertise_sign_in(self):
        """The adapter uses `gh auth`; a grant here would be banked and ignored."""
        assert oauth.status("github") == {"oauth": False}

    def test_apps_with_no_oauth_path_say_so_and_nothing_else(self):
        for key in ("notion", "linear", "stripe", "imap"):
            assert oauth.status(key) == {"oauth": False}, key

    def test_status_never_leaks_the_client_secret(self, google):
        assert "test-client-secret" not in repr(oauth.status("gmail"))


# --------------------------------------------------------------------------- #
# starting the flow
# --------------------------------------------------------------------------- #
class TestStart:
    def test_url_carries_pkce_state_and_offline_access(self, google):
        from urllib.parse import parse_qs, urlparse

        result = oauth.start("gmail", "user-1")
        query = parse_qs(urlparse(result["url"]).query)

        assert query["code_challenge_method"] == ["S256"]
        assert len(query["code_challenge"][0]) == 43  # base64url sha256, unpadded
        assert query["state"] == [result["state"]]
        # Without these two Google omits the refresh token and the connection
        # dies in an hour with no explanation.
        assert query["access_type"] == ["offline"]
        assert query["prompt"] == ["consent"]
        assert query["redirect_uri"] == [oauth.redirect_uri()]

    def test_the_verifier_never_appears_in_the_url(self, google):
        """PKCE is pointless if the verifier travels with the challenge."""
        result = oauth.start("gmail", "user-1")
        verifier = oauth._pending[result["state"]].verifier
        assert verifier not in result["url"]

    def test_each_start_is_a_fresh_nonce(self, google):
        a = oauth.start("gmail", "user-1")
        b = oauth.start("gmail", "user-1")
        assert a["state"] != b["state"]
        assert oauth._pending[a["state"]].verifier != oauth._pending[b["state"]].verifier

    def test_unconfigured_raises_with_the_steps_to_fix_it(self, monkeypatch):
        from duet.config import settings

        monkeypatch.setattr(settings, "google_client_id", "", raising=False)
        monkeypatch.setattr(settings, "google_client_secret", "", raising=False)
        monkeypatch.delenv("DUET_GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("DUET_GOOGLE_CLIENT_SECRET", raising=False)

        with pytest.raises(RuntimeError) as exc:
            oauth.start("gmail", "user-1")
        assert "DUET_GOOGLE_CLIENT_ID" in str(exc.value)

    def test_an_app_without_oauth_is_a_value_error_not_a_crash(self, google):
        with pytest.raises(ValueError):
            oauth.start("notion", "user-1")


# --------------------------------------------------------------------------- #
# the callback - the part an attacker aims at
# --------------------------------------------------------------------------- #
class TestFinish:
    def test_a_state_we_never_issued_is_refused(self, google):
        with pytest.raises(PermissionError):
            oauth.finish("state-i-invented", "some-code")

    def test_a_state_cannot_be_replayed(self, google, captured_post):
        captured_post.will_return(
            {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "scope": "a b"}
        )
        started = oauth.start("gmail", "user-1")
        oauth.finish(started["state"], "code-1")

        with pytest.raises(PermissionError):
            oauth.finish(started["state"], "code-1")

    def test_an_expired_state_is_refused(self, google, monkeypatch):
        started = oauth.start("gmail", "user-1")
        aged = oauth._pending[started["state"]]
        aged.created_at = time.time() - (oauth.STATE_TTL_S + 1)

        with pytest.raises(PermissionError):
            oauth.finish(started["state"], "code-1")

    def test_the_exchange_sends_the_verifier_and_the_secret(self, google, captured_post):
        captured_post.will_return(
            {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600}
        )
        started = oauth.start("gmail", "user-1")
        verifier = oauth._pending[started["state"]].verifier
        oauth.finish(started["state"], "code-1")

        sent = captured_post.calls[-1]["data"]
        assert sent["code_verifier"] == verifier
        assert sent["client_secret"] == "test-client-secret"
        assert sent["grant_type"] == "authorization_code"
        # Must be byte-identical to the authorize call or the provider refuses.
        assert sent["redirect_uri"] == oauth.redirect_uri()

    def test_result_reports_refreshability_without_exposing_tokens(self, google, captured_post):
        captured_post.will_return(
            {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "scope": "x y"}
        )
        started = oauth.start("gmail", "user-1")
        result = oauth.finish(started["state"], "code-1")

        assert result["has_refresh"] is True
        assert result["scopes"] == ["x", "y"]
        assert "rt-1" not in repr(result)
        assert "at-1" not in repr(result)

    def test_a_grant_with_no_refresh_token_is_flagged(self, google, captured_post):
        """The callback page warns about this; it must know it happened."""
        captured_post.will_return({"access_token": "at-1", "expires_in": 3600})
        started = oauth.start("gmail", "user-1")
        assert oauth.finish(started["state"], "code-1")["has_refresh"] is False


# --------------------------------------------------------------------------- #
# using the grant
# --------------------------------------------------------------------------- #
class TestAccessToken:
    def _store(self, record: dict[str, Any]):
        return get_vault().put("user-1", "gmail", record)

    def _grant(self, **overrides: Any) -> dict[str, Any]:
        record = {
            "type": "oauth2",
            "provider": "google",
            "user_id": "user-1",
            "connector_key": "gmail",
            "access_token": "at-live",
            "refresh_token": "rt-live",
            "expires_at": time.time() + 3600,
            "scope": "",
        }
        record.update(overrides)
        return record

    def test_a_valid_token_is_returned_untouched(self, google, captured_post):
        handle = self._store(self._grant())
        assert oauth.access_token(handle) == "at-live"
        assert captured_post.calls == []  # no needless refresh

    def test_a_token_with_no_expiry_is_not_treated_as_expired(self, google, captured_post):
        """Slack bot tokens: no expires_in, no refresh token, valid until revoked.

        The old code invented a one-hour expiry and then refused the token for
        having no way to renew it.
        """
        handle = self._store(
            self._grant(expires_at=None, refresh_token=None, provider="slack")
        )
        assert oauth.access_token(handle) == "at-live"
        assert captured_post.calls == []

    def test_store_records_no_expiry_when_the_provider_gives_none(self):
        """A Slack-shaped response: a token, no expires_in, no refresh token."""
        oauth._store("user-1", "slack", oauth.PROVIDERS["slack"], {"access_token": "xoxb-1"})

        record = get_vault().inject(_handle_for("user-1", "slack"), lambda c: dict(c))
        assert record["expires_at"] is None
        assert record["access_token"] == "xoxb-1"
        # And it is usable, which is the whole point of recording no expiry.
        assert oauth.access_token(_handle_for("user-1", "slack")) == "xoxb-1"

    def test_store_records_a_real_expiry_when_the_provider_gives_one(self):
        oauth._store(
            "user-1", "gmail", oauth.PROVIDERS["google"],
            {"access_token": "at-1", "expires_in": 3600},
        )
        record = get_vault().inject(_handle_for("user-1", "gmail"), lambda c: dict(c))
        assert record["expires_at"] == pytest.approx(time.time() + 3600, abs=5)

    def test_an_expiring_token_is_refreshed_and_written_back(self, google, captured_post):
        captured_post.will_return({"access_token": "at-fresh", "expires_in": 3600})
        handle = self._store(self._grant(expires_at=time.time() + 10))

        assert oauth.access_token(handle) == "at-fresh"
        assert captured_post.calls[-1]["data"]["grant_type"] == "refresh_token"
        # Written back, so the next call does not refresh again.
        assert oauth.access_token(handle) == "at-fresh"
        assert len(captured_post.calls) == 1

    def test_the_refresh_token_survives_a_refresh_that_omits_it(self, google, captured_post):
        """Providers usually omit it on refresh; dropping it breaks the next one."""
        captured_post.will_return({"access_token": "at-fresh", "expires_in": 3600})
        handle = self._store(self._grant(expires_at=time.time() + 10))
        oauth.access_token(handle)

        kept = get_vault().inject(handle, lambda c: c.get("refresh_token"))
        assert kept == "rt-live"

    def test_access_token_never_returns_the_refresh_token(self, google, captured_post):
        captured_post.will_return({"access_token": "at-fresh", "expires_in": 3600})
        handle = self._store(self._grant(expires_at=time.time() + 10))
        assert oauth.access_token(handle) == "at-fresh"
        assert "rt-live" not in oauth.access_token(handle)

    def test_an_expired_grant_with_no_refresh_token_asks_for_reconnection(self, google):
        handle = self._store(self._grant(expires_at=time.time() - 1, refresh_token=None))
        with pytest.raises(PermissionError) as exc:
            oauth.access_token(handle)
        assert "reconnect" in str(exc.value).lower()

    def test_a_refused_refresh_asks_for_reconnection(self, google, captured_post):
        captured_post.will_return({"error": "invalid_grant"})
        handle = self._store(self._grant(expires_at=time.time() - 1))
        with pytest.raises(PermissionError):
            oauth.access_token(handle)

    def test_a_credential_that_is_not_a_grant_is_refused(self, google):
        """The Gmail trap: an IMAP app password pasted where a grant belongs.

        Without this check the adapter would send `None` as a bearer token and
        the failure would surface as an opaque 401 from Google.
        """
        handle = self._store(
            {"type": "imap", "host": "imap.gmail.com", "app_password": "not-a-grant"}
        )
        with pytest.raises(ValueError) as exc:
            oauth.access_token(handle)
        assert "not an OAuth grant" in str(exc.value)


# --------------------------------------------------------------------------- #
# redirect URI
# --------------------------------------------------------------------------- #
class TestRedirectUri:
    def test_defaults_to_the_loopback_address_the_api_binds(self):
        uri = oauth.redirect_uri()
        assert uri.endswith("/api/oauth/callback")
        # Google treats localhost and 127.0.0.1 as different registrations, so
        # this must not drift to the hostname form.
        assert "127.0.0.1" in uri

    def test_can_be_overridden_for_a_tunnel(self, monkeypatch):
        from duet.config import settings

        monkeypatch.setattr(settings, "oauth_redirect_base", "https://duet.example", raising=False)
        assert oauth.redirect_uri() == "https://duet.example/api/oauth/callback"


# --------------------------------------------------------------------------- #
# Gmail error classification
# --------------------------------------------------------------------------- #
class TestGmailErrors:
    """A 403 is not automatically a bad credential.

    Hit for real: the grant was perfect and the Gmail API simply was not
    enabled on the Cloud project. Google says so in one clear sentence with a
    link that fixes it, and `raise_for_status` threw that away in favour of
    "Client error '403 Forbidden' for url ...". Worse, classifying it as an
    auth failure tells the user to reconnect an account that is fine.
    """

    def _resp(self, status: int, payload: Any):
        return FakeResponse(payload, status_code=status)

    def test_a_disabled_api_is_not_reported_as_a_bad_credential(self):
        from duet.connectors.live import ConnectorAuthError, ConnectorError
        from duet.connectors.live.gmail import _check

        payload = {
            "error": {
                "code": 403,
                "message": "Gmail API has not been used in project 123 before or it is disabled.",
                "errors": [{"reason": "accessNotConfigured"}],
                "details": [{"reason": "SERVICE_DISABLED"}],
            }
        }
        with pytest.raises(ConnectorError) as exc:
            _check(self._resp(403, payload))

        assert not isinstance(exc.value, ConnectorAuthError)
        # Google's own sentence survives, because it is the actionable part.
        assert "has not been used in project" in str(exc.value)

    def test_a_genuinely_rejected_token_asks_for_reconnection(self):
        from duet.connectors.live import ConnectorAuthError
        from duet.connectors.live.gmail import _check

        payload = {
            "error": {
                "code": 401,
                "message": "Invalid Credentials",
                "errors": [{"reason": "authError"}],
            }
        }
        with pytest.raises(ConnectorAuthError):
            _check(self._resp(401, payload))

    def test_throttling_is_not_a_credential_problem(self):
        from duet.connectors.live import ConnectorAuthError, ConnectorError
        from duet.connectors.live.gmail import _check

        payload = {
            "error": {"code": 403, "message": "Rate limit exceeded",
                      "errors": [{"reason": "rateLimitExceeded"}]}
        }
        with pytest.raises(ConnectorError) as exc:
            _check(self._resp(403, payload))
        assert not isinstance(exc.value, ConnectorAuthError)

    def test_a_success_raises_nothing(self):
        from duet.connectors.live.gmail import _check

        assert _check(self._resp(200, {"messages": []})) is None

    def test_a_non_json_body_still_classifies(self):
        """A proxy or captive portal returns HTML; this must not crash."""
        from duet.connectors.live import ConnectorError
        from duet.connectors.live.gmail import _check

        class HtmlResponse:
            status_code = 500

            def json(self):
                raise ValueError("not json")

        with pytest.raises(ConnectorError) as exc:
            _check(HtmlResponse())
        assert "500" in str(exc.value)
