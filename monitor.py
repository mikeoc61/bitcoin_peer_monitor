#!/usr/bin/env python3
"""
bitcoin_peer_monitor/monitor.py

Collects bitcoind status and generates a daily human-readable report and a JSON summary.

Features:
- Uses /usr/local/bin/bitcoin-cli to gather network/mempool/peer/block stats.
- Persists total nettotals (bytes in/out) in ~/.bitcoin_peer_monitor/state.json to compute daily deltas.
- Writes human-readable report and JSON summary to ~/bitcoin_peer_monitor/reports/YYYY-MM-DD.txt|.json

Run as the user that can run bitcoin-cli (cookie-based auth):
  /home/mikeoc/bitcoin_peer_monitor/monitor.py

Command-line options (minimal for now): none — configure via constants at top or env vars.
"""

import os
import sys
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

# Config
BITCOIN_CLI = "/usr/local/bin/bitcoin-cli"
DATADIR = "/media/mikeoc/T72GB/Bitcoin"
WORKDIR = Path.home() / "bitcoin_peer_monitor"
REPORTS_DIR = WORKDIR / "reports"
STATE_FILE = WORKDIR / "state.json"
TOP_N_PEERS = 5

# Helpers

def run_cmd(cmd):
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return out
    except subprocess.CalledProcessError as e:
        return e.output


def run_cli_json(cmd_args):
    cmd = [BITCOIN_CLI] + cmd_args
    out = run_cmd(cmd)
    try:
        return json.loads(out)
    except Exception:
        return None


def ensure_dirs():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    WORKDIR.mkdir(parents=True, exist_ok=True)


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def collect_bitcoind_stats():
    stats = {}
    # networkinfo
    networkinfo = run_cli_json(["getnetworkinfo"]) or {}
    stats['networkinfo'] = networkinfo
    # net totals
    nettotals = run_cli_json(["getnettotals"]) or {}
    stats['nettotals'] = nettotals
    # mempool
    mempool = run_cli_json(["getmempoolinfo"]) or {}
    stats['mempool'] = mempool
    # blockcount
    blockcount = run_cli_json(["getblockcount"])
    stats['blockcount'] = blockcount if blockcount is not None else None
    # peer info
    peers = run_cli_json(["getpeerinfo"]) or []
    stats['peer_count'] = len(peers)

    # compute top peers by bytes (sent+recv)
    peer_summaries = []
    for p in peers:
        sent = p.get('bytessent', 0)
        recv = p.get('bytesrecv', 0)
        total = sent + recv
        peer_summaries.append({
            'id': p.get('id'),
            'addr': p.get('addr'),
            'inbound': p.get('inbound'),
            'bytessent': sent,
            'bytesrecv': recv,
            'total_bytes': total,
            'pingtime': p.get('pingtime'),
            'conntime': p.get('conntime')
        })
    peer_summaries.sort(key=lambda x: x['total_bytes'], reverse=True)
    stats['top_peers'] = peer_summaries[:TOP_N_PEERS]

    return stats


def compute_deltas(state, nettotals):
    # nettotals expected to have 'totalbytesrecv' and 'totalbytessent'
    prev = state.get('nettotals', {})
    now = {}
    now['totalbytesrecv'] = nettotals.get('totalbytesrecv') if isinstance(nettotals, dict) else None
    now['totalbytessent'] = nettotals.get('totalbytessent') if isinstance(nettotals, dict) else None
    delta = {}
    if prev and now.get('totalbytesrecv') is not None and prev.get('totalbytesrecv') is not None:
        delta['bytesrecv'] = now['totalbytesrecv'] - prev.get('totalbytesrecv', 0)
    else:
        delta['bytesrecv'] = None
    if prev and now.get('totalbytessent') is not None and prev.get('totalbytessent') is not None:
        delta['bytessent'] = now['totalbytessent'] - prev.get('totalbytessent', 0)
    else:
        delta['bytessent'] = None
    return now, delta


def get_datadir_size(path):
    try:
        # du -sb for bytes
        out = run_cmd(['du', '-sb', path])
        size = int(out.split()[0])
        return size
    except Exception:
        return None


