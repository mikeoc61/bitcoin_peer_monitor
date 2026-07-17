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
from ipaddress import ip_address, ip_network
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

# --- Configuration ---------------------------------------------------------

# Timeout (seconds) for the bitcoin-cli subprocess call.
# Bump if you see "bitcoin-cli timed out" errors during heavy node activity
# (validation, mempool processing, reorg recovery, etc.).
BITCOIN_CLI_TIMEOUT = 15

# Timeout (seconds) for outbound geolocation HTTP lookups.
GEO_LOOKUP_TIMEOUT = 3

# Service flags (bitmask values)
NODE_NETWORK         = 1 << 0
NODE_WITNESS         = 1 << 3
NODE_COMPACT_FILTERS = 1 << 6
NODE_NETWORK_LIMITED = 1 << 10

# --- Cloud provider IP detection -------------------------------------------
# ip-api.com's free tier has poor coverage for IPv6 cloud allocations
# (returns "Unknown"). Pre-classify common cloud ranges locally before
# hitting the API. Extend as needed.

CLOUD_PREFIXES = [
    # AWS
    ("2600:1f00::/24",  "AWS",          "☁️"),
    ("2406:da00::/24",  "AWS",          "☁️"),
    ("2a05:d000::/24",  "AWS",          "☁️"),
    # Google Cloud
    ("2600:1900::/28",  "Google Cloud", "☁️"),
    ("2001:4860::/32",  "Google",       "☁️"),
    # Azure
    ("2603:1000::/24",  "Azure",        "☁️"),
    ("2a01:111::/32",   "Azure",        "☁️"),
    # Cloudflare
    ("2606:4700::/32",  "Cloudflare",   "☁️"),
    ("2a06:98c0::/29",  "Cloudflare",   "☁️"),
    # Hetzner
    ("2a01:4f8::/29",   "Hetzner",      "🇩🇪"),
    ("2a01:4f9::/29",   "Hetzner",      "🇩🇪"),
    # OVH
    ("2001:41d0::/32",  "OVH",          "🇫🇷"),
    # DigitalOcean
    ("2604:a880::/32",  "DigitalOcean", "☁️"),
    ("2a03:b0c0::/32",  "DigitalOcean", "☁️"),
    # Linode / Akamai
    ("2600:3c00::/24",  "Linode",       "☁️"),
    ("2400:8900::/32",  "Linode",       "☁️"),
    # Vultr
    ("2001:19f0::/29",  "Vultr",        "☁️"),
    ("2a05:f480::/29",  "Vultr",        "☁️"),
]

# Pre-compile networks at module load for fast lookups.
_CLOUD_NETWORKS = []
for _prefix, _name, _flag in CLOUD_PREFIXES:
    try:
        _CLOUD_NETWORKS.append((ip_network(_prefix), _name, _flag))
    except ValueError:
        pass

# Geolocation cache: ip -> {flag, city, country}
_geo_cache = {}


