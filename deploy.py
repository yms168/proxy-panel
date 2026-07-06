#!/usr/bin/env python3
"""
One-Click Deployment Script for Unified Proxy Manager
======================================================
Deploys the Multi-Source Proxy Aggregator and Unified Panel to your VPS.

Prerequisites:
  - Ubuntu 24.04 VPS with root SSH access
  - 3x-ui already installed (or install with: install-3xui.sh)

Usage:
  python deploy.py <server_ip> <ssh_password>

Example:
  python deploy.py 103.79.184.54 mypassword
"""
import paramiko
import os
import sys
import time
import json
from pathlib import Path


LOCAL_DIR = Path(__file__).parent


def deploy(host: str, password: str, user: str = "root", port: int = 22):
    """Deploy all components to the target server."""

    def ssh(cmd, timeout=60):
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(host, username=user, password=password, port=port,
                  timeout=10, look_for_keys=False, allow_agent=False)
        _, stdout, stderr = c.exec_command(cmd, timeout=timeout)
        o = stdout.read().decode('utf-8', errors='replace')
        e = stderr.read().decode('utf-8', errors='replace')
        c.close()
        if e: print("  [stderr]", e[:300])
        return o

    def upload(local_path, remote_path):
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(host, username=user, password=password, port=port,
                  timeout=10, look_for_keys=False, allow_agent=False)
        sftp = c.open_sftp()
        # Ensure directory exists
        rd = os.path.dirname(remote_path)
        try:
            sftp.stat(rd)
        except:
            parts = rd.strip('/').split('/')
            p = ''
            for part in parts:
                p += '/' + part
                try:
                    sftp.stat(p)
                except:
                    sftp.mkdir(p)
        sftp.put(str(local_path), remote_path)
        sftp.close()
        c.close()
        return True

    print("=" * 60)
    print("Unified Proxy Manager - Deployment")
    print(f"Target: {user}@{host}:{port}")
    print("=" * 60)

    # --- Step 1: Check server ---
    print("\n[1/5] Checking server...")
    out = ssh("python3 --version && pip3 --version && df -h / | tail -1")
    print(out[:300])

    # --- Step 2: Install Python deps ---
    print("[2/5] Installing Python dependencies...")
    out = ssh("pip3 install --break-system-packages fastapi uvicorn requests pydantic 2>&1 | tail -5")
    print(out[:300] if out else "(already installed or no output)")

    # --- Step 3: Upload & start Proxy Pool ---
    print("[3/5] Deploying Multi-Source Proxy Aggregator...")
    pool_dir = LOCAL_DIR / "proxy-pool"
    for f in pool_dir.iterdir():
        if f.is_file():
            upload(f, f"/opt/proxy-pool/{f.name}")
            print(f"  -> /opt/proxy-pool/{f.name}")

    # Write systemd service
    ssh("cat > /etc/systemd/system/proxy-pool.service << 'EOF'\n"
        "[Unit]\nDescription=Multi-Source Proxy Aggregator\nAfter=network.target\n\n"
        "[Service]\nType=simple\nUser=root\nWorkingDirectory=/opt/proxy-pool\n"
        "ExecStart=/usr/bin/python3 /opt/proxy-pool/server.py\n"
        "Restart=always\nRestartSec=10\nEnvironment=PYTHONUNBUFFERED=1\n\n"
        "[Install]\nWantedBy=multi-user.target\nEOF")

    ssh("systemctl daemon-reload && systemctl enable proxy-pool && systemctl restart proxy-pool")
    time.sleep(2)
    out = ssh("systemctl status proxy-pool --no-pager | head -5")
    print(out[:300])

    # --- Step 4: Upload & start Unified Panel ---
    print("[4/5] Deploying Unified Panel...")
    panel_dir = LOCAL_DIR / "unified-panel"
    for f in panel_dir.iterdir():
        if f.is_file():
            upload(f, f"/opt/unified-panel/{f.name}")
            print(f"  -> /opt/unified-panel/{f.name}")

    ssh("cat > /etc/systemd/system/unified-panel.service << 'EOF'\n"
        "[Unit]\nDescription=Unified Proxy Manager Panel\n"
        "After=network.target proxy-pool.service\nWants=proxy-pool.service\n\n"
        "[Service]\nType=simple\nUser=root\nWorkingDirectory=/opt/unified-panel\n"
        "ExecStart=/usr/bin/python3 /opt/unified-panel/server.py\n"
        "Restart=always\nRestartSec=10\nEnvironment=PYTHONUNBUFFERED=1\nEnvironment=PANEL_PORT=8888\n\n"
        "[Install]\nWantedBy=multi-user.target\nEOF")

    ssh("systemctl daemon-reload && systemctl enable unified-panel && systemctl restart unified-panel")
    time.sleep(2)
    out = ssh("systemctl status unified-panel --no-pager | head -5")
    print(out[:300])

    # --- Step 5: Verify ---
    print("[5/5] Verifying deployment...")
    time.sleep(3)

    print("\n--- Listening Ports ---")
    out = ssh("ss -tlnp | grep -E '5001|8888|2506'")
    print(out or "(checking...)")

    # External checks
    import requests
    checks = {
        "Proxy Pool API": f"http://{host}:5001/api/status",
        "Unified Panel API": f"http://{host}:8888/api/unified/status",
        "Unified Panel UI": f"http://{host}:8888/",
    }

    for name, url in checks.items():
        try:
            r = requests.get(url, timeout=10)
            print(f"  ✅ {name}: HTTP {r.status_code}")
        except Exception as e:
            print(f"  ❌ {name}: {e}")

    print("\n" + "=" * 60)
    print("🎉 Deployment Complete!")
    print(f"  Proxy Pool API:  http://{host}:5001/api/status")
    print(f"  Unified Panel:   http://{host}:8888/")
    print(f"  (3x-ui should be at port 2506)")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    deploy(sys.argv[1], sys.argv[2])
