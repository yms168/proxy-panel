#!/usr/bin/env python3
"""
Multi-Source Proxy Aggregator v2.0
===================================
Aggregates proxies from VPN Gate, ProxyScrape, OpenProxyList and more.
Provides REST API for the Unified Proxy Manager panel.

Features:
  - Multi-source proxy aggregation (4+ sources)
  - Automatic health checking with latency measurement
  - IP classification (residential vs datacenter via ip-api.com)
  - Persistent proxy pool storage
  - RESTful API for proxy listing, filtering, and best-node selection
  - Background fetch cycles (every 15 minutes)

Endpoints:
  GET  /api/status         - Pool statistics
  GET  /api/proxies        - List proxies with filters
  GET  /api/proxies/best   - Get best proxies (residential-first, low latency)
  POST /api/fetch          - Trigger a full fetch cycle
  POST /api/test/{id}      - Re-test a specific proxy
  DELETE /api/proxies/{id} - Remove a proxy
  GET  /api/health         - Health check

Author: https://blog.lingdian168.online/
License: MIT
"""
import asyncio
import json
import logging
import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import requests

# --- Config ---
BASE_DIR = Path(__file__).parent
PROXY_STORE = BASE_DIR / "proxies.json"
LOG_FILE = BASE_DIR / "server.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger("proxy-pool")


# ============================================================
# Data Models
# ============================================================

class Proxy(BaseModel):
    id: str
    ip: str
    port: int
    protocol: str  # socks5, socks4, http, https
    country: str = "Unknown"
    region: str = ""
    isp: str = ""
    ip_type: str = "datacenter"  # residential | datacenter | unknown
    latency_ms: int = 9999
    source: str = "unknown"
    last_check: str = ""
    added_at: str = ""
    alive: bool = True
    score: int = 0

class PoolStatus(BaseModel):
    total: int
    alive: int
    residential: int
    datacenter: int
    avg_latency: float
    last_fetch: str
    sources: dict


# ============================================================
# Proxy Pool Manager
# ============================================================

class ProxyPool:
    """Thread-safe in-memory proxy pool with JSON persistence."""

    def __init__(self):
        self.proxies: dict[str, Proxy] = {}
        self.lock = threading.Lock()
        self.last_fetch: dict[str, str] = {}
        self.load()

    def load(self):
        if PROXY_STORE.exists():
            try:
                with open(PROXY_STORE) as f:
                    data = json.load(f)
                for p in data.get("proxies", []):
                    self.proxies[p["id"]] = Proxy(**p)
                self.last_fetch = data.get("last_fetch", {})
                log.info(f"Loaded {len(self.proxies)} proxies from store")
            except Exception as e:
                log.error(f"Failed to load proxies: {e}")

    def save(self):
        with self.lock:
            data = {
                "proxies": [p.model_dump() for p in self.proxies.values()],
                "last_fetch": self.last_fetch,
                "saved_at": datetime.utcnow().isoformat()
            }
        with open(PROXY_STORE, 'w') as f:
            json.dump(data, f, indent=2)

    def add(self, proxy: Proxy):
        with self.lock:
            if proxy.id in self.proxies:
                existing = self.proxies[proxy.id]
                existing.latency_ms = proxy.latency_ms
                existing.last_check = proxy.last_check
                existing.alive = proxy.alive
            else:
                self.proxies[proxy.id] = proxy

    def remove(self, proxy_id: str):
        with self.lock:
            self.proxies.pop(proxy_id, None)

    def get_all(self, filters: dict = None) -> list[Proxy]:
        with self.lock:
            proxies = list(self.proxies.values())
        if filters:
            if filters.get("country"):
                proxies = [p for p in proxies if p.country.upper() == filters["country"].upper()]
            if filters.get("ip_type"):
                proxies = [p for p in proxies if p.ip_type == filters["ip_type"]]
            if filters.get("protocol"):
                proxies = [p for p in proxies if p.protocol == filters["protocol"]]
            if filters.get("alive_only"):
                proxies = [p for p in proxies if p.alive]
        return proxies

    def get_best(self, prefer_residential: bool = True, count: int = 10) -> list[Proxy]:
        alive = [p for p in self.get_all() if p.alive and p.latency_ms < 5000]
        if prefer_residential:
            alive.sort(key=lambda p: (0 if p.ip_type == "residential" else 1, p.latency_ms))
        else:
            alive.sort(key=lambda p: p.latency_ms)
        return alive[:count]

    def get_status(self) -> PoolStatus:
        all_p = list(self.proxies.values())
        alive = [p for p in all_p if p.alive]
        residential = [p for p in all_p if p.ip_type == "residential" and p.alive]
        avg_lat = sum(p.latency_ms for p in alive) / len(alive) if alive else 0

        last_fetch_str = "never"
        if self.last_fetch:
            last_fetch_str = max(self.last_fetch.values())

        return PoolStatus(
            total=len(all_p),
            alive=len(alive),
            residential=len(residential),
            datacenter=len([p for p in alive if p.ip_type != "residential"]),
            avg_latency=round(avg_lat, 1),
            last_fetch=last_fetch_str,
            sources=self.last_fetch
        )


