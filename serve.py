#!/usr/bin/env python3
"""Local dashboard server for 4-week T-bill auctions.

Binds to 127.0.0.1 only. Does not call a brokerage and does not place orders.
Broker keys, if saved, stay in keys.json and are never printed.
"""

import json
import sys
import os
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import roll

ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "dashboard.html"
KEYS_PATH = ROOT / "keys.json"
HOST = "127.0.0.1"
PORT = 8765
MAX_BODY = 4096

BROKERS = {"schwab", "ibkr"}
MODES = {"paper", "live"}
STATUS_SAVED = "keys saved locally, trading not wired"
STATUS_EMPTY = "no keys saved, trading not wired"
NOTE = (
    "TreasuryDirect has no public trading API. Keys are stored only on this "
    "machine for a later broker adapter (Schwab or IBKR). This page does not "
    "send an order."
)


def mask_secret(value):
    text = "" if value is None else str(value)
    if not text:
        return ""
    if len(text) < 8:
        return "•" * len(text)
    return text[:2] + ("•" * (len(text) - 4)) + text[-2:]


def _read_keys():
    if not KEYS_PATH.is_file():
        return None
    try:
        data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def settings_public():
    saved = _read_keys() or {}
    key = saved.get("api_key") or ""
    secret = saved.get("api_secret") or ""
    has_keys = bool(key) and bool(secret)
    return {
        "saved": has_keys,
        "broker": saved.get("broker") if saved.get("broker") in BROKERS else None,
        "mode": saved.get("mode") if saved.get("mode") in MODES else "paper",
        "api_key_masked": mask_secret(key) if has_keys else "",
        "api_secret_masked": mask_secret(secret) if has_keys else "",
        "connection_status": STATUS_SAVED if has_keys else STATUS_EMPTY,
        "note": NOTE,
    }


def _write_keys(payload):
    broker = str(payload.get("broker") or "").strip().lower()
    mode = str(payload.get("mode") or "").strip().lower()
    api_key = payload.get("api_key")
    api_secret = payload.get("api_secret")
    if broker not in BROKERS:
        raise ValueError("broker must be schwab or ibkr")
    if mode not in MODES:
        raise ValueError("mode must be paper or live")
    if not isinstance(api_key, str) or not isinstance(api_secret, str):
        raise ValueError("api_key and api_secret must be strings")
    api_key = api_key.strip()
    api_secret = api_secret.strip()
    if not api_key or not api_secret:
        raise ValueError("api_key and api_secret are required")
    if len(api_key) > 200 or len(api_secret) > 200:
        raise ValueError("key or secret is too long")
    if any(ch in api_key + api_secret for ch in "\n\r\x00"):
        raise ValueError("key or secret contains a forbidden character")
    body = {
        "broker": broker,
        "mode": mode,
        "api_key": api_key,
        "api_secret": api_secret,
    }
    tmp = KEYS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(KEYS_PATH)
    os.chmod(KEYS_PATH, 0o600)


def auctions_public(notional, rolls, limit=12):
    source_url, rows = roll.fetch_auctions(limit)
    described = [roll.describe_auction(row, notional) for row in rows]
    latest = roll.project_latest(rows[0], notional, rolls)
    return {
        "source": source_url,
        "notional": format(notional, "f"),
        "rolls": rolls,
        "latest": latest,
        "auctions": described,
        "disclaimer": (
            "Figures come from the Treasury Fiscal Data auctions API. "
            "The roll projection assumes the latest price stays constant. "
            "This is not a trade."
        ),
    }


def _parse_notional(raw):
    try:
        notional = Decimal(raw)
    except (InvalidOperation, TypeError):
        raise ValueError("notional must be a number")
    if notional <= 0 or notional > Decimal("1000000000"):
        raise ValueError("notional must be between 0 and 1e9")
    return notional


def _parse_rolls(raw):
    try:
        rolls = int(raw)
    except (TypeError, ValueError):
        raise ValueError("rolls must be an integer")
    if rolls < 1 or rolls > 520:
        raise ValueError("rolls must be from 1 to 520")
    return rolls


