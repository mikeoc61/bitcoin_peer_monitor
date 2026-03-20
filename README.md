# Bitcoin Peer Monitor

Tools for monitoring peer connections on a Bitcoin Core node. Two versions are available: a terminal-based CLI script and a web service accessible via browser.

---

## Versions

### 1. CLI Version (`peer_monitor.py`)

A terminal script that queries `bitcoin-cli getpeerinfo` and renders a live-updating table in the console using the [Rich](https://github.com/Textualize/rich) library. Refreshes every 15 seconds. Best for quick local inspection directly on the node.

### 2. Web Version (`web/`)

A FastAPI + HTMX web service that serves the same peer data as a browser-based dashboard. Accessible from any device on your local network. Auto-refreshes every 15 seconds without a page reload.

---

## Requirements

Both versions require a running Bitcoin Core node with `bitcoin-cli` in your `PATH`.

### CLI
```
pip install rich
```

### Web
```
pip install fastapi uvicorn
```

---

## Usage

### CLI

```bash
python peer_monitor.py
```

Press `Ctrl+C` to exit.

### Web

```bash
cd web
uvicorn app:app --host 0.0.0.0 --port 8000
```

Then open `http://<your-node-ip>:8000` in a browser on your local network.

To allow the port through the firewall (Ubuntu/Debian):
```bash
sudo ufw allow 8000/tcp
```

---

## Project Structure

```
bitcoin-peer-monitor/
├── README.md
├── peer_monitor.py        # CLI version
└── web/
    ├── app.py             # FastAPI backend
    └── templates/
        └── index.html     # HTMX frontend
```

---

## Features

| Feature | CLI | Web |
|---|---|---|
| Peer ID, version, protocol | ✓ | ✓ |
| Connection duration | ✓ | ✓ |
| Service flags (N, W, CF, NL) | ✓ | ✓ (color-coded badges) |
| Bytes sent / received | ✓ | ✓ |
| Ping time | ✓ | ✓ (color-coded) |
| Inbound / relay flags | ✓ | ✓ |
| Highlights missing NODE_NETWORK | ✓ (yellow row) | ✓ (amber tint + ⚠) |
| Remote browser access | — | ✓ |
| Auto-refresh | ✓ | ✓ |

---

## Service Flags

| Badge | Flag | Meaning |
|---|---|---|
| N | NODE_NETWORK | Full node, serves block history |
| W | NODE_WITNESS | Supports SegWit |
| CF | NODE_COMPACT_FILTERS | Supports BIP 157/158 compact filters |
| NL | NODE_NETWORK_LIMITED | Pruned node, limited block history |

Peers missing the **N** flag are highlighted as a heads-up — they cannot serve the full block history.

---

## Author

Michael O'Connor