# Global pool instance
pool = ProxyPool()


# ============================================================
# Proxy Testers
# ============================================================

def test_socks5(ip: str, port: int, timeout: int = 5) -> tuple:
    """Test SOCKS5 proxy connectivity. Returns (alive, latency_ms)."""
    try:
        import socket
        start = time.time()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        s.send(b'\x05\x01\x00')  # SOCKS5 handshake: v5, 1 auth method, no auth
        resp = s.recv(2)
        if resp == b'\x05\x00':
            latency = int((time.time() - start) * 1000)
            s.close()
            return True, latency
        s.close()
    except:
        pass
    return False, 9999


def test_http(ip: str, port: int, timeout: int = 5) -> tuple:
    """Test HTTP proxy connectivity. Returns (alive, latency_ms)."""
    try:
        start = time.time()
        proxies = {"http": f"http://{ip}:{port}", "https": f"http://{ip}:{port}"}
        r = requests.get("http://httpbin.org/ip", proxies=proxies, timeout=timeout)
        latency = int((time.time() - start) * 1000)
        return r.status_code == 200, latency
    except:
        return False, 9999


def classify_ip(ip: str) -> tuple:
    """
    Classify IP using ip-api.com free API (45 req/min limit).
    Returns (country, region, isp, ip_type).
    """
    try:
        r = requests.get(
            f"http://ip-api.com/json/{ip}?fields=country,regionName,isp,proxy,hosting",
            timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            country = data.get("country", "Unknown")
            region = data.get("regionName", "")
            isp = data.get("isp", "")
            # hosting=True -> datacenter; proxy=True and not hosting -> likely residential
            if data.get("hosting"):
                ip_type = "datacenter"
            elif data.get("proxy"):
                ip_type = "residential"
            else:
                ip_type = "datacenter"
            return country, region, isp, ip_type
    except:
        pass
    return "Unknown", "", "", "unknown"


# ============================================================
# Proxy Fetchers (Multi-Source)
# ============================================================

def fetch_vpngate() -> list[dict]:
    """Fetch VPN servers from VPN Gate (University of Tsukuba project)."""
    results = []
    try:
        r = requests.get("http://www.vpngate.net/api/iphone/", timeout=30)
        if r.status_code == 200:
            lines = r.text.strip().split('\r\n')
            for line in lines[2:]:  # Skip CSV header
                if not line.strip() or line.startswith('*'):
                    continue
                parts = line.split(',')
                if len(parts) >= 15:
                    try:
                        host = parts[1].strip()
                        country = parts[6].strip()
                        if host:
                            results.append({
                                "ip": host, "port": 0, "protocol": "openvpn",
                                "country": country, "source": "vpngate"
                            })
                    except (IndexError, ValueError):
                        continue
        log.info(f"VPN Gate: fetched {len(results)} entries")
    except Exception as e:
        log.error(f"VPN Gate error: {e}")
    return results


def fetch_proxyscrape(protocol: str = "socks5") -> list[dict]:
    """Fetch proxies from ProxyScrape v4 API."""
    results = []
    try:
        url = (
            f"https://api.proxyscrape.com/v4/free-proxy-list/get"
            f"?request=display_proxies&protocol={protocol}"
            f"&proxy_format=protocolipport&format=json&timeout=20000&limit=100"
        )
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            data = r.json()
            for p in data.get("proxies", []):
                results.append({
                    "ip": p["ip"], "port": int(p["port"]),
                    "protocol": p.get("protocol", protocol),
                    "country": p.get("country", "Unknown"),
                    "source": "proxyscrape"
                })
        log.info(f"ProxyScrape ({protocol}): fetched {len(results)}")
    except Exception as e:
        log.error(f"ProxyScrape error: {e}")
    return results


def fetch_openproxylist() -> list[dict]:
    """Fetch from OpenProxyList (free proxy lists)."""
    results = []
    urls = {
        "socks4": "https://api.openproxylist.xyz/socks4.txt",
        "socks5": "https://api.openproxylist.xyz/socks5.txt",
        "http": "https://api.openproxylist.xyz/http.txt",
    }
    for proto, url in urls.items():
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                for line in r.text.strip().split('\n'):
                    parts = line.strip().split(':')
                    if len(parts) == 2:
                        results.append({
                            "ip": parts[0], "port": int(parts[1]),
                            "protocol": proto, "country": "Unknown",
                            "source": "openproxylist"
                        })
        except Exception as e:
            log.error(f"OpenProxyList ({proto}) error: {e}")
    log.info(f"OpenProxyList: fetched {len(results)}")
    return results


# ============================================================
# Main Fetch & Test Cycle
# ============================================================

def run_fetch_cycle():
    """Fetch from all sources, test proxies, update pool."""
    log.info("=" * 40)
    log.info("Starting fetch cycle...")
    all_raw = []

    # Parallel fetch from multiple sources
    threads = [
        threading.Thread(target=lambda: all_raw.extend(fetch_vpngate())),
        threading.Thread(target=lambda: all_raw.extend(fetch_proxyscrape("socks5"))),
        threading.Thread(target=lambda: all_raw.extend(fetch_proxyscrape("http"))),
        threading.Thread(target=lambda: all_raw.extend(fetch_openproxylist())),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    # Deduplicate by (ip, port)
    seen = set()
    unique = []
    for p in all_raw:
        key = (p["ip"], p["port"])
        if key not in seen:
            seen.add(key)
            unique.append(p)

    log.info(f"Unique proxies to test: {len(unique)}")

    # Test each proxy
    tested = 0
    now = datetime.utcnow().isoformat()
    for p in unique:
        ip, port = p["ip"], p["port"]
        protocol = p.get("protocol", "socks5")

        if protocol in ("socks5", "socks4"):
            alive, latency = test_socks5(ip, port, timeout=5)
        elif protocol in ("http", "https"):
            alive, latency = test_http(ip, port, timeout=5)
        else:
            continue

        if alive:
            country, region, isp, ip_type = classify_ip(ip)
            proxy_id = f"{ip}:{port}"
            proxy = Proxy(
                id=proxy_id, ip=ip, port=port, protocol=protocol,
                country=country, region=region, isp=isp, ip_type=ip_type,
                latency_ms=latency, source=p.get("source", "unknown"),
                last_check=now, added_at=now, alive=True,
                score=max(0, 100 - latency // 10)
            )
            pool.add(proxy)
            tested += 1
            log.info(f"  LIVE: {ip}:{port} [{protocol}] {country} {ip_type} {latency}ms")

        # Rate limit for ip-api.com (45 req/min)
        time.sleep(0.15)

    # Update fetch timestamps
    now = datetime.utcnow().isoformat()
    pool.last_fetch = {
        "vpngate": now,
        "proxyscrape": now,
        "openproxylist": now,
    }
    pool.save()
    log.info(f"Fetch cycle complete: {tested} live / {len(unique)} tested")
    log.info("=" * 40)


# ============================================================
# FastAPI Application
# ============================================================

app = FastAPI(
    title="Multi-Source Proxy Aggregator",
    version="2.0.0",
    description="Aggregates and tests proxies from multiple free sources. "
                "Provides REST API for proxy management and smart routing.",
)


@app.get("/api/status")
async def api_status():
    """Get pool statistics and health."""
    return pool.get_status().model_dump()


@app.get("/api/proxies")
async def api_proxies(
    country: Optional[str] = Query(None, description="Filter by country code"),
    ip_type: Optional[str] = Query(None, description="residential | datacenter"),
    protocol: Optional[str] = Query(None, description="socks5 | http | socks4"),
    alive_only: bool = Query(True, description="Only return alive proxies"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List proxies with optional filters."""
    filters = {"alive_only": alive_only}
    if country: filters["country"] = country
    if ip_type: filters["ip_type"] = ip_type
    if protocol: filters["protocol"] = protocol

    proxies = pool.get_all(filters)
    return {
        "total": len(proxies),
        "proxies": [p.model_dump() for p in proxies[offset:offset + limit]]
    }


@app.get("/api/proxies/best")
async def api_best(
    count: int = Query(10, ge=1, le=50),
    prefer_residential: bool = Query(True),
):
    """Get best proxies: residential-first, lowest latency."""
    best = pool.get_best(prefer_residential=prefer_residential, count=count)
    return {"proxies": [p.model_dump() for p in best]}


@app.post("/api/fetch")
async def api_fetch():
    """Trigger a full fetch-and-test cycle (async)."""
    t = threading.Thread(target=run_fetch_cycle, daemon=True)
    t.start()
    return {"status": "fetch_started", "note": "Check /api/status for results"}


@app.post("/api/test/{proxy_id}")
async def api_test_one(proxy_id: str):
    """Re-test a single proxy."""
    p = pool.proxies.get(proxy_id)
    if not p:
        raise HTTPException(404, "Proxy not found")
    if p.protocol in ("socks5", "socks4"):
        alive, latency = test_socks5(p.ip, p.port)
    else:
        alive, latency = test_http(p.ip, p.port)
    p.alive = alive
    p.latency_ms = latency
    p.last_check = datetime.utcnow().isoformat()
    pool.add(p)
    pool.save()
    return {"id": proxy_id, "alive": alive, "latency_ms": latency}


@app.delete("/api/proxies/{proxy_id}")
async def api_delete(proxy_id: str):
    """Remove a proxy from the pool."""
    pool.remove(proxy_id)
    pool.save()
    return {"status": "deleted"}


@app.get("/api/health")
async def api_health():
    return {"status": "ok"}


# ============================================================
# Background Scheduler
# ============================================================

def background_loop():
    """Periodic fetch cycle every 15 minutes."""
    # Initial fetch after 30s (let server start first)
    time.sleep(30)
    while True:
        try:
            run_fetch_cycle()
        except Exception as e:
            log.error(f"Background cycle error: {e}")
        time.sleep(900)  # 15 min


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    log.info("Starting Multi-Source Proxy Aggregator v2.0...")

    # Start background fetcher
    threading.Thread(target=background_loop, daemon=True).start()

    # Start API server
    port = int(os.environ.get("POOL_PORT", 5001))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
