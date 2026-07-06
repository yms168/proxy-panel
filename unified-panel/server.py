#!/usr/bin/env python3
"""
Unified Proxy Manager Panel v2.0
=================================
One dashboard to rule them all — integrates 3x-ui Xray panel
with the Multi-Source Proxy Aggregator.

Features:
  - Real-time dashboard: exit IP, latency, node count, Xray traffic
  - Smart Connect: one-click best residential proxy selection
  - Node pool browser with filters (country, type, sort)
  - Best-node ranking (residential-first, lowest latency)
  - Direct Xray integration (reads from x-ui SQLite DB)

Prerequisites:
  - 3x-ui installed with Xray running
  - Multi-Source Proxy Aggregator running on port 5001

Author: https://blog.lingdian168.online/
License: MIT
"""
import json
import os
import re
import subprocess
import sys
import time
import threading
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
import requests
import sqlite3
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

# --- Config (env vars or defaults) ---
XUI_DB = os.environ.get("XUI_DB", "/etc/x-ui/x-ui.db")
XRAY_BIN = os.environ.get("XRAY_BIN", "/usr/local/x-ui/bin/xray-linux-amd64")
PROXY_POOL_URL = os.environ.get("POOL_URL", "http://127.0.0.1:5001")
PANEL_PORT = int(os.environ.get("PANEL_PORT", 8888))
SITE_URL = os.environ.get("SITE_URL", "https://blog.lingdian168.online/")

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("unified-panel")

# ============================================================
# Xray Status Helpers (reads x-ui DB directly, no auth needed)
# ============================================================

def is_xray_running() -> bool:
    """Check if xray process is alive."""
    try:
        result = subprocess.run(["pgrep", "-f", "xray-linux"], capture_output=True, timeout=5)
        return result.returncode == 0
    except:
        return False


def get_xray_version() -> str:
    """Get Xray version from binary."""
    try:
        result = subprocess.run([XRAY_BIN, "version"], capture_output=True, timeout=5)
        out = result.stdout.decode()
        m = re.search(r'Xray\s+([\d.]+)', out)
        return m.group(1) if m else "?"
    except:
        try:
            # Fallback: check journal
            result = subprocess.run(
                ["journalctl", "-u", "x-ui", "--no-pager", "-n", "5"],
                capture_output=True, timeout=5
            )
            m = re.search(r'Xray\s+([\d.]+)', result.stdout.decode())
            return m.group(1) if m else "?"
        except:
            return "?"


def get_inbounds_from_db() -> list:
    """Read inbound proxies from x-ui SQLite database."""
    try:
        conn = sqlite3.connect(XUI_DB)
        rows = conn.execute(
            "SELECT id, remark, port, protocol, enable, up, down FROM inbounds"
        ).fetchall()
        conn.close()
        return [{
            "id": r[0], "remark": r[1] or "", "port": r[2],
            "protocol": r[3] or "?", "enable": bool(r[4]),
            "up": r[5] or 0, "down": r[6] or 0
        } for r in rows]
    except Exception as e:
        log.error(f"DB error: {e}")
        return []


def get_total_traffic() -> tuple:
    """Get total upload/download bytes from all inbounds."""
    try:
        conn = sqlite3.connect(XUI_DB)
        row = conn.execute(
            "SELECT COALESCE(SUM(up),0), COALESCE(SUM(down),0) FROM inbounds"
        ).fetchone()
        conn.close()
        return row[0] or 0, row[1] or 0
    except:
        return 0, 0


# ============================================================
# Proxy Pool Client
# ============================================================

class PoolClient:
    """HTTP client for the Multi-Source Proxy Aggregator API."""

    def __init__(self, base_url: str = PROXY_POOL_URL):
        self.base = base_url

    def _get(self, path: str, params: dict = None) -> dict:
        try:
            r = requests.get(f"{self.base}{path}", params=params, timeout=10)
            return r.json() if r.ok else {}
        except:
            return {}

    def _post(self, path: str) -> dict:
        try:
            r = requests.post(f"{self.base}{path}", timeout=10)
            return r.json() if r.ok else {}
        except:
            return {}

    def status(self) -> dict:
        return self._get("/api/status")

    def best(self, count: int = 10) -> dict:
        return self._get("/api/proxies/best", {"count": count, "prefer_residential": True})

    def proxies(self, **filters) -> dict:
        params = {k: v for k, v in filters.items() if v}
        return self._get("/api/proxies", params)

    def fetch(self) -> dict:
        return self._post("/api/fetch")


