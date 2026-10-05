python --version"""main.py - Tkinter GUI for PeerCoin (P2P wallet + miner). Run: python main.py"""
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from blockchain import BLOCK_REWARD, DIFFICULTY
from node import P2PNode
from transaction import COINBASE
from wallet import Wallet


class App:
    def __init__(self, root):
        self.root, self.node = root, None
        self.ui_queue = queue.Queue()            # worker threads -> UI thread
        self.peers, self.blocks = [], []
        root.title("PeerCoin - P2P Wallet & Miner")
        root.geometry("1040x760")
        self._build()
        self._set_running(False)
        self._poll()
        root.protocol("WM_DELETE_WINDOW", self._close)

    # ---------- layout ----------
    def _tree(self, parent, cols, widths):
        frame = ttk.Frame(parent)
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=8)
        for c, w in zip(cols, widths):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(frame, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return frame, tree

    def _build(self):
        r = self.root
        f = ttk.LabelFrame(r, text="My Node & Wallet")
        f.pack(fill="x", padx=8, pady=4)
        self.name_var, self.port_var = tk.StringVar(value="Alice"), tk.StringVar(value="5000")
        ttk.Label(f, text="Name:").grid(row=0, column=0, padx=4, pady=4)
        self.name_e = ttk.Entry(f, textvariable=self.name_var, width=14)
        self.name_e.grid(row=0, column=1)
        ttk.Label(f, text="Port:").grid(row=0, column=2, padx=4)
        self.port_e = ttk.Entry(f, textvariable=self.port_var, width=8)
        self.port_e.grid(row=0, column=3)
        self.start_b = ttk.Button(f, text="Start Node", command=self.start_node)
        self.start_b.grid(row=0, column=4, padx=6)
        self.stop_b = ttk.Button(f, text="Stop", command=self.stop_node)
        self.stop_b.grid(row=0, column=5)
        self.addr_var = tk.StringVar(value="Not running")
        ttk.Label(f, textvariable=self.addr_var).grid(row=1, column=0, columnspan=5, sticky="w", padx=4)
        self.copy_b = ttk.Button(f, text="Copy Address", command=self.copy_address)
        self.copy_b.grid(row=1, column=5, pady=2)
        self.balance_var = tk.StringVar(value="Balance: -")
        ttk.Label(f, textvariable=self.balance_var, font=("TkDefaultFont", 12, "bold")).grid(
            row=2, column=0, columnspan=6, sticky="w", padx=4, pady=(0, 4))

        c = ttk.LabelFrame(r, text="Connect to Another Peer")
        c.pack(fill="x", padx=8, pady=4)
        self.ip_var, self.rport_var = tk.StringVar(value="127.0.0.1"), tk.StringVar(value="5001")
        ttk.Label(c, text="IP:").grid(row=0, column=0, padx=4, pady=4)
        self.ip_e = ttk.Entry(c, textvariable=self.ip_var, width=18)
        self.ip_e.grid(row=0, column=1)
        ttk.Label(c, text="Port:").grid(row=0, column=2, padx=4)
        self.rport_e = ttk.Entry(c, textvariable=self.rport_var, width=8)
        self.rport_e.grid(row=0, column=3)
        self.conn_b = ttk.Button(c, text="Connect", command=self.connect_peer)
        self.conn_b.grid(row=0, column=4, padx=6)

        mid = ttk.Frame(r)
        mid.pack(fill="both", expand=True, padx=8, pady=4)
        pf = ttk.LabelFrame(mid, text="Connected Peers")
        pf.pack(side="left", fill="y")
        self.peer_list = tk.Listbox(pf, width=34, exportselection=False)
        self.peer_list.pack(fill="both", expand=True, padx=4, pady=4)

        nb = ttk.Notebook(mid)
        nb.pack(side="left", fill="both", expand=True, padx=(6, 0))

        # --- tab 1: send + mine ---
        t1 = ttk.Frame(nb)
        nb.add(t1, text="Send / Mine")
        ttk.Label(t1, text="Recipient address:").grid(row=0, column=0, sticky="w", padx=6, pady=(10, 2))
        self.to_var, self.amount_var = tk.StringVar(), tk.StringVar()
        self.to_e = ttk.Entry(t1, textvariable=self.to_var, width=50)
        self.to_e.grid(row=1, column=0, columnspan=2, sticky="we", padx=6)
        self.use_b = ttk.Button(t1, text="Use Selected Peer", command=self.use_selected)
        self.use_b.grid(row=1, column=2, padx=6)
        ttk.Label(t1, text="Amount (whole coins):").grid(row=2, column=0, sticky="w", padx=6, pady=(10, 2))
        self.amount_e = ttk.Entry(t1, textvariable=self.amount_var, width=14)
        self.amount_e.grid(row=3, column=0, sticky="w", padx=6)
        self.send_b = ttk.Button(t1, text="Sign & Send", command=self.send_coins)
        self.send_b.grid(row=3, column=1, sticky="w")
        ttk.Separator(t1).grid(row=4, column=0, columnspan=3, sticky="we", pady=14, padx=6)
        ttk.Label(t1, text=f"Mining packs pending transactions into a block (difficulty {DIFFICULTY}).\n"
                           f"The miner earns {BLOCK_REWARD} coins per block.", justify="left").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=6)
        self.mine_b = ttk.Button(t1, text="Mine Block", command=self.mine)
        self.mine_b.grid(row=6, column=0, sticky="w", padx=6, pady=8)

        # --- tab 2: mempool ---
        t2 = ttk.Frame(nb)
        nb.add(t2, text="Mempool (pending)")
        fr, self.mempool_tree = self._tree(t2, ("TxID", "From", "To", "Amount", "Nonce"), (90, 150, 150, 70, 50))
        fr.pack(fill="both", expand=True, padx=4, pady=4)

        # --- tab 3: blockchain ---
        t3 = ttk.Frame(nb)
        nb.add(t3, text="Blockchain")
        fr, self.block_tree = self._tree(t3, ("#", "Hash", "Prev", "Txs", "Nonce", "Miner"), (35, 120, 120, 40, 60, 130))
        fr.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        self.block_tree.bind("<<TreeviewSelect>>", lambda e: self._show_block())
        self.block_text = tk.Text(t3, height=8, state="disabled", wrap="word", font=("Courier", 9))
        self.block_text.pack(fill="x", padx=4, pady=4)

        # --- tab 4: balances ---
        t4 = ttk.Frame(nb)
        nb.add(t4, text="Balances")
        fr, self.bal_tree = self._tree(t4, ("Account", "Address", "Balance", "Txs sent"), (150, 250, 80, 70))
        fr.pack(fill="both", expand=True, padx=4, pady=4)

        lf = ttk.LabelFrame(r, text="Event Log")
        lf.pack(fill="both", padx=8, pady=4)
        self.log = tk.Text(lf, height=9, state="disabled", wrap="word", font=("Courier", 9))
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lf, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log["yscrollcommand"] = sb.set

        self.status = tk.StringVar(value="Ready")
        ttk.Label(r, textvariable=self.status, relief="sunken", anchor="w").pack(fill="x", side="bottom")

    def _set_running(self, running):
        for w in (self.name_e, self.port_e, self.start_b):
            w.config(state="disabled" if running else "normal")
        for w in (self.stop_b, self.copy_b, self.ip_e, self.rport_e, self.conn_b, self.to_e,
                  self.use_b, self.amount_e, self.send_b, self.mine_b):
            w.config(state="normal" if running else "disabled")

    # ---------- thread-safe UI updates ----------
    def _ui(self, fn, *a):
        self.ui_queue.put((fn, a))

    def _poll(self):
        while not self.ui_queue.empty():
            fn, a = self.ui_queue.get()
            fn(*a)
        self.root.after(100, self._poll)

    def _log(self, line):
        self.log.config(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _on_event(self, line):
        self._ui(self._log, line)

    def _on_change(self):
        self._ui(self._refresh)

    # ---------- display ----------
    def _label(self, addr):
        if addr == COINBASE:
            return "COINBASE (reward)"
        name = self.node.address_names.get(addr) if self.node else None
        return f"{name} ({addr[:6]}...)" if name else addr[:10] + "..."

    def _refresh(self):
        for tree in (self.mempool_tree, self.bal_tree):
            tree.delete(*tree.get_children())
        selected_peer = self._selected_peer()
        self.peer_list.delete(0, "end")
        if not self.node:
            self.block_tree.delete(*self.block_tree.get_children())
            self.balance_var.set("Balance: -")
            return
        s = self.node.snapshot()
        self.balance_var.set(f"Balance: {s['balance']} coins   (pending to spend: {s['pending_out']})"
                             f"   |   Chain height: {s['height']}")
        self.peers = self.node.get_peers()
        for i, p in enumerate(self.peers):
            self.peer_list.insert("end", f"{p.name}  {p.address[:10]}...  {p.addr[0]}:{p.listen_port or p.addr[1]}")
            if selected_peer and p.peer_id == selected_peer.peer_id:
                self.peer_list.selection_set(i)
        for tx in s["mempool"]:
            self.mempool_tree.insert("", "end", values=(tx.txid[:10], self._label(tx.sender),
                                                        self._label(tx.recipient), tx.amount, tx.nonce))
        for addr, bal in sorted(s["balances"].items(), key=lambda kv: -kv[1]):
            self.bal_tree.insert("", "end", values=(self._label(addr), addr[:24] + "...", bal, s["nonces"].get(addr, 0)))
        keep = self.block_tree.selection()
        self.block_tree.delete(*self.block_tree.get_children())
        self.blocks = s["blocks"]
        for b in self.blocks:
            self.block_tree.insert("", "end", iid=str(b.index), values=(
                b.index, b.hash[:14] + "...", b.prev_hash[:14] + "...", max(len(b.transactions) - 1, 0),
                b.nonce, self._label(b.miner) if b.transactions else "-"))
        if keep and self.block_tree.exists(keep[0]):
            self.block_tree.selection_set(keep[0])
        self.block_tree.see(str(self.blocks[-1].index))

    def _show_block(self):
        sel = self.block_tree.selection()
        if not sel or int(sel[0]) >= len(self.blocks):
            return
        b = self.blocks[int(sel[0])]
        lines = [f"Block #{b.index}", f"Hash : {b.hash}", f"Prev : {b.prev_hash}", f"Nonce: {b.nonce}",
                 f"Time : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(b.timestamp))}", "Transactions:"]
        for tx in b.transactions:
            lines.append(f"  {self._label(tx.sender)} -> {self._label(tx.recipient)}: {tx.amount}"
                         + ("" if tx.is_coinbase else f"  (nonce {tx.nonce}, id {tx.txid[:10]})"))
        if not b.transactions:
            lines.append("  (genesis block - no transactions)")
        self.block_text.config(state="normal")
        self.block_text.delete("1.0", "end")
        self.block_text.insert("end", "\n".join(lines))
        self.block_text.config(state="disabled")

    def _selected_peer(self):
        sel = self.peer_list.curselection()
        return self.peers[sel[0]] if sel and sel[0] < len(self.peers) else None

    # ---------- actions ----------
    def start_node(self):
        name = self.name_var.get().strip()
        if not name:
            return messagebox.showerror("Error", "Name cannot be empty")
        try:
            port = int(self.port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            return messagebox.showerror("Error", "Port must be an integer between 1 and 65535")
        wallet = Wallet.load_or_create(name)          # wallets/<name>.json
        node = P2PNode(name, port, wallet, self._on_event, self._on_change)
        try:
            node.start()
        except OSError as e:
            return messagebox.showerror("Error", str(e))
        self.node = node
        self.addr_var.set(f"{name} | Address: {wallet.address}")
        self.status.set(f"Node started on port {port}")
        self._set_running(True)
        self._refresh()

    def stop_node(self):
        if self.node:
            self.node.stop()
            self.node = None
        self.addr_var.set("Not running")
        self.status.set("Node stopped")
        self._set_running(False)
        self._refresh()

    def copy_address(self):
        if self.node:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.node.wallet.address)
            self.status.set("Address copied to clipboard")

    def use_selected(self):
        p = self._selected_peer()
        if not p or not p.address:
            return messagebox.showwarning("No peer selected", "Select a connected peer first.")
        self.to_var.set(p.address)

    def connect_peer(self):
        ip = self.ip_var.get().strip()
        try:
            port = int(self.rport_var.get())
        except ValueError:
            return messagebox.showerror("Error", "Invalid port")

        def work():
            try:
                name = self.node.connect(ip, port)
                self._ui(self.status.set, f"Connected to {name} ({ip}:{port})")
            except Exception as e:
                self._ui(self._log, f"[ERROR] {e}")
                self._ui(self.status.set, str(e))
        threading.Thread(target=work, daemon=True).start()

    def send_coins(self):
        to = self.to_var.get().strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", to):
            return messagebox.showerror("Error", "Recipient must be a 40-character address (use 'Use Selected Peer')")
        try:
            amount = int(self.amount_var.get())
            if amount <= 0:
                raise ValueError
        except ValueError:
            return messagebox.showerror("Error", "Amount must be a positive whole number")

        def work():
            try:
                tx = self.node.submit_transaction(to, amount)
                self._ui(self.status.set, f"Sent {amount} coins (tx {tx.txid[:10]}...) - waiting to be mined")
            except Exception as e:
                self._ui(self._log, f"[REJECTED] {e}")
                self._ui(messagebox.showerror, "Transaction rejected", str(e))
        threading.Thread(target=work, daemon=True).start()
        self.amount_var.set("")

    def mine(self):
        self.mine_b.config(state="disabled")
        self.status.set("Mining...")

        def work():
            try:
                block = self.node.mine_block()
                if block:
                    self._ui(self.status.set, f"Mined block #{block.index}")
            except Exception as e:
                self._ui(self._log, f"[ERROR] {e}")
            finally:
                self._ui(lambda: self.mine_b.config(state="normal" if self.node else "disabled"))
        threading.Thread(target=work, daemon=True).start()

    def _close(self):
        if self.node:
            self.node.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