def write_reports(report_text, summary_json):
    date = datetime.now().strftime('%Y-%m-%d')
    txt_path = REPORTS_DIR / f"{date}.txt"
    json_path = REPORTS_DIR / f"{date}.json"
    txt_path.write_text(report_text)
    json_path.write_text(json.dumps(summary_json, indent=2))
    return txt_path, json_path


def human_bytes(n):
    if n is None:
        return 'N/A'
    try:
        n = float(n)
    except Exception:
        return str(n)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if n < 1024.0 or unit == 'TB':
            if unit == 'B':
                return f"{int(n)} {unit}"
            else:
                return f"{n:.2f} {unit}"
        n /= 1024.0


def human_readable_report(stats, delta, datadir_size):
    lines = []
    lines.append(f"Bitcoin Peer Monitor report for {datetime.now().isoformat()}")
    lines.append("")
    lines.append("== Network ==")
    ni = stats.get('networkinfo') or {}
    lines.append(f"Connections: {stats.get('peer_count')}\n")
    lines.append("== Net totals ==")
    nt = stats.get('nettotals') or {}
    lines.append(f"Total bytes recv: {human_bytes(nt.get('totalbytesrecv'))}, Total bytes sent: {human_bytes(nt.get('totalbytessent'))}")
    if delta:
        lines.append(f"Delta since last run: bytes recv: {human_bytes(delta.get('bytesrecv'))}, bytes sent: {human_bytes(delta.get('bytessent'))}")
    lines.append("")
    lines.append("== Mempool ==")
    mp = stats.get('mempool') or {}
    lines.append(f"Size: {mp.get('size')}, Bytes: {human_bytes(mp.get('bytes'))}")
    lines.append("")
    lines.append("== Blockchain ==")
    lines.append(f"Block count: {stats.get('blockcount')}")
    if datadir_size is not None:
        lines.append(f"Datadir size: {human_bytes(datadir_size)}")
    lines.append("")
    lines.append("== Top peers ==")
    for p in stats.get('top_peers', []):
        # format ping in ms
        ping = p.get('pingtime')
        if ping is None:
            ping_s = 'N/A'
        else:
            try:
                ping_s = f"{float(ping)*1000:.1f} ms"
            except Exception:
                ping_s = str(ping)
        # format connection time as duration hh:mm:ss; conntime may be epoch or seconds connected
        conntime = p.get('conntime')
        if conntime is None:
            conntime_s = 'N/A'
        else:
            try:
                now_ts = int(datetime.now().timestamp())
                c_int = int(conntime)
                if c_int > 1_000_000_000:
                    dur = now_ts - c_int
                else:
                    dur = c_int
                h = dur // 3600
                m = (dur % 3600) // 60
                s = dur % 60
                conntime_s = f"{h:02d}:{m:02d}:{s:02d}"
            except Exception:
                conntime_s = str(conntime)
        lines.append(f"{p['addr']} id={p['id']} total={human_bytes(p['total_bytes'])} sent={human_bytes(p['bytessent'])} recv={human_bytes(p['bytesrecv'])} ping={ping_s} conntime={conntime_s}")
    lines.append("")
    return "\n".join(lines)


def main():
    ensure_dirs()
    state = load_state()

    # collect bitcoind stats
    stats = collect_bitcoind_stats()

    # compute deltas for nettotals
    now_net, delta = compute_deltas(state, stats.get('nettotals') or {})

    # datadir size
    datadir_size = get_datadir_size(DATADIR)

    # prepare summary JSON
    summary = {
        'timestamp': datetime.now().isoformat(),
        'stats': stats,
        'delta': delta,
        'datadir_size_bytes': datadir_size
    }

    # human report
    report_text = human_readable_report(stats, delta, datadir_size)

    # write reports
    txt_path, json_path = write_reports(report_text, summary)

    # persist nettotals for next run (only totals, per choice)
    state['nettotals'] = now_net
    state['last_run'] = datetime.now().isoformat()
    save_state(state)

    # print the human report to stdout so wrapper captures it
    print(report_text)

if __name__ == '__main__':
    main()
