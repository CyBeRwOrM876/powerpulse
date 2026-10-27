"""Signed, tamper-evident reports (Ed25519 over canonical JSON)."""
from __future__ import annotations
import hashlib
import json
import os
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from .config import DATA


def _round(o):
    """Round floats so the canonical form is stable across platforms."""
    if isinstance(o, float):
        r = round(o, 2)
        return int(r) if r == int(r) and abs(r) < 1e15 else r
    if isinstance(o, dict):
        return {str(k): _round(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_round(v) for v in o]
    return o


def canonical(payload: dict) -> str:
    """Sorted keys, no whitespace, UTF-8. Restricted to rounded numbers so it matches RFC 8785 output for our data."""
    return json.dumps(_round(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Signer:
    def __init__(self, key_path=None):
        self.path = key_path or DATA / "report_signing_key.pem"
        env = os.getenv("PP_SIGNING_KEY_PEM")
        if env:
            self.sk = serialization.load_pem_private_key(env.encode(), password=None)
        elif self.path.exists():
            self.sk = serialization.load_pem_private_key(self.path.read_bytes(), password=None)
        else:
            self.sk = Ed25519PrivateKey.generate()
            self.path.write_bytes(self.sk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                         serialization.NoEncryption()))
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        self.pk = self.sk.public_key()
        self.public_pem = self.pk.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        raw = self.pk.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.key_id = hashlib.sha256(raw).hexdigest()[:16]

    def sign(self, payload: dict) -> tuple[str, str, str]:
        c = canonical(payload)
        return c, self.sk.sign(c.encode()).hex(), self.key_id


def verify(public_pem: str, canonical_payload: str, sig_hex: str) -> bool:
    try:
        pk = serialization.load_pem_public_key(public_pem.encode())
        assert isinstance(pk, Ed25519PublicKey)
        pk.verify(bytes.fromhex(sig_hex), canonical_payload.encode())
        return True
    except (InvalidSignature, ValueError, AssertionError):
        return False


_signer: Signer | None = None


def signer() -> Signer:
    global _signer
    if _signer is None:
        _signer = Signer()
    return _signer