pool = PoolClient()

# ============================================================
# Cache
# ============================================================

cache = {"exit_ip": None, "latency": None, "ts": 0}
cache_lock = threading.Lock()


def fetch_exit_ip() -> Optional[str]:
    """Get current public exit IP."""
    try:
        r = requests.get("http://ifconfig.me/ip", timeout=8)
        return r.text.strip()
    except:
        return None


# ============================================================
# FastAPI Application
# ============================================================

app = FastAPI(
    title="Unified Proxy Manager",
    version="2.0.0",
    description="Dashboard integrating Xray + Multi-Source Proxy Aggregator",
)


@app.get("/api/unified/status")
async def unified_status():
    """Get combined status from all subsystems."""
    ps = pool.status()
    xray_ok = is_xray_running()
    inbounds = get_inbounds_from_db()
    total_up, total_down = get_total_traffic()

    # Get proxy server endpoints
    proxy_endpoints = {}
    try:
        r = requests.get(f"{PROXY_POOL_URL}/api/proxy/status", timeout=5)
        if r.ok: proxy_endpoints = r.json()
    except:
        pass

    with cache_lock:
        ip = cache.get("exit_ip")
        lat = cache.get("latency")

    # Refresh exit IP if stale (>60s)
    if ip is None or (cache.get("ts", 0) and time.time() - cache["ts"] > 60):
        new_ip = fetch_exit_ip()
        if new_ip:
            ip = new_ip
            with cache_lock:
                cache["exit_ip"] = ip
                cache["ts"] = time.time()

    return {
        "proxy_ok": ps.get("alive", 0) > 0,
        "proxy_total": ps.get("total", 0),
        "proxy_alive": ps.get("alive", 0),
        "proxy_residential": ps.get("residential", 0),
        "proxy_datacenter": ps.get("datacenter", 0),
        "proxy_avg_latency": ps.get("avg_latency", 0),
        "xray_running": xray_ok,
        "xray_version": get_xray_version(),
        "inbounds_count": len(inbounds),
        "traffic_up_mb": round(total_up / 1048576, 2),
        "traffic_down_mb": round(total_down / 1048576, 2),
        "exit_ip": ip or "--",
        "latency": lat or "--",
        "inbounds": inbounds[:10],
        "proxy_endpoints": proxy_endpoints,
    }


@app.get("/api/unified/nodes")
async def list_nodes(
    country: str = Query(None),
    ip_type: str = Query(None),
    sort: str = Query("latency"),
    limit: int = Query(200, le=500),
):
    """List proxy nodes with filtering and sorting."""
    params = {"alive_only": True, "limit": limit}
    if country: params["country"] = country
    if ip_type: params["ip_type"] = ip_type

    result = pool.proxies(**params)
    proxies = result.get("proxies", [])

    sort_key_map = {"latency": "latency_ms", "score": "score", "type": "ip_type"}
    sort_key = sort_key_map.get(sort, "latency_ms")
    proxies.sort(key=lambda p: p.get(sort_key, 9999), reverse=(sort == "score"))

    return {"nodes": proxies, "total": len(proxies)}


@app.get("/api/unified/best")
async def best_nodes(count: int = Query(10)):
    """Get top-ranked nodes: residential-first, lowest latency."""
    result = pool.best(count=count)
    return {"nodes": result.get("proxies", [])}


