"""wallet.py - Key pair (private/public key), address, signing and verifying.

private key  -> secret, used to SIGN transactions (never shared)
public key   -> shared, used by everyone to VERIFY signatures
address      -> first 40 hex chars of SHA-256(public key); this is the "account number"
"""
import hashlib
import json
import os
import re

from ecdsa import SECP256k1, SigningKey, VerifyingKey

WALLET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wallets")


def address_from_public_key(public_key_hex):
    return hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()[:40]


def verify_signature(public_key_hex, data, signature_hex):
    """True only if signature_hex is a valid ECDSA signature of `data` for this public key."""
    try:
        vk = VerifyingKey.from_string(bytes.fromhex(public_key_hex), curve=SECP256k1)
        return bool(vk.verify(bytes.fromhex(signature_hex), data, hashfunc=hashlib.sha256))
    except Exception:
        return False


class Wallet:
    def __init__(self, private_key_hex=None):
        if private_key_hex:
            self._sk = SigningKey.from_string(bytes.fromhex(private_key_hex), curve=SECP256k1)
        else:
            self._sk = SigningKey.generate(curve=SECP256k1)
        self.private_key = self._sk.to_string().hex()
        self.public_key = self._sk.get_verifying_key().to_string().hex()
        self.address = address_from_public_key(self.public_key)

    def sign(self, data):
        """Sign bytes with the private key; returns the signature as hex."""
        return self._sk.sign(data, hashfunc=hashlib.sha256).hex()

    # ---------- save / load (wallets/<name>.json) ----------
    @staticmethod
    def _path(name):
        return os.path.join(WALLET_DIR, re.sub(r"[^A-Za-z0-9_-]", "_", name) + ".json")

    def save(self, name):
        os.makedirs(WALLET_DIR, exist_ok=True)
        with open(self._path(name), "w") as f:
            json.dump({"private_key": self.private_key, "public_key": self.public_key,
                       "address": self.address}, f, indent=2)

    @classmethod
    def load_or_create(cls, name):
        """Re-open the wallet saved for `name`, or create (and save) a new one."""
        path = cls._path(name)
        if os.path.exists(path):
            with open(path) as f:
                return cls(json.load(f)["private_key"])
        wallet = cls()
        wallet.save(name)
        return wallet
