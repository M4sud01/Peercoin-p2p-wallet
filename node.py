"""node.py - One peer = P2P server + client + wallet + mempool + miner.

Flow of a payment:
  1. submit_transaction(): wallet signs a tx -> checked -> put in MEMPOOL -> broadcast (gossip)
  2. every peer re-checks the tx, stores it in its own mempool, and forwards it
  3. mine_block(): a miner packs mempool txs into a block and does proof of work
  4. the block is broadcast; every peer re-validates it and adds it to its chain
  5. if a peer is behind (or on a shorter fork), it asks get_chain and keeps the longest valid chain
"""
import socket
import threading
import uuid

import protocol
from blockchain import Block, Blockchain, check_transaction
from transaction import Transaction


class PeerConnection:
    def __init__(self, sock, addr, info):
        self.sock, self.addr = sock, addr
        self.peer_id = str(info["peer_id"])
        self.name = str(info["peer_name"])[:32]
        self.listen_port = info.get("port")
        self.address = str(info.get("address", ""))
        self.send_lock = threading.Lock()


class P2PNode:
    def __init__(self, name, port, wallet, on_event=print, on_change=lambda: None):
        self.name, self.port, self.wallet = name, port, wallet
        self.peer_id = uuid.uuid4().hex[:8]
        self.on_event, self.on_change = on_event, on_change
        self.blockchain = Blockchain()
        self.mempool = {}                         # txid -> Transaction (pending, not yet in a block)
        self.address_names = {wallet.address: name}
        self.lock = threading.RLock()             # protects blockchain + mempool
        self.peers, self.peers_lock = {}, threading.Lock()
        self.server_socket, self.running = None, False
        self.mining, self._stop_mining = False, threading.Event()

    # ================= server role =================
    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)      # TCP
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", self.port))
            s.listen()
        except OSError as e:
            s.close()
            raise OSError(f"Cannot listen on port {self.port}: {e}")
        self.server_socket, self.running = s, True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self.on_event(f"[SYSTEM] Node started: {self.name} [{self.peer_id}] on port {self.port}")
        self.on_event(f"[WALLET] Address: {self.wallet.address}")

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_incoming, args=(conn, addr), daemon=True).start()

    def _intro_args(self):
        with self.lock:
            return (self.peer_id, self.name, self.port, self.wallet.address, self.blockchain.height)

    def _handle_incoming(self, conn, addr):
        try:
            conn.settimeout(5)
            hello = protocol.recv_json(conn)
            if hello.get("type") != protocol.HELLO:
                raise ValueError("expected hello")
            protocol.send_json(conn, protocol.make_hello_ack(*self._intro_args()))
            conn.settimeout(None)
            if not self._register(conn, addr, hello):
                conn.close()
        except Exception as e:
            self.on_event(f"[ERROR] Incoming connection from {addr[0]}:{addr[1]} failed: {e}")
            conn.close()

    # ================= client role =================
    def connect(self, ip, port):
        try:
            socket.inet_aton(ip)
        except OSError:
            raise ValueError(f"Invalid IP address: {ip}")
        if not 1 <= port <= 65535:
            raise ValueError(f"Invalid port: {port}")
        if port == self.port and ip in ("127.0.0.1", "localhost"):
            raise ValueError("Cannot connect to yourself")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect((ip, port))
            protocol.send_json(sock, protocol.make_hello(*self._intro_args()))
            ack = protocol.recv_json(sock)
            if ack.get("type") != protocol.HELLO_ACK:
                raise ValueError("expected hello_ack")
            sock.settimeout(None)
        except Exception as e:
            sock.close()
            raise OSError(f"Connection failed: {e}")
        if not self._register(sock, (ip, port), ack):
            sock.close()
            raise ValueError("Already connected to this peer")
        return ack["peer_name"]

    # ================= peers =================
    def _register(self, sock, addr, info):
        pc = PeerConnection(sock, addr, info)
        with self.peers_lock:
            if pc.peer_id == self.peer_id or pc.peer_id in self.peers:
                return False
            self.peers[pc.peer_id] = pc
        if pc.address:
            self.address_names[pc.address] = pc.name
        self.on_event(f"[SYSTEM] Connected to {pc.name} ({addr[0]}:{addr[1]})")
        self.on_change()
        threading.Thread(target=self._receive_loop, args=(pc,), daemon=True).start()
        # catch the new peer up: share our pending transactions; ask for its chain if it is ahead
        with self.lock:
            txs = [t.to_dict() for t in self.mempool.values()]
            my_height = self.blockchain.height
        for d in txs:
            self._send(pc, {"type": protocol.NEW_TX, "tx": d})
        remote_height = info.get("height")
        if isinstance(remote_height, int) and remote_height > my_height:
            self.on_event(f"[SYNC] {pc.name} has a longer chain ({remote_height} > {my_height}); requesting it")
            self._send(pc, {"type": protocol.GET_CHAIN})
        return True

    def get_peers(self):
        with self.peers_lock:
            return list(self.peers.values())

    def _remove(self, pc):
        with self.peers_lock:
            removed = self.peers.pop(pc.peer_id, None)
        for fn in (lambda: pc.sock.shutdown(socket.SHUT_RDWR), pc.sock.close):
            try:
                fn()
            except OSError:
                pass
        if removed:
            self.on_change()

    def _send(self, pc, msg):
        try:
            with pc.send_lock:
                protocol.send_json(pc.sock, msg)
        except OSError:
            self._remove(pc)

    def _broadcast(self, msg, exclude=None):
        for pc in self.get_peers():
            if pc is not exclude:
                self._send(pc, msg)

    # ================= receiving =================
    def _receive_loop(self, pc):
        try:
            while self.running:
                msg = protocol.recv_json(pc.sock)           # connection problems end the loop
                try:
                    self._dispatch(pc, msg)
                except (ValueError, KeyError, TypeError, AttributeError) as e:
                    self.on_event(f"[REJECTED] from {pc.name}: {e}")   # bad data: ignore, keep connection
        except Exception as e:
            if self.running:
                self.on_event(f"[SYSTEM] {pc.name} disconnected ({e})")
        finally:
            self._remove(pc)

    def _dispatch(self, pc, msg):
        kind = msg.get("type")
        if kind == protocol.NEW_TX:
            self._on_tx(pc, msg)
        elif kind == protocol.NEW_BLOCK:
            self._on_block(pc, msg)
        elif kind == protocol.GET_CHAIN:
            with self.lock:
                chain = [b.to_dict() for b in self.blockchain.chain]
            self._send(pc, {"type": protocol.CHAIN, "chain": chain})
        elif kind == protocol.CHAIN:
            self._on_chain(pc, msg)

    def _on_tx(self, pc, msg):
        tx = Transaction.from_dict(msg["tx"])
        with self.lock:
            if tx.txid in self.mempool:
                return                                      # already known -> stops gossip loops
            self._add_to_mempool(tx)                        # raises ValueError if invalid
        self.on_event(f"[TX] Received valid transaction {tx.txid[:10]}... from {pc.name}")
        self.on_change()
        self._broadcast({"type": protocol.NEW_TX, "tx": tx.to_dict()}, exclude=pc)

    def _on_block(self, pc, msg):
        block = Block.from_dict(msg["block"])
        need_chain = accepted = False
        with self.lock:
            tip = self.blockchain.tip
            if block.index <= tip.index:
                return                                      # old block or fork of equal length
            if block.index == tip.index + 1 and block.prev_hash == tip.hash:
                self.blockchain.add_block(block)            # raises ValueError if invalid
                self._revalidate_mempool()
                self._stop_mining.set()                     # someone else won this round
                accepted = True
            else:
                need_chain = True                           # we are behind or on another fork
        if accepted:
            self.on_event(f"[BLOCK] Accepted block #{block.index} from {pc.name} ({block.hash[:12]}...)")
            self.on_change()
            self._broadcast({"type": protocol.NEW_BLOCK, "block": block.to_dict()}, exclude=pc)
        elif need_chain:
            self.on_event(f"[SYNC] Block #{block.index} does not fit our chain; requesting full chain from {pc.name}")
            self._send(pc, {"type": protocol.GET_CHAIN})

    def _on_chain(self, pc, msg):
        with self.lock:
            old_txs = [t for b in self.blockchain.chain for t in b.transactions[1:]]
            replaced = self.blockchain.try_replace(msg["chain"])   # raises ValueError if invalid
            if replaced:
                self._revalidate_mempool(extra=old_txs)     # txs from dropped blocks go back to pending
                self._stop_mining.set()
                tip = self.blockchain.tip
        if replaced:
            self.on_event(f"[SYNC] Switched to longer chain from {pc.name} (height {tip.index})")
            self.on_change()
            self._broadcast({"type": protocol.NEW_BLOCK, "block": tip.to_dict()}, exclude=pc)

    # ================= mempool =================
    def _add_to_mempool(self, tx):
        """Caller holds self.lock. Raises ValueError if the tx is invalid.
        Counts the sender's other PENDING txs, so a double-spend is rejected even before mining."""
        pending = [t for t in self.mempool.values() if t.sender == tx.sender]
        check_transaction(tx, self.blockchain.balances, self.blockchain.nonces,
                          pending_out=sum(t.amount for t in pending), pending_count=len(pending))
        self.mempool[tx.txid] = tx

    def _revalidate_mempool(self, extra=()):
        """Caller holds self.lock. Drop mempool txs that are no longer valid (e.g. now in a block)."""
        candidates = {t.txid: t for t in list(self.mempool.values()) + list(extra)}
        self.mempool = {}
        for tx in sorted(candidates.values(), key=lambda t: (t.sender, t.nonce)):
            try:
                self._add_to_mempool(tx)
            except ValueError:
                pass

    # ================= actions used by the GUI =================
    def submit_transaction(self, recipient, amount):
        """Create, sign, store and broadcast a payment from this node's wallet."""
        with self.lock:
            mine = [t for t in self.mempool.values() if t.sender == self.wallet.address]
            nonce = self.blockchain.nonces.get(self.wallet.address, 0) + len(mine)
            tx = Transaction(self.wallet.address, recipient, amount, nonce)
            tx.sign(self.wallet)
            self._add_to_mempool(tx)                        # raises ValueError if invalid
        self.on_event(f"[TX] Created transaction {tx.txid[:10]}... ({amount} coins) -> mempool")
        self.on_change()
        self._broadcast({"type": protocol.NEW_TX, "tx": tx.to_dict()})
        return tx

    def mine_block(self):
        """Proof of work on the pending transactions. Blocks the calling thread (run it in a thread)."""
        with self.lock:
            if self.mining:
                raise ValueError("Already mining")
            self.mining = True
            self._stop_mining.clear()
            block = self.blockchain.build_candidate(self.wallet.address, list(self.mempool.values()))
        self.on_event(f"[MINING] Mining block #{block.index} with {len(block.transactions) - 1} transaction(s)...")
        self.on_change()
        try:
            found = Blockchain.mine(block, self._stop_mining.is_set)
        finally:
            with self.lock:
                self.mining = False
        if not found:
            self.on_event("[MINING] Stopped: another block arrived first")
            return None
        try:
            with self.lock:
                self.blockchain.add_block(block)
                self._revalidate_mempool()
        except ValueError:
            self.on_event("[MINING] Block discarded: the chain changed while mining")
            return None
        self.on_event(f"[MINING] Found block #{block.index} ({block.hash[:12]}...) reward {block.transactions[0].amount} coins")
        self.on_change()
        self._broadcast({"type": protocol.NEW_BLOCK, "block": block.to_dict()})
        return block

    def snapshot(self):
        """A consistent copy of everything the GUI shows."""
        with self.lock:
            me = self.wallet.address
            return {"height": self.blockchain.height,
                    "balance": self.blockchain.balances.get(me, 0),
                    "pending_out": sum(t.amount for t in self.mempool.values() if t.sender == me),
                    "balances": dict(self.blockchain.balances),
                    "nonces": dict(self.blockchain.nonces),
                    "mempool": list(self.mempool.values()),
                    "blocks": list(self.blockchain.chain)}

    def stop(self):
        self.running = False
        self._stop_mining.set()
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
        for pc in self.get_peers():
            self._remove(pc)
        self.on_event("[SYSTEM] Node stopped")