@app.post("/api/unified/smart-connect")
async def smart_connect():
    """Pick the best residential proxy and return connection info."""
    best = pool.best(count=1).get("proxies", [])
    if not best:
        raise HTTPException(500, "No proxies available. Click 'Refresh Nodes' first.")

    proxy = best[0]
    with cache_lock:
        cache["exit_ip"] = proxy["ip"]
        cache["latency"] = str(proxy.get("latency_ms", "--"))
        cache["ts"] = time.time()

    return {
        "status": "connected",
        "proxy": proxy,
        "note": (
            f"Best residential proxy selected: {proxy['ip']}:{proxy['port']} "
            f"({proxy.get('country', '?')}, {proxy.get('latency_ms', '?')}ms). "
            f"Configure this as your Xray outbound for proxying."
        )
    }


@app.post("/api/unified/refresh")
async def refresh_nodes():
    """Trigger a full proxy pool refresh."""
    return pool.fetch()

@app.post("/api/unified/rotate")
async def rotate_proxy():
    """Rotate to a new upstream proxy."""
    try:
        r = requests.post(f"{PROXY_POOL_URL}/api/proxy/rotate", timeout=10)
        return r.json()
    except Exception as e:
        raise HTTPException(500, f"Rotation failed: {e}")


# ============================================================
# Subscription Endpoint
# ============================================================

# VMess inbound info (matches what we set up in 3x-ui)
VMESS_PORT = 10000
VMESS_CLIENT_ID = "45e3d998-89f1-458c-be77-0753a8818d50"
VMESS_WS_PATH = "/proxy"
SERVER_IP = os.environ.get("SERVER_IP", "")

import base64 as _b64

@app.get("/sub")
async def subscription(format: str = "raw"):
    """
    Subscription endpoint for proxy clients (V2RayN, Clash, Shadowrocket, etc.)
    GET /sub            -> Raw VMess links (one per line)
    GET /sub?format=clash -> Clash YAML config
    GET /sub?format=base64 -> Base64-encoded VMess links
    """
    # Determine server IP
    server_ip = SERVER_IP
    if not server_ip:
        # Try to get from request or from server
        try:
            server_ip = requests.get("http://ifconfig.me/ip", timeout=3).text.strip()
        except:
            server_ip = "103.79.184.54"

    vmess_config = {
        "v": "2",
        "ps": "ProxyPool-US-Residential",
        "add": server_ip,
        "port": str(VMESS_PORT),
        "id": VMESS_CLIENT_ID,
        "aid": "0",
        "scy": "auto",
        "net": "ws",
        "type": "none",
        "host": "",
        "path": VMESS_WS_PATH,
        "tls": "",
        "sni": "",
        "alpn": ""
    }

    vmess_link = "vmess://" + _b64.b64encode(json.dumps(vmess_config, ensure_ascii=False).encode()).decode()

    if format == "clash":
        clash_yaml = f"""proxies:
  - name: "ProxyPool-US"
    type: vmess
    server: {server_ip}
    port: {VMESS_PORT}
    uuid: {VMESS_CLIENT_ID}
    alterId: 0
    cipher: auto
    network: ws
    ws-opts:
      path: {VMESS_WS_PATH}
proxy-groups:
  - name: "Proxy"
    type: select
    proxies:
      - ProxyPool-US
rules:
  - MATCH,Proxy
"""
        return HTMLResponse(content=clash_yaml, media_type="text/plain")

    if format == "base64":
        return HTMLResponse(content=_b64.b64encode(vmess_link.encode()).decode(), media_type="text/plain")

    # Default: raw VMess link
    return HTMLResponse(content=vmess_link, media_type="text/plain")


