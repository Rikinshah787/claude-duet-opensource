"""Credential isolation.

The rule this module exists to enforce: **the model never sees a credential.**

Agents hold a `CredentialHandle` - an opaque reference. The secret is resolved
only inside `inject()`, at the moment an approved call is made, and the resolved
value is never returned to the caller, logged, or placed anywhere an agent's
context can reach.

Local storage is Fernet-encrypted on disk. In a deployment this class is the
seam to swap for a real secret manager (1Password Connect, Vault, AWS Secrets
Manager) without touching any calling code.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken

from ..config import DATA_DIR, settings
from ..connectors.base import CredentialHandle

log = logging.getLogger("duet.vault")

VAULT_PATH = Path(os.environ.get("DUET_VAULT_PATH", DATA_DIR / "vault.enc"))


def _derive_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class Vault:
    def __init__(self, path: Path | None = None, secret: str | None = None):
        self.path = path or VAULT_PATH
        self._fernet = Fernet(_derive_key(secret or settings.secret_key))

    # ------------------------------------------------------------------ #
    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self._fernet.decrypt(self.path.read_bytes()).decode("utf-8"))
        except (InvalidToken, ValueError):
            log.error("vault could not be decrypted - wrong DUET_SECRET_KEY?")
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        blob = self._fernet.encrypt(json.dumps(data).encode("utf-8"))
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass  # best effort; Windows ACLs differ

    # ------------------------------------------------------------------ #
    def put(self, user_id: str, connector_key: str, credential: dict[str, Any]) -> CredentialHandle:
        """Store a credential and hand back only a reference to it."""
        ref = hashlib.sha256(f"{user_id}:{connector_key}".encode()).hexdigest()[:32]
        data = self._read()
        data[ref] = {
            "user_id": user_id,
            "connector_key": connector_key,
            "credential": credential,
        }
        self._write(data)
        log.info("stored credential ref=%s connector=%s", ref[:6], connector_key)
        return CredentialHandle(ref=ref, connector_key=connector_key)

    def exists(self, ref: str) -> bool:
        return ref in self._read()

    def forget(self, ref: str) -> bool:
        data = self._read()
        if ref not in data:
            return False
        del data[ref]
        self._write(data)
        return True

    def forget_user(self, user_id: str) -> int:
        """Disconnecting an app must actually destroy the credential."""
        data = self._read()
        doomed = [r for r, v in data.items() if v.get("user_id") == user_id]
        for r in doomed:
            del data[r]
        if doomed:
            self._write(data)
        return len(doomed)

    # ------------------------------------------------------------------ #
    def inject(self, handle: CredentialHandle, fn: Callable[[dict[str, Any]], Any]) -> Any:
        """Run `fn` with the resolved secret.

        The secret exists only for the duration of the call and is never
        returned. Callers get `fn`'s result, so there is no code path that hands
        a raw credential back up the stack.
        """
        record = self._read().get(handle.ref)
        if not record:
            raise KeyError(f"no credential for ref {handle.ref[:6]}...")
        try:
            return fn(record["credential"])
        finally:
            del record


_vault: Vault | None = None


def get_vault() -> Vault:
    global _vault
    if _vault is None:
        _vault = Vault()
    return _vault
