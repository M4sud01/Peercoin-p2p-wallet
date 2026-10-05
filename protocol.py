"""protocol.py - Network messages and framing (same idea as PeerLink).

Framing: [4-byte big-endian length][JSON payload (UTF-8)]  - TCP is a byte stream,
so the length tells the receiver where each message ends.

Messages:
  hello / hello_ack : introduce yourself (id, name, port, wallet address, chain height)
  new_tx            : a signed transaction (gossiped to all peers)
  new_block         : a freshly mined block (gossiped to all peers)
  get_chain         : "send me your whole blockchain"
  chain             : the whole blockchain (list of blocks)
"""
import json
import struct

MAX_MESSAGE_SIZE = 32 * 1024 * 1024   # sanity limit (a full chain can be large)

HELLO, HELLO_ACK = "hello", "hello_ack"
NEW_TX, NEW_BLOCK, GET_CHAIN, CHAIN = "new_tx", "new_block", "get_chain", "chain"


def _intro(kind, peer_id, name, port, address, height):
    return {"type": kind, "peer_id": peer_id, "peer_name": name,
            "port": port, "address": address, "height": height}


def make_hello(peer_id, name, port, address, height):
    return _intro(HELLO, peer_id, name, port, address, height)


def make_hello_ack(peer_id, name, port, address, height):
    return _intro(HELLO_ACK, peer_id, name, port, address, height)


def send_json(sock, obj):
    payload = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def recv_exact(sock, n):
    """Read exactly n bytes (one recv() may return fewer). Raises ConnectionError."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError("connection closed by peer")
        buf.extend(chunk)
    return bytes(buf)


def recv_json(sock):
    (length,) = struct.unpack("!I", recv_exact(sock, 4))
    if length == 0 or length > MAX_MESSAGE_SIZE:
        raise ValueError(f"invalid message length: {length}")
    return json.loads(recv_exact(sock, length).decode("utf-8"))