@app.get("/api/unified/subscription")
async def subscription_info():
    """Get subscription information and links."""
    server_ip = SERVER_IP
    if not server_ip:
        try:
            server_ip = requests.get("http://ifconfig.me/ip", timeout=3).text.strip()
        except:
            server_ip = "103.79.184.54"

    vmess_config = {
        "v": "2",
        "ps": "ProxyPool-US-Residential",
        "add": server_ip,
        "port": str(VMESS_PORT),
        "id": VMESS_CLIENT_ID,
        "aid": "0",
        "scy": "auto",
        "net": "ws",
        "type": "none",
        "host": "",
        "path": VMESS_WS_PATH,
        "tls": "",
    }
    vmess_link = "vmess://" + _b64.b64encode(json.dumps(vmess_config, ensure_ascii=False).encode()).decode()

    return {
        "vmess_link": vmess_link,
        "subscription_url": f"http://{server_ip}:8888/sub",
        "clash_subscription": f"http://{server_ip}:8888/sub?format=clash",
        "base64_subscription": f"http://{server_ip}:8888/sub?format=base64",
        "server_ip": server_ip,
        "vmess_port": VMESS_PORT,
        "socks5_proxy": f"{server_ip}:10808",
        "http_proxy": f"{server_ip}:10809",
        "direct_socks5": f"{server_ip}:1080",
        "direct_http": f"{server_ip}:1081",
        "note": "Use the subscription URL in V2RayN/Clash/Shadowrocket clients"
    }