class Handler(BaseHTTPRequestHandler):
    server_version = "tbill-roll"

    def log_message(self, fmt, *args):
        # Request line only. Never include a body, which could hold a secret.
        super().log_message(fmt, *args)

    def _send(self, status, body, content_type, extra_headers=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for key, value in extra_headers:
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, payload):
        self._send(
            status,
            json.dumps(payload),
            "application/json; charset=utf-8",
        )

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > MAX_BODY:
            raise ValueError("body must be under 4KB")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("body must be JSON")
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
        return payload

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/dashboard.html"):
            if not DASHBOARD.is_file():
                self._json(500, {"error": "dashboard.html is missing"})
                return
            self._send(
                200,
                DASHBOARD.read_bytes(),
                "text/html; charset=utf-8",
            )
            return
        if parsed.path == "/api/auctions":
            qs = parse_qs(parsed.query)
            try:
                notional = _parse_notional((qs.get("notional") or ["10000"])[0])
                rolls = _parse_rolls((qs.get("rolls") or ["13"])[0])
                limit = _parse_rolls((qs.get("limit") or ["12"])[0])
                if limit > 20:
                    limit = 20
                payload = auctions_public(notional, rolls, limit)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            except Exception as exc:
                self._json(502, {"error": f"treasury lookup failed: {exc}"})
                return
            self._json(200, payload)
            return
        if parsed.path == "/api/settings":
            self._json(200, settings_public())
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/settings":
            self._json(404, {"error": "not found"})
            return
        try:
            payload = self._read_json()
            _write_keys(payload)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        except OSError:
            self._json(500, {"error": "could not store keys locally"})
            return
        self._json(200, settings_public())


def main():
    httpd = None
    bound = None
    for port in range(PORT, PORT + 20):
        try:
            httpd = ThreadingHTTPServer((HOST, port), Handler)
            bound = port
            break
        except OSError:
            continue
    if httpd is None or bound is None:
        raise SystemExit(f"no free localhost port in {PORT}-{PORT + 19}")
    print(f"tbill-roll dashboard at http://{HOST}:{bound}/", flush=True)
    if bound != PORT:
        print(f"Preferred port {PORT} was busy; using {bound}.", flush=True)
    print("Localhost only. No brokerage calls.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopping", flush=True)
    finally:
        httpd.server_close()


def export_static():
    """Write a snapshot into dashboard.html and docs/ for GitHub Pages or file open."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    payload = auctions_public(Decimal("10000"), 13, 12)
    payload["fetched_at"] = datetime.now(ZoneInfo("America/Tijuana")).isoformat(timespec="seconds")
    payload["note"] = (
        "Snapshot of the public Fiscal Data auctions API. "
        "Embedded so the page still renders if a live request fails. Not a trade."
    )
    raw = json.dumps(payload, indent=2).replace("<", "\\u003c")
    html_path = ROOT / "dashboard.html"
    html = html_path.read_text(encoding="utf-8")
    start = html.find('<script id="tbill-snapshot"')
    end = html.find("</script>", start)
    if start < 0 or end < 0:
        raise SystemExit("dashboard.html is missing the snapshot script")
    close = end + len("</script>")
    html = (
        html[:start]
        + '<script id="tbill-snapshot" type="application/json">\n'
        + raw
        + "\n  </script>"
        + html[close:]
    )
    html_path.write_text(html, encoding="utf-8")
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / ".nojekyll").write_text("", encoding="utf-8")
    (docs / "index.html").write_text(html, encoding="utf-8")
    (docs / "data.json").write_text(raw + "\n", encoding="utf-8")
    latest = payload["latest"]
    print(
        f"snapshot {latest['cusip']} auction {latest['auction_date']} "
        f"investment {latest['investment_rate']}% fetched {payload['fetched_at']}"
    )
    print(f"wrote {html_path}")
    print(f"wrote {docs / 'index.html'}")


if __name__ == "__main__":
    if "--export" in sys.argv:
        try:
            export_static()
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        main()
