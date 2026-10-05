"""blockchain.py - Blocks, proof-of-work mining, and the rules every node enforces.

Account model: the chain's state is two tables, rebuilt by replaying all blocks:
    balances[address] -> coins        nonces[address] -> number of txs already sent
A block is valid only if every rule below passes, so a cheater's block is rejected by everyone.
"""
import hashlib
import time

from transaction import COINBASE, Transaction

DIFFICULTY = 4          # block hash must start with this many '0' hex digits (proof of work)
BLOCK_REWARD = 50       # coins created for the miner of each block
MAX_TX_PER_BLOCK = 10   # transactions per block (not counting the reward)


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Block:
    def __init__(self, index, prev_hash, timestamp, transactions, nonce=0):
        self.index, self.prev_hash, self.timestamp = index, prev_hash, timestamp
        self.transactions = transactions                       # first one is the coinbase reward
        self.nonce = nonce
        self.tx_root = sha256("".join(t.txid for t in transactions))
        self.hash = self.compute_hash()

    def compute_hash(self):
        return sha256(f"{self.index}|{self.prev_hash}|{self.timestamp}|"
                      f"{self.tx_root}|{self.nonce}|{DIFFICULTY}")

    @property
    def miner(self):
        return self.transactions[0].recipient if self.transactions else ""

    def to_dict(self):
        return {"index": self.index, "prev_hash": self.prev_hash, "timestamp": self.timestamp,
                "nonce": self.nonce, "hash": self.hash,
                "transactions": [t.to_dict() for t in self.transactions]}

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict) or not isinstance(d["transactions"], list):
            raise ValueError("malformed block")
        for key in ("index", "timestamp", "nonce"):
            if not isinstance(d[key], int) or isinstance(d[key], bool):
                raise ValueError(f"malformed block field: {key}")
        if not isinstance(d["prev_hash"], str):
            raise ValueError("malformed block field: prev_hash")
        block = cls(d["index"], d["prev_hash"], d["timestamp"],
                    [Transaction.from_dict(t) for t in d["transactions"]], d["nonce"])
        if block.hash != d.get("hash"):          # hash is recomputed locally, never trusted
            raise ValueError("block hash does not match its contents")
        return block


def make_genesis():
    return Block(0, "0" * 64, 0, [], 0)           # identical on every node


GENESIS_HASH = make_genesis().hash


# ---------- transaction rules ----------
def check_transaction(tx, balances, nonces, pending_out=0, pending_count=0):
    """Raise ValueError unless `tx` is valid against the given state.
    pending_* = what the sender already has waiting in the mempool (for double-spend checks)."""
    tx.validate_format()
    if tx.is_coinbase:
        raise ValueError("unexpected coinbase transaction")
    if not tx.verify_signature():
        raise ValueError("invalid signature")
    expected = nonces.get(tx.sender, 0) + pending_count
    if tx.nonce != expected:
        raise ValueError(f"bad nonce (expected {expected}, got {tx.nonce}) - replay or double-spend")
    if balances.get(tx.sender, 0) - pending_out < tx.amount:
        raise ValueError("insufficient balance")


def apply_transaction(tx, balances, nonces):
    check_transaction(tx, balances, nonces)
    balances[tx.sender] -= tx.amount
    balances[tx.recipient] = balances.get(tx.recipient, 0) + tx.amount
    nonces[tx.sender] = nonces.get(tx.sender, 0) + 1


def apply_coinbase(tx, balances, index):
    tx.validate_format()
    if not (tx.is_coinbase and tx.amount == BLOCK_REWARD and tx.nonce == index):
        raise ValueError("invalid coinbase (reward must be exactly the block reward)")
    balances[tx.recipient] = balances.get(tx.recipient, 0) + tx.amount


class Blockchain:
    def __init__(self):
        self.chain = [make_genesis()]
        self.balances, self.nonces = {}, {}

    @property
    def tip(self):
        return self.chain[-1]

    @property
    def height(self):
        return len(self.chain) - 1

    @staticmethod
    def validate_block(block, prev, balances, nonces):
        """Raise ValueError if invalid; otherwise apply the block to balances/nonces (pass copies)."""
        if block.index != prev.index + 1:
            raise ValueError("wrong block index")
        if block.prev_hash != prev.hash:
            raise ValueError("prev_hash does not link to the previous block")
        if block.hash != block.compute_hash():
            raise ValueError("block hash does not match its contents")
        if not block.hash.startswith("0" * DIFFICULTY):
            raise ValueError("proof of work is invalid")
        txs = block.transactions
        if not 1 <= len(txs) <= MAX_TX_PER_BLOCK + 1:
            raise ValueError("wrong number of transactions")
        if not txs[0].is_coinbase:
            raise ValueError("first transaction must be the coinbase reward")
        apply_coinbase(txs[0], balances, block.index)
        for tx in txs[1:]:
            apply_transaction(tx, balances, nonces)

    def add_block(self, block):
        balances, nonces = dict(self.balances), dict(self.nonces)
        self.validate_block(block, self.tip, balances, nonces)
        self.chain.append(block)
        self.balances, self.nonces = balances, nonces

    def try_replace(self, chain_dicts):
        """Longest valid chain wins. Returns True if our chain was replaced."""
        if not isinstance(chain_dicts, list) or len(chain_dicts) <= len(self.chain):
            return False
        new_chain = [Block.from_dict(d) for d in chain_dicts]
        if new_chain[0].index != 0 or new_chain[0].hash != GENESIS_HASH:
            raise ValueError("different genesis block")
        balances, nonces = {}, {}
        for prev, block in zip(new_chain, new_chain[1:]):
            self.validate_block(block, prev, balances, nonces)
        self.chain, self.balances, self.nonces = new_chain, balances, nonces
        return True

    def build_candidate(self, miner_address, mempool_txs):
        """Create the next block: reward for the miner + valid pending transactions."""
        index = self.tip.index + 1
        coinbase = Transaction(COINBASE, miner_address, BLOCK_REWARD, index)
        balances, nonces = dict(self.balances), dict(self.nonces)
        apply_coinbase(coinbase, balances, index)
        chosen = []
        for tx in sorted(mempool_txs, key=lambda t: (t.sender, t.nonce)):
            if len(chosen) >= MAX_TX_PER_BLOCK:
                break
            try:
                apply_transaction(tx, balances, nonces)
                chosen.append(tx)
            except ValueError:
                continue
        return Block(index, self.tip.hash, int(time.time()), [coinbase] + chosen)

    @staticmethod
    def mine(block, should_stop=lambda: False):
        """Proof of work: try nonces until the hash starts with DIFFICULTY zeros.
        Returns False if should_stop() becomes true (e.g. someone else found the block first)."""
        target = "0" * DIFFICULTY
        nonce = 0
        while True:
            block.nonce = nonce
            digest = block.compute_hash()
            if digest.startswith(target):
                block.hash = digest
                return True
            nonce += 1
            if nonce % 2000 == 0 and should_stop():
                return False