# ============================================================
# Dashboard HTML (Single Page App)
# ============================================================

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>⚡ Unified Proxy Manager</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0e17;color:#c9d1d9;min-height:100vh;line-height:1.5}
.header{background:linear-gradient(135deg,#131a2b,#0f1419);padding:16px 28px;border-bottom:1px solid #21262d;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}
.header h1{font-size:1.25rem;font-weight:600}
.status-row{display:flex;gap:20px;font-size:.85rem}
.status-item{display:flex;align-items:center;gap:6px}
.dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.dot.online{background:#3fb950;box-shadow:0 0 6px rgba(63,185,80,.5)}
.dot.offline{background:#f85149;box-shadow:0 0 6px rgba(248,81,73,.4)}
.dot.warn{background:#d29922}
.container{max-width:1400px;margin:0 auto;padding:20px 24px}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin-bottom:24px}
.card{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:18px 20px;transition:border-color .2s}
.card:hover{border-color:#30363d}
.card h3{font-size:.8rem;color:#8b949e;margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px}
.card .val{font-size:1.7rem;font-weight:700}
.card .sub{font-size:.78rem;color:#6e7681;margin-top:2px}
.btn{padding:10px 22px;border:1px solid #30363d;border-radius:8px;font-size:.9rem;cursor:pointer;font-weight:500;transition:all .15s;background:#21262d;color:#c9d1d9}
.btn-primary{background:linear-gradient(135deg,#1f6feb,#388bfd);border-color:#388bfd;color:#fff}
.btn-primary:hover{background:linear-gradient(135deg,#388bfd,#58a6ff)}
.btn:hover{border-color:#58a6ff}
.section{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:20px;margin-bottom:18px}
.section h2{font-size:1rem;margin-bottom:14px;color:#58a6ff}
.filters{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}
.filters select,.filters input{padding:8px 12px;border:1px solid #30363d;border-radius:6px;background:#0d1117;color:#c9d1d9;font-size:.85rem}
.filters input{flex:1;min-width:180px}
.filters input::placeholder{color:#484f58}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #21262d;font-size:.85rem}
th{color:#8b949e;font-weight:600;font-size:.8rem}
tr:hover{background:rgba(56,139,253,.05)}
.tag{padding:2px 8px;border-radius:12px;font-size:.73rem;font-weight:600;white-space:nowrap}
.tag-res{background:rgba(63,185,80,.15);color:#3fb950}
.tag-dc{background:rgba(139,148,158,.1);color:#8b949e}
.tag-live{background:rgba(63,185,80,.12);color:#3fb950}
.tag-dead{background:rgba(248,81,73,.12);color:#f85149}
.msg{text-align:center;padding:40px;color:#484f58}
.err-msg{background:rgba(248,81,73,.08);border:1px solid rgba(248,81,73,.3);border-radius:8px;padding:12px;color:#f85149;margin:8px 0}
.ok-msg{background:rgba(63,185,80,.08);border:1px solid rgba(63,185,80,.3);border-radius:8px;padding:14px;color:#3fb950;margin:8px 0}
.btn-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}
.footer{text-align:center;padding:20px;color:#484f58;font-size:.8rem}
.footer a{color:#58a6ff;text-decoration:none}
code{background:#0d1117;padding:1px 6px;border-radius:4px;font-size:.82rem}
</style>
</head>
<body>
<div class="header">
<h1>⚡ Unified Proxy Manager</h1>
<div class="status-row">
<div class="status-item"><span class="dot" id="proxy-dot"></span>Proxy:<b id="proxy-st">--</b></div>
<div class="status-item"><span class="dot" id="xray-dot"></span>Xray:<b id="xray-st">--</b></div>
</div>
</div>
<div class="container">
<div class="stats-grid">
<div class="card"><h3>🌐 当前出口 IP</h3><div class="val" id="exitIp">--</div><div class="sub" id="exitIpSub"></div></div>
<div class="card"><h3>⏱ 延迟</h3><div class="val" id="latency">--</div><div class="sub">到出口代理延迟</div></div>
<div class="card"><h3>🔢 可用节点</h3><div class="val" id="totalNodes">--</div><div class="sub" id="nodeSub">住宅: -- / 机房: --</div></div>
<div class="card"><h3>📊 Xray 流量</h3><div class="val" id="traffic">--</div><div class="sub">上行 ↑ / 下行 ↓</div></div>
</div>
<div class="btn-row">
<button class="btn btn-primary" onclick="smartConnect()">🎯 一键智能最优连接</button>
<button class="btn" onclick="fetchAll()">🔄 抓取最新节点</button>
<button class="btn" onclick="refreshAll()">📋 刷新状态</button>
</div>
<div class="section">
<h2>🔌 代理订阅 / 出口</h2>
<div class="btn-row" style="margin-bottom:8px">
<button class="btn" onclick="rotateProxy()">🔄 切换出口 IP</button>
</div>
<div id="proxyEndpointsPanel" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px">
<div class="msg">Loading...</div>
</div>
</div>
<div class="section"><h2>🏆 最优候选节点（按延迟 + 住宅IP优先级排序）</h2><div id="bestPanel">点击 "智能最优连接" 选择最优出口...</div></div>
<div class="section">
<h2>📋 节点池</h2>
<div class="filters">
<select id="fCountry" onchange="loadNodes()"><option value="">所有国家</option></select>
<select id="fType" onchange="loadNodes()"><option value="">所有类型</option><option value="residential">🏠 住宅 IP</option><option value="datacenter">🏢 机房 IP</option></select>
<select id="fSort" onchange="loadNodes()"><option value="latency">按延迟排序</option><option value="score">按评分排序</option><option value="type">按类型排序</option></select>
<input type="text" id="fSearch" placeholder="搜索 IP、位置、ISP..." oninput="loadNodes()">
</div>
<div id="nodesTable"><div class="msg">Loading...</div></div>
</div>
</div>
<div class="footer">Powered by <a href="https://blog.lingdian168.online/" target="_blank">零点博客</a> | Unified Proxy Manager v2.0</div>
<script>
async function api(path,opts={}){try{const r=await fetch('/api/unified'+path,opts);return await r.json()}catch(e){return{error:e.message}}}
async function refreshAll(){const s=await api('/status');document.getElementById('proxy-dot').className='dot '+(s.proxy_ok?'online':'warn');document.getElementById('proxy-st').textContent=s.proxy_ok?'Online':(s.proxy_total?'Partial':'No nodes');document.getElementById('xray-dot').className='dot '+(s.xray_running?'online':'offline');document.getElementById('xray-st').textContent=s.xray_running?'Active v'+(s.xray_version||'?'):'Down';document.getElementById('exitIp').textContent=s.exit_ip||'--';document.getElementById('latency').textContent=s.latency?s.latency+'ms':'--';document.getElementById('totalNodes').textContent=s.proxy_alive;document.getElementById('nodeSub').textContent='住宅: '+(s.proxy_residential||0)+' / 机房: '+(s.proxy_datacenter||0);document.getElementById('traffic').textContent=(s.traffic_up_mb||0).toFixed(1)+' MB ↑ / '+(s.traffic_down_mb||0).toFixed(1)+' MB ↓';document.getElementById('exitIpSub').textContent=s.xray_running?'Xray v'+(s.xray_version||'?')+' | Inbounds: '+(s.inbounds_count||0):'';return s}
async function loadNodes(){const p=new URLSearchParams();const c=document.getElementById('fCountry').value;const t=document.getElementById('fType').value;const s=document.getElementById('fSort').value;if(c)p.set('country',c);if(t)p.set('ip_type',t);p.set('sort',s);p.set('limit',200);const d=await api('/nodes?'+p);const nodes=d.nodes||[];const q=(document.getElementById('fSearch')?.value||'').toLowerCase();const filtered=q?nodes.filter(n=>(n.ip||'').includes(q)||(n.country||'').toLowerCase().includes(q)||(n.isp||'').toLowerCase().includes(q)||(n.region||'').toLowerCase().includes(q)):nodes;let h='<table><thead><tr><th>状态</th><th>延迟</th><th>IP:端口</th><th>位置</th><th>ISP</th><th>类型</th><th>协议</th><th>评分</th></tr></thead><tbody>';if(filtered.length===0){h+='<tr><td colspan="8" class="msg">暂无节点，点击 "抓取最新节点" 获取</td></tr>'}else{for(const n of filtered.slice(0,80)){const alive=n.alive?'<span class="tag tag-live">在线</span>':'<span class="tag tag-dead">离线</span>';const type=n.ip_type==='residential'?'<span class="tag tag-res">🏠 住宅</span>':'<span class="tag tag-dc">🏢 机房</span>';const lat=n.latency_ms<9999?n.latency_ms+'ms':'超时';h+='<tr><td>'+alive+'</td><td>'+lat+'</td><td><code>'+n.ip+':'+n.port+'</code></td><td>'+(n.country||'?')+(n.region?' / '+n.region:'')+'</td><td>'+(n.isp||'--')+'</td><td>'+type+'</td><td>'+(n.protocol||'?').toUpperCase()+'</td><td>'+(n.score||0)+'</td></tr>'}}h+='</tbody></table>';document.getElementById('nodesTable').innerHTML=h}
async function loadBest(){const d=await api('/best?count=10');const nodes=d.nodes||[];if(nodes.length===0){document.getElementById('bestPanel').innerHTML='<div class="msg">暂无节点，点击 "抓取最新节点" 获取</div>';return}let h='<table><thead><tr><th>#</th><th>延迟</th><th>IP:端口</th><th>位置</th><th>ISP</th><th>类型</th><th>评分</th></tr></thead><tbody>';nodes.forEach((n,i)=>{const type=n.ip_type==='residential'?'<span class="tag tag-res">🏠 住宅</span>':'<span class="tag tag-dc">🏢 机房</span>';h+='<tr><td>'+(i+1)+'</td><td>'+(n.latency_ms||'?')+'ms</td><td><code>'+n.ip+':'+n.port+'</code></td><td>'+(n.country||'?')+(n.region?' / '+n.region:'')+'</td><td>'+(n.isp||'--')+'</td><td>'+type+'</td><td>'+(n.score||0)+'</td></tr>'});h+='</tbody></table>';document.getElementById('bestPanel').innerHTML=h}
async function smartConnect(){document.getElementById('bestPanel').innerHTML='<div class="msg">正在分析最优节点...</div>';const d=await api('/smart-connect',{method:'POST'});if(d.status==='connected'){const p=d.proxy;document.getElementById('bestPanel').innerHTML='<div class="ok-msg">✅ <b>已连接到最优节点</b><br><br>IP: <code>'+p.ip+':'+p.port+'</code> | '+(p.country||'?')+' | <span class="tag tag-res">'+(p.ip_type==='residential'?'🏠 住宅IP':'🏢 机房IP')+'</span> | 延迟: '+(p.latency_ms||'?')+'ms | ISP: '+(p.isp||'--')+'<br><br><small style="color:#8b949e">当前出口 IP: '+p.ip+'</small></div>'}else{document.getElementById('bestPanel').innerHTML='<div class="err-msg">连接失败: '+JSON.stringify(d)+'</div>'}refreshAll()}
function renderProxyEndpoints(ep){
  if(!ep || !ep.active){
    document.getElementById('proxyEndpointsPanel').innerHTML='<div class="msg">代理服务启动中...请稍后刷新</div>';
    return;
  }
  const up=ep.upstream||{};
  let h='';
  h+='<div class="card" style="border-color:#3fb950">';
  h+='<h3>SOCKS5 代理</h3><div class="val" style="font-size:1.2rem"><code>'+ep.socks5+'</code></div>';
  h+='<div class="sub">当前上游: '+up.ip+':'+up.port+' ['+(up.country||'?')+'] '+up.latency+'ms</div></div>';
  h+='<div class="card" style="border-color:#58a6ff">';
  h+='<h3>HTTP 代理</h3><div class="val" style="font-size:1.2rem"><code>'+ep.http+'</code></div>';
  h+='<div class="sub">浏览器/curl 直接使用 | 上游: '+up.ip+':'+up.port+'</div></div>';
  document.getElementById('proxyEndpointsPanel').innerHTML=h;
}
async function rotateProxy(){
  document.getElementById('proxyEndpointsPanel').innerHTML='<div class="msg">正在切换到新的住宅 IP...</div>';
  const d=await api('/rotate',{method:'POST'});
  if(d.success){
    const up=d.upstream;
    document.getElementById('proxyEndpointsPanel').innerHTML='<div class="ok-msg">切换到新出口: <code>'+up.ip+':'+up.port+'</code> ['+(up.country||'?')+'] '+up.latency+'ms</div>';
  }else{
    document.getElementById('proxyEndpointsPanel').innerHTML='<div class="err-msg">切换失败，请确认代理池中有可用节点</div>';
  }
  setTimeout(refreshAll,2000);
}
async function refreshAll(){
  const s=await api('/status');
  document.getElementById('proxy-dot').className='dot '+(s.proxy_ok?'online':'warn');
  document.getElementById('proxy-st').textContent=s.proxy_ok?'Online':(s.proxy_total?'Partial':'No nodes');
  document.getElementById('xray-dot').className='dot '+(s.xray_running?'online':'offline');
  document.getElementById('xray-st').textContent=s.xray_running?'Active v'+(s.xray_version||'?'):'Down';
  document.getElementById('exitIp').textContent=s.exit_ip||'--';
  document.getElementById('latency').textContent=s.latency?s.latency+'ms':'--';
  document.getElementById('totalNodes').textContent=s.proxy_alive;
  document.getElementById('nodeSub').textContent='住宅: '+(s.proxy_residential||0)+' / 机房: '+(s.proxy_datacenter||0);
  document.getElementById('traffic').textContent=(s.traffic_up_mb||0).toFixed(1)+' MB up / '+(s.traffic_down_mb||0).toFixed(1)+' MB down';
  document.getElementById('exitIpSub').textContent=s.xray_running?'Xray v'+(s.xray_version||'?')+' | Inbounds: '+(s.inbounds_count||0):'';
  renderProxyEndpoints(s.proxy_endpoints);
  return s;
}
async function fetchAll(){document.getElementById('bestPanel').innerHTML='<div class="msg">正在从多个源抓取最新节点...约需 30 秒</div>';await api('/refresh',{method:'POST'});document.getElementById('bestPanel').innerHTML='<div class="msg">抓取任务已启动，10 秒后刷新...</div>';setTimeout(()=>{loadNodes();loadBest();refreshAll()},10000)}
refreshAll();loadNodes();loadBest();setInterval(refreshAll,30000);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    log.info(f"⚡ Unified Proxy Manager starting on :{PANEL_PORT}")
    # Initial exit IP fetch
    def init_fetch():
        time.sleep(2)
        ip = fetch_exit_ip()
        if ip:
            with cache_lock:
                cache["exit_ip"] = ip
                cache["ts"] = time.time()
    threading.Thread(target=init_fetch, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=PANEL_PORT, log_level="warning")
