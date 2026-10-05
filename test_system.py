"""test_system.py - Automated checks. Run:  python test_system.py

Covers signatures, double-spend protection, tampered blocks, and a real 3-node
network on localhost (gossip, mining, chain sync). Takes ~10-20 seconds."""
import time
import unittest

from blockchain import BLOCK_REWARD, Block, Blockchain
from node import P2PNode
from transaction import COINBASE, Transaction
from wallet import Wallet


def quiet(_msg):
    pass


def wait_until(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def mine_on(chain, miner, txs=()):
    block = chain.build_candidate(miner.address, list(txs))
    Blockchain.mine(block)
    chain.add_block(block)
    return block


class TestSignatures(unittest.TestCase):
    def test_valid_signature(self):
        a, b = Wallet(), Wallet()
        tx = Transaction(a.address, b.address, 10, 0)
        tx.sign(a)
        self.assertTrue(tx.verify_signature())

    def test_tampered_amount_breaks_signature(self):
        a, b = Wallet(), Wallet()
        tx = Transaction(a.address, b.address, 10, 0)
        tx.sign(a)
        tx.amount = 1000
        self.assertFalse(tx.verify_signature())

    def test_cannot_spend_someone_elses_coins(self):
        alice, eve, bob = Wallet(), Wallet(), Wallet()
        tx = Transaction(alice.address, bob.address, 10, 0)   # claims to be Alice...
        tx.sign(eve)                                          # ...but signed by Eve
        self.assertFalse(tx.verify_signature())


class TestBlockchain(unittest.TestCase):
    def setUp(self):
        self.alice, self.bob = Wallet(), Wallet()
        self.chain = Blockchain()

    def test_mining_gives_reward(self):
        mine_on(self.chain, self.alice)
        self.assertEqual(self.chain.balances[self.alice.address], BLOCK_REWARD)

    def test_payment_moves_coins(self):
        mine_on(self.chain, self.alice)
        tx = Transaction(self.alice.address, self.bob.address, 20, 0)
        tx.sign(self.alice)
        mine_on(self.chain, self.bob, [tx])
        self.assertEqual(self.chain.balances[self.alice.address], BLOCK_REWARD - 20)
        self.assertEqual(self.chain.balances[self.bob.address], 20 + BLOCK_REWARD)

    def test_replayed_transaction_rejected(self):
        mine_on(self.chain, self.alice)
        tx = Transaction(self.alice.address, self.bob.address, 20, 0)
        tx.sign(self.alice)
        mine_on(self.chain, self.bob, [tx])
        block = self.chain.build_candidate(self.bob.address, [])
        block.transactions.append(tx)                         # same tx again (replay)
        block.tx_root = Block(0, "", 0, block.transactions).tx_root
        Blockchain.mine(block)
        with self.assertRaises(ValueError):
            self.chain.add_block(block)

    def test_tampered_block_rejected(self):
        mine_on(self.chain, self.alice)
        tx = Transaction(self.alice.address, self.bob.address, 20, 0)
        tx.sign(self.alice)
        block = mine_on(self.chain, self.bob, [tx])
        data = block.to_dict()
        data["transactions"][1]["amount"] = 49                # change the amount after mining
        with self.assertRaises(ValueError):
            Block.from_dict(data)

    def test_fake_reward_rejected(self):
        block = self.chain.build_candidate(self.alice.address, [])
        block.transactions[0].amount = 1000                   # miner gives himself 1000 coins
        block.tx_root = Block(0, "", 0, block.transactions).tx_root
        Blockchain.mine(block)                                # even with valid proof of work
        with self.assertRaises(ValueError):
            self.chain.add_block(block)

    def test_longest_valid_chain_replaces_shorter(self):
        other = Blockchain()
        for _ in range(3):
            mine_on(other, self.bob)
        mine_on(self.chain, self.alice)
        self.assertTrue(self.chain.try_replace([b.to_dict() for b in other.chain]))
        self.assertEqual(self.chain.height, 3)


class TestMempoolDoubleSpend(unittest.TestCase):
    def test_double_spend_rejected(self):
        alice, bob, carol = Wallet(), Wallet(), Wallet()
        node = P2PNode("Alice", 0, alice, quiet)
        node.mine_block()                                     # Alice now has 50 coins
        node.submit_transaction(bob.address, 40)              # OK: 40 of 50
        with self.assertRaises(ValueError) as ctx:
            node.submit_transaction(carol.address, 40)        # would need 80 coins
        self.assertIn("insufficient", str(ctx.exception))
        conflicting = Transaction(alice.address, carol.address, 10, 0)   # nonce 0 already used
        conflicting.sign(alice)
        with node.lock, self.assertRaises(ValueError) as ctx:
            node._add_to_mempool(conflicting)
        self.assertIn("nonce", str(ctx.exception))


class TestNetwork(unittest.TestCase):
    def test_three_nodes_gossip_mine_and_sync(self):
        a = P2PNode("Alice", 7101, Wallet(), quiet)
        b = P2PNode("Bob", 7102, Wallet(), quiet)
        c = P2PNode("Chalie", 7103, Wallet(), quiet)
        nodes = [a, b, c]
        for n in nodes:
            n.start()
        try:
            b.connect("127.0.0.1", 7101)
            c.connect("127.0.0.1", 7102)                      # Alice <-> Bob <-> Chalie (not directly linked)
            a.mine_block()                                    # Alice earns 50
            self.assertTrue(wait_until(lambda: all(n.blockchain.height == 1 for n in nodes)))

            a.submit_transaction(b.wallet.address, 20)        # gossip: Alice -> Bob -> Chalie
            self.assertTrue(wait_until(lambda: all(len(n.mempool) == 1 for n in nodes)))

            c.mine_block()                                    # Chalie mines Alice's payment
            self.assertTrue(wait_until(lambda: all(n.blockchain.height == 2 for n in nodes)))
            self.assertTrue(wait_until(lambda: all(len(n.mempool) == 0 for n in nodes)))
            for n in nodes:
                self.assertEqual(n.blockchain.balances[a.wallet.address], 30)
                self.assertEqual(n.blockchain.balances[b.wallet.address], 20)
                self.assertEqual(n.blockchain.balances[c.wallet.address], 50)

            d = P2PNode("Late", 7104, Wallet(), quiet)        # a new node joins later
            d.start()
            nodes.append(d)
            d.connect("127.0.0.1", 7103)
            self.assertTrue(wait_until(lambda: d.blockchain.height == 2))
            self.assertEqual(d.blockchain.tip.hash, a.blockchain.tip.hash)
        finally:
            for n in nodes:
                n.stop()

    def test_fork_resolves_to_longest_chain(self):
        a = P2PNode("A", 7201, Wallet(), quiet)
        b = P2PNode("B", 7202, Wallet(), quiet)
        a.start()
        b.start()
        try:
            a.mine_block()                                    # A: height 1
            b.mine_block()
            b.mine_block()                                    # B: height 2 (longer fork)
            a.connect("127.0.0.1", 7202)
            self.assertTrue(wait_until(lambda: a.blockchain.tip.hash == b.blockchain.tip.hash))
            self.assertEqual(a.blockchain.height, 2)
            self.assertEqual(a.blockchain.balances.get(a.wallet.address, 0), 0)   # A's orphaned reward is gone
        finally:
            a.stop()
            b.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