def get_peer_info():
    """Fetch peer info. Returns (peers, error_message)."""
    try:
        result = subprocess.run(
#            ["bitcoin-cli", "-datadir=/media/mikeoc/T72GB/Bitcoin", "getpeerinfo"],
            ["bitcoin-cli", "getpeerinfo"],
            capture_output=True,
            text=True,
            timeout=BITCOIN_CLI_TIMEOUT
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            msg = stderr if stderr else "bitcoin-cli returned a non-zero exit code"
            return [], msg
        if not result.stdout.strip():
            return [], "No response from bitcoind — node may be starting up"
        return json.loads(result.stdout), None
    except subprocess.TimeoutExpired:
        return [], f"bitcoin-cli timed out after {BITCOIN_CLI_TIMEOUT}s — node may be busy"
    except json.JSONDecodeError:
        return [], "Could not parse response from bitcoind"
    except Exception as e:
        return [], f"Unexpected error: {e}"


def extract_ip(addr):
    """Extract IP/hostname from a bitcoind 'addr' string.

    Handles:
        IPv4 with port:   1.2.3.4:8333         -> 1.2.3.4
        IPv6 with port:   [2001:db8::1]:8333   -> 2001:db8::1
        Tor / I2P:        foo.onion:8333       -> foo.onion
        Bare address:     2001:db8::1          -> 2001:db8::1
    """
    if not addr:
        return ""
    addr = addr.strip()
    # IPv6 bracketed form
    if addr.startswith("["):
        end = addr.find("]")
        if end != -1:
            return addr[1:end]
    # IPv4 / .onion / .i2p with a single trailing :port
    if addr.count(":") == 1:
        return addr.rsplit(":", 1)[0]
    # Bare hostname or address (no port)
    return addr


def lookup_cloud(ip_str):
    """Return cloud provider info if IP is in a known cloud range, else None."""
    try:
        ip = ip_address(ip_str)
    except ValueError:
        return None
    for network, name, flag in _CLOUD_NETWORKS:
        if ip.version == network.version and ip in network:
            return {"flag": flag, "city": name, "country": "Cloud"}
    return None


def lookup_geo(addr):
    """Look up geolocation for a bitcoind peer address.

    Resolution order:
        1. In-memory cache
        2. Anonymity network suffixes (.onion / .i2p)
        3. Private / loopback / link-local (via stdlib ipaddress)
        4. Known cloud provider prefix table
        5. ip-api.com HTTP lookup
    Results are cached for the lifetime of the process.
    """
    clean_ip = extract_ip(addr)

    if clean_ip in _geo_cache:
        return _geo_cache[clean_ip]

    # Anonymity networks
    if clean_ip.endswith(".onion"):
        result = {"flag": "🧅", "city": "Tor", "country": ""}
        _geo_cache[clean_ip] = result
        return result
    if clean_ip.endswith(".i2p"):
        result = {"flag": "🌐", "city": "I2P", "country": ""}
        _geo_cache[clean_ip] = result
        return result

    # Private / loopback / link-local (RFC1918, ULA fc00::/7, fe80::/10, 127/8, ::1)
    try:
        ip_obj = ip_address(clean_ip)
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local:
            result = {"flag": "🏠", "city": "Local", "country": ""}
            _geo_cache[clean_ip] = result
            return result
    except ValueError:
        pass  # not a parseable IP — fall through to ip-api anyway

    # Known cloud provider (handles AWS/GCP/Azure IPv6 etc. that ip-api can't resolve)
    cloud = lookup_cloud(clean_ip)
    if cloud:
        _geo_cache[clean_ip] = cloud
        return cloud

    # Fall back to ip-api.com
    try:
        resp = requests.get(
            f"http://ip-api.com/json/{clean_ip}?fields=status,country,countryCode,city",
            timeout=GEO_LOOKUP_TIMEOUT
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

        # Full subver — no server-side truncation. CSS in templates/index.html
        # handles overflow (text-overflow: ellipsis) and full text appears on
        # hover via the title attribute below.
        subver = peer.get("subver", "Unknown").strip("/")
        subver_attr = subver.replace('"', '&quot;')

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
            <td class="td-ver" title="{subver_attr}">{subver}</td>
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
    peers, error = get_peer_info()
    now = datetime.now().strftime("%H:%M:%S")

    error_banner = ""
    if error:
        error_banner = f"""
        <div id="node-error-banner">
            <span class="error-icon">⚠</span>
            <span class="error-msg">Node unreachable — {error}</span>
            <span class="error-time">Last attempt: {now}</span>
        </div>"""

    rows = build_rows(peers)
    peer_count = f"{len(peers)} peers connected" if not error else "Node offline"

    return f"""
    {error_banner}
    <div id="meta-bar">
        <span class="peer-count {'peer-count-offline' if error else ''}">{peer_count}</span>
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


@app.get("/tts/{filename}")
async def tts_file(filename: str):
    # Serve announcement clips from a local directory.
    # Expected path: /home/mikeoc/bitcoin_peer_monitor/web/static/tts/<filename>
    from fastapi.responses import FileResponse
    base = Path("static/tts")
    file_path = (base / filename).resolve()
    if base.resolve() not in file_path.parents and file_path != base.resolve():
        return HTMLResponse("invalid path", status_code=400)
    if not file_path.exists():
        return HTMLResponse("not found", status_code=404)
    return FileResponse(str(file_path))
