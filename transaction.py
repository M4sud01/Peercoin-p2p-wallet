"""transaction.py - A signed transfer of coins (account/balance model).

Fields: sender, recipient (addresses), amount, nonce, public_key, signature.
 * nonce  = how many transactions the sender has already made (0, 1, 2, ...).
            Each nonce can be used ONCE, so a transaction cannot be replayed
            and two conflicting spends cannot both be valid (double-spend protection).
 * The signature covers (sender, recipient, amount, nonce), so nobody can change them.
 * A "coinbase" transaction (sender == COINBASE) is the mining reward; it has no signature.
"""
import hashlib
import json
import re

from wallet import address_from_public_key, verify_signature

COINBASE = "COINBASE"
MAX_AMOUNT = 10 ** 12
ADDRESS_RE = re.compile(r"^[0-9a-f]{40}$")


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


class Transaction:
    def __init__(self, sender, recipient, amount, nonce, public_key="", signature=""):
        self.sender, self.recipient = sender, recipient
        self.amount, self.nonce = amount, nonce
        self.public_key, self.signature = public_key, signature

    def payload(self):
        """The exact bytes that get signed (same on every computer)."""
        return json.dumps({"sender": self.sender, "recipient": self.recipient,
                           "amount": self.amount, "nonce": self.nonce},
                          sort_keys=True, separators=(",", ":")).encode()

    @property
    def txid(self):
        return hashlib.sha256(self.payload()).hexdigest()

    @property
    def is_coinbase(self):
        return self.sender == COINBASE

    def sign(self, wallet):
        self.public_key = wallet.public_key
        self.signature = wallet.sign(self.payload())

    def validate_format(self):
        if not _is_int(self.amount) or not 0 < self.amount <= MAX_AMOUNT:
            raise ValueError("amount must be a positive whole number")
        if not _is_int(self.nonce) or self.nonce < 0:
            raise ValueError("invalid nonce")
        if not isinstance(self.recipient, str) or not ADDRESS_RE.match(self.recipient):
            raise ValueError("invalid recipient address (need 40 lowercase hex characters)")
        if not self.is_coinbase:
            if not isinstance(self.sender, str) or not ADDRESS_RE.match(self.sender):
                raise ValueError("invalid sender address")
            if self.sender == self.recipient:
                raise ValueError("cannot send coins to yourself")

    def verify_signature(self):
        """Check 1: public key really belongs to the sender address.
        Check 2: the signature really was made by the matching private key."""
        if self.is_coinbase:
            return False
        try:
            return (address_from_public_key(self.public_key) == self.sender and
                    verify_signature(self.public_key, self.payload(), self.signature))
        except Exception:
            return False

    def to_dict(self):
        return {"sender": self.sender, "recipient": self.recipient, "amount": self.amount,
                "nonce": self.nonce, "public_key": self.public_key, "signature": self.signature}

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict):
            raise ValueError("transaction must be an object")
        return cls(d["sender"], d["recipient"], d["amount"], d["nonce"],
                   d.get("public_key", ""), d.get("signature", ""))
