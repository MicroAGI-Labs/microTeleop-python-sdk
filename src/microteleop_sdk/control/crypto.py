"""Ed25519 JWS with pinned keys, fixed algorithms and separate message purposes."""

import hashlib
import json
import os
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class AuthenticationError(ValueError):
    pass


def public_key_text(key: Ed25519PublicKey) -> str:
    return key.public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode()


def load_public_key(text: str) -> Ed25519PublicKey:
    try:
        key = serialization.load_ssh_public_key(text.encode())
        if not isinstance(key, Ed25519PublicKey):
            raise TypeError("Only Ed25519 robot/platform keys are supported")
        return key
    except (ValueError, TypeError) as exc:
        raise AuthenticationError("Invalid Ed25519 public key") from exc


def fingerprint(key: Ed25519PublicKey) -> str:
    return hashlib.sha256(key.public_bytes_raw()).hexdigest()


class SigningKey:
    def __init__(self, private_key: Ed25519PrivateKey):
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("Expected an Ed25519 private key")
        self._key = private_key
        self.key_id = fingerprint(private_key.public_key())

    @classmethod
    def load(cls, path: str):
        data = Path(path).read_bytes()
        loader = (
            serialization.load_ssh_private_key
            if data.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----")
            else serialization.load_pem_private_key
        )
        key = loader(data, password=None)
        return cls(key)

    @classmethod
    def generate(cls, path: str):
        """Provision a new key at an available path."""
        key = Ed25519PrivateKey.generate()
        data = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        return cls(key)

    @property
    def public_key(self):
        return public_key_text(self._key.public_key())

    def sign(self, payload: dict, purpose: str) -> str:
        return jwt.api_jws.PyJWS().encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
            self._key,
            algorithm="EdDSA",
            headers={"typ": purpose, "kid": self.key_id},
        )


class TrustStore:
    def __init__(self, public_keys: dict[str, str]):
        self.keys = {kid: load_public_key(text) for kid, text in public_keys.items()}
        if any(kid != fingerprint(key) for kid, key in self.keys.items()):
            raise AuthenticationError("Public-key fingerprint mismatch")

    def verify(self, token: str, purpose: str) -> dict:
        try:
            if not isinstance(token, str) or len(token) > 16384:
                raise ValueError("Invalid token size")
            header = jwt.get_unverified_header(token)
            if (
                set(header) != {"alg", "typ", "kid"}
                or header["alg"] != "EdDSA"
                or header["typ"] != purpose
            ):
                raise ValueError("Invalid signature header")
            key = self.keys[header["kid"]]
            data = jwt.api_jws.PyJWS().decode(token, key, algorithms=["EdDSA"])
            result = json.loads(data)
            if not isinstance(result, dict):
                raise TypeError("Expected object")
            return result
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise AuthenticationError("Signature verification failed") from exc
