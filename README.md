# PeerCoin - P2P Cryptocurrency Wallet, Transactions & Mining

A small peer-to-peer cryptocurrency in Python. Every running instance is a **node** that is a
TCP server + client, owns a **wallet** (private/public key pair), keeps a **mempool** of pending
payments, can **mine** blocks (proof of work), and always follows the **longest valid chain**.
There is **no central server and no bank** - the rules are enforced by every node.

Built on the PeerLink networking layer (TCP sockets, threads, length-prefixed JSON framing).

## 1. Requirements
- Python 3.9+, Tkinter (`sudo apt install python3-tk` on some Linux systems)
- One package: `pip install -r requirements.txt` (the `ecdsa` library)

## 2. Project Structure
| File | Responsibility |
|---|---|
| `main.py` | Tkinter GUI (wallet, send, mine, mempool, blockchain, balances) |
| `node.py` | P2P server/client, gossip, mempool, mining, chain sync |
| `blockchain.py` | Blocks, proof of work, validation rules, longest-chain rule |
| `transaction.py` | Signed transaction (account model + nonce) |
| `wallet.py` | Key pair, address, sign / verify (ECDSA, secp256k1) |
| `protocol.py` | Message types and `[4-byte length][JSON]` framing |
| `test_system.py` | Automated tests (`python test_system.py`) |
| `wallets/` | Saved wallets `<name>.json` (**contain private keys - never share/commit**) |

## 3. Quick Demo (3 windows)
```
python main.py      # run it 3 times
```
1. **Alice** port `5000`, **Bob** port `5001`, **Chalie** port `5002` -> *Start Node* in each.
2. Bob connects to `127.0.0.1:5000`. Chalie connects to `127.0.0.1:5001` (Alice and Chalie are not directly linked).
3. Alice clicks **Mine Block** -> she gets 50 coins (the reward). Everyone's chain height becomes 1.
4. Alice selects Bob in *Connected Peers* -> **Use Selected Peer** -> amount `20` -> **Sign & Send**.
   The payment shows in the **Mempool** tab of all three windows (gossip), but nobody has the coins yet.
5. Chalie clicks **Mine Block** -> the block with Alice's payment is added on every node.
   Check the **Balances** tab: Alice 30, Bob 20, Chalie 50.
6. Try to cheat: Alice sends 40 coins twice (she only has 30) -> `insufficient balance`.
7. Close Bob, mine on Alice and Chalie, reopen Bob and connect -> Bob downloads the missing blocks.

## 4. How It Works
**Wallet.** A private key (secret) signs; the public key verifies. The **address** is the first 40 hex
chars of SHA-256(public key). Wallets are saved in `wallets/<name>.json`.

**Transaction (account model).** `sender, recipient, amount, nonce, public_key, signature`.
The signature covers `sender, recipient, amount, nonce`; changing any of them breaks it. Every node checks
that (a) the public key matches the sender address and (b) the signature is valid.

**Account model vs UTXO.** We store a *balance* per address (like a bank ledger). Bitcoin uses UTXOs
(unspent outputs) instead. The account model is simpler; the **nonce** replaces UTXO's "spend once" rule.

**Double-spend protection.**
- *Nonce:* the n-th payment of an account must carry nonce n. A nonce works once, so a payment cannot be replayed, and two different payments with the same nonce cannot both be valid.
- *Balance check including pending payments:* the mempool counts what the sender already has pending, so spending the same coins twice is rejected immediately.
- *Chain:* even if two nodes saw conflicting payments in a different order, only the one mined first stays valid; the other becomes invalid and is dropped.

**Mempool.** Valid but unconfirmed transactions wait here. Each node re-validates a payment it receives
before storing and forwarding it (*gossip*), so invalid payments die at the first honest node.

**Mining.** A miner builds a block = 1 coinbase reward + up to 10 mempool transactions, then tries nonces until
`SHA-256(block header)` starts with `0000` (difficulty 4). Finding it is hard; checking it is one hash.
Mining stops automatically if another block arrives first.

**Consensus.** Every node validates every block (index, link to previous hash, hash correct, proof of work, reward
exactly 50, every transaction valid). If a peer has a longer valid chain, the node requests it (`get_chain`) and
switches to it; payments from dropped blocks go back to the mempool.

## 5. Network Messages
`hello` / `hello_ack` (id, name, port, address, chain height) - `new_tx` - `new_block` - `get_chain` - `chain`

## 6. Run the Tests
```
python test_system.py
```
Checks: valid/forged/tampered signatures, mining reward, payments, replay, double-spend, tampered block,
fake reward, longest-chain rule, 3-node gossip + mining + sync, late joiner, and fork resolution.

## 7. Limitations (good "future work" answers)
- Private keys are stored unencrypted in `wallets/*.json`; real wallets encrypt them and use seed phrases.
- Fixed difficulty (real chains adjust it), no transaction fees, no Merkle tree (a single hash of tx ids is used).
- Whole chain is sent on sync; real nodes sync block-by-block. No peer discovery, NAT traversal or encryption (TLS).
- State is in memory only: restarting a node starts from an empty chain (it re-syncs from peers).
- No protection against a 51% attack, Sybil attacks, or network-level DoS.
