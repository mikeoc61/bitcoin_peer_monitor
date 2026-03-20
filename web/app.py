"""
Bitcoin Peer Monitor - Web Service
FastAPI backend serving peer data as HTML fragments via HTMX.

Run with:
    pip install fastapi uvicorn requests
    uvicorn app:app --host 0.0.0.0 --port 8000

Access at http://<your-node-ip>:8000
"""

import json
import subprocess
import requests
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

# Service flags (bitmask values)
NODE_NETWORK         = 1 << 0
NODE_WITNESS         = 1 << 3
NODE_COMPACT_FILTERS = 1 << 6
NODE_NETWORK_LIMITED = 1 << 10

# Geolocation cache: ip -> {flag, city, country}
_geo_cache = {}


def get_peer_info():
    result = subprocess.run(
        ["bitcoin-cli", "getpeerinfo"],
        capture_output=True,
        text=True
    )
    return json.loads(result.stdout)


def lookup_geo(ip):
    """Look up geolocation for an IP using ip-api.com (free, no key required).
    Results are cached in memory for the lifetime of the process."""
    # Strip port if present
    clean_ip = ip.split(":")[0].strip("[]")

    # Return cached result if available
    if clean_ip in _geo_cache:
        return _geo_cache[clean_ip]

    # Skip private/local IPs
    private_prefixes = ("10.", "172.", "192.168.", "127.", "::1", "fc", "fd")
    if any(clean_ip.startswith(p) for p in private_prefixes):
        result = {"flag": "🏠", "city": "Local", "country": ""}
        _geo_cache[clean_ip] = result
        return result

    try:
        resp = requests.get(
            f"http://ip-api.com/json/{clean_ip}?fields=status,country,countryCode,city",
            timeout=3
        )
        data = resp.json()
        if data.get("status") == "success":
            cc = data.get("countryCode", "")
            flag = country_code_to_flag(cc)
            city = data.get("city", "")
            country = data.get("country", "")
            result = {"flag": flag, "city": city, "country": country}
        else:
            result = {"flag": "🌐", "city": "Unknown", "country": ""}
    except Exception:
        result = {"flag": "🌐", "city": "Unknown", "country": ""}

    _geo_cache[clean_ip] = result
    return result


def country_code_to_flag(cc):
    """Convert a 2-letter country code to a flag emoji."""
    if not cc or len(cc) != 2:
        return "🌐"
    return chr(ord(cc[0]) + 127397) + chr(ord(cc[1]) + 127397)


def decode_services(services_hex):
    services_int = int(services_hex, 16)
    parts = []
    if services_int & NODE_NETWORK:         parts.append("N")
    if services_int & NODE_WITNESS:         parts.append("W")
    if services_int & NODE_COMPACT_FILTERS: parts.append("CF")
    if services_int & NODE_NETWORK_LIMITED: parts.append("NL")
    return parts


def connection_duration(connected_since):
    duration = datetime.now() - datetime.fromtimestamp(connected_since)
    days = duration.total_seconds() / 86400
    if days > 1:
        return f"{days:.1f}d"
    hours, rem = divmod(duration.total_seconds(), 3600)
    mins, secs = divmod(rem, 60)
    return f"{int(hours):02}:{int(mins):02}:{int(secs):02}"


def format_ping(pingtime):
    try:
        ms = int(pingtime * 1000)
        if ms < 100:
            cls = "ping-good"
        elif ms < 300:
            cls = "ping-mid"
        else:
            cls = "ping-bad"
        return f'<span class="{cls}">{ms} ms</span>'
    except (TypeError, ValueError):
        return '<span class="ping-bad">N/A</span>'


def truncate(s, n):
    return s[:n] + "…" if len(s) > n else s


def build_rows(peers):
    rows = []
    for peer in peers:
        services = decode_services(peer.get("services", "0"))
        missing_network = "N" not in services
        badge_html = " ".join(
            f'<span class="badge badge-{"n" if s == "N" else "w" if s == "W" else "cf" if s == "CF" else "nl"}">{s}</span>'
            for s in services
        ) or '<span class="badge badge-none">—</span>'

        ping_html = format_ping(peer.get("pingtime"))
        duration = connection_duration(peer.get("conntime", 0))
        inbound = peer.get("inbound", False)
        relay = peer.get("relaytxes", False)

        sent_mb = peer.get("bytessent", 0) / 1_048_576
        recv_mb = peer.get("bytesrecv", 0) / 1_048_576

        subver = truncate(peer.get("subver", "Unknown").strip("/"), 22)
        row_class = "row-warn" if missing_network else ""

        # Geolocation
        addr = peer.get("addr", "")
        geo = lookup_geo(addr)
        geo_html = f'<span class="geo-flag">{geo["flag"]}</span> <span class="geo-city">{geo["city"]}</span>'
        if geo["country"] and geo["country"] != geo["city"]:
            geo_html += f' <span class="geo-country">{geo["country"]}</span>'

        # Data attributes for client-side sorting
        ping_ms = -1
        try:
            ping_ms = int(peer.get("pingtime", -1) * 1000)
        except (TypeError, ValueError):
            pass

        rows.append(f"""
        <tr class="{row_class}"
            data-id="{peer['id']}"
            data-duration="{peer.get('conntime', 0)}"
            data-ping="{ping_ms}"
            data-sent="{peer.get('bytessent', 0)}"
            data-recv="{peer.get('bytesrecv', 0)}"
            data-proto="{peer.get('version', 0)}"
            data-geo="{geo['country']} {geo['city']}"
            data-inbound="{'1' if inbound else '0'}"
            data-relay="{'1' if relay else '0'}">
            <td class="td-id">{peer['id']}</td>
            <td class="td-dur">{duration}</td>
            <td class="td-svc">{badge_html}</td>
            <td class="td-ver">{subver}</td>
            <td class="td-proto">{peer.get('version', '?')}</td>
            <td class="td-num">{sent_mb:.2f} MB</td>
            <td class="td-num">{recv_mb:.2f} MB</td>
            <td class="td-ping">{ping_html}</td>
            <td class="td-bool">{'✓' if inbound else ''}</td>
            <td class="td-bool">{'✓' if relay else ''}</td>
            <td class="td-geo">{geo_html}</td>
        </tr>""")
    return "\n".join(rows)


@app.get("/peers", response_class=HTMLResponse)
async def peers_fragment():
    peers = get_peer_info()
    rows = build_rows(peers)
    now = datetime.now().strftime("%H:%M:%S")
    return f"""
    <div id="meta-bar">
        <span class="peer-count">{len(peers)} peers connected</span>
        <span class="last-update">Last update: {now}</span>
    </div>
    <div class="table-wrap">
    <table id="peer-table">
        <thead>
            <tr>
                <th data-col="id">ID</th>
                <th data-col="duration">Connected</th>
                <th>Services</th>
                <th>Client</th>
                <th data-col="proto">Proto</th>
                <th data-col="sent">Sent</th>
                <th data-col="recv">Received</th>
                <th data-col="ping">Ping</th>
                <th data-col="inbound">Inbound</th>
                <th data-col="relay">Relay</th>
                <th data-col="geo">Location</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
    </div>
    """


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("templates/index.html") as f:
        return f.read()
