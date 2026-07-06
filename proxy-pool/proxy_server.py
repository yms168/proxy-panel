#!/usr/bin/env python3
"""
Built-in SOCKS5/HTTP Proxy Server
==================================
Provides direct proxy endpoints that auto-route through the best pool proxy.

SOCKS5: port 1080
HTTP:   port 1081

The upstream proxy is dynamically selected from the pool (best residential, lowest latency).
"""
import logging
import socket
import struct
import sys
import threading
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("proxy-server")

SOCKS5_PORT = 1080
HTTP_PORT = 1081

# Current upstream proxy (set externally by the pool)
current_upstream = {"ip": None, "port": 0, "latency": 0, "country": "", "protocol": "socks5"}
upstream_lock = threading.Lock()

# Reference to the pool (set by server.py)
_pool = None


def set_pool(pool_obj):
    """Set the proxy pool reference for upstream selection."""
    global _pool
    _pool = pool_obj


def pick_best_upstream():
    """Pick the best residential proxy from the pool."""
    global _pool
    if _pool is None:
        log.error("Pool not initialized")
        return False
    try:
        best = _pool.get_best(prefer_residential=True, count=1)
        if best:
            p = best[0]
            with upstream_lock:
                current_upstream["ip"] = p.ip
                current_upstream["port"] = p.port
                current_upstream["latency"] = p.latency_ms
                current_upstream["country"] = p.country
                current_upstream["protocol"] = p.protocol
            log.info(f"Upstream: {p.ip}:{p.port} [{p.country}] {p.latency_ms}ms")
            return True
    except Exception as e:
        log.error(f"Pick upstream error: {e}")
    return False


def get_upstream():
    """Get current upstream proxy info."""
    with upstream_lock:
        return dict(current_upstream)


# ============================================================
# SOCKS5 Proxy Server
# ============================================================

def handle_socks5_client(client_sock, client_addr):
    """Handle a single SOCKS5 client connection."""
    upstream = get_upstream()
    if not upstream.get("ip"):
        client_sock.close()
        return

    try:
        client_sock.settimeout(30)

        # 1. Greeting
        data = client_sock.recv(2)
        if len(data) < 2 or data[0] != 0x05:
            client_sock.close()
            return
        nmethods = data[1]
        methods = client_sock.recv(nmethods)
        client_sock.send(b'\x05\x00')

        # 2. Request
        data = client_sock.recv(4)
        if len(data) < 4:
            client_sock.close()
            return
        ver, cmd, _, atyp = data[0], data[1], data[2], data[3]

        # Read destination address
        dst_addr = None
        dst_port = None
        if atyp == 1:  # IPv4
            addr_data = client_sock.recv(4)
            dst_addr = socket.inet_ntoa(addr_data)
        elif atyp == 3:  # Domain name
            name_len = client_sock.recv(1)[0]
            dst_addr = client_sock.recv(name_len).decode()
        elif atyp == 4:  # IPv6
            addr_data = client_sock.recv(16)
            dst_addr = socket.inet_ntop(socket.AF_INET6, addr_data)
        else:
            client_sock.close()
            return

        dst_port_data = client_sock.recv(2)
        dst_port = struct.unpack('>H', dst_port_data)[0]

        if cmd == 1:  # CONNECT - route through upstream proxy
            # Connect to upstream SOCKS5 proxy
            up = get_upstream()
            up_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            up_sock.settimeout(10)
            up_sock.connect((up["ip"], up["port"]))

            # SOCKS5 handshake with upstream
            up_sock.send(b'\x05\x01\x00')
            up_sock.recv(2)

            # Ask upstream to connect to target
            req = b'\x05\x01\x00\x03' + bytes([len(dst_addr)]) + dst_addr.encode() + struct.pack('>H', dst_port)
            up_sock.send(req)
            resp = up_sock.recv(10)
            if len(resp) < 2 or resp[1] != 0x00:
                up_sock.close()
                client_sock.send(b'\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00')
                return

            # Reply success to client
            bind_addr = b'\x00' + socket.inet_aton('0.0.0.0') + struct.pack('>H', 0)
            client_sock.send(b'\x05\x00\x00' + b'\x01' + bind_addr)

            # Relay client <-> upstream <-> target
            relay(client_sock, up_sock)
        else:
            client_sock.send(b'\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00')

    except Exception as e:
        log.debug(f"SOCKS5 error from {client_addr}: {e}")
    finally:
        try:
            client_sock.close()
        except:
            pass


def relay(sock1, sock2):
    """Bidirectional relay between two sockets."""
    import select
    sockets = [sock1, sock2]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], 30)
            if not readable:
                break
            for s in readable:
                data = s.recv(8192)
                if not data:
                    return
                other = sock2 if s is sock1 else sock1
                other.sendall(data)
    except:
        pass
    finally:
        try:
            sock1.close()
        except:
            pass
        try:
            sock2.close()
        except:
            pass


def run_socks5_server():
    """Start SOCKS5 proxy server."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', SOCKS5_PORT))
    server.listen(50)
    log.info(f"SOCKS5 proxy on 0.0.0.0:{SOCKS5_PORT}")

    while True:
        try:
            client_sock, addr = server.accept()
            t = threading.Thread(target=handle_socks5_client, args=(client_sock, addr), daemon=True)
            t.start()
        except Exception as e:
            log.error(f"SOCKS5 accept error: {e}")


# ============================================================
# HTTP Proxy Server
# ============================================================

def handle_http_client(client_sock, client_addr):
    """Handle a single HTTP proxy client connection."""
    try:
        client_sock.settimeout(30)
        data = client_sock.recv(4096)
        if not data:
            client_sock.close()
            return

        request = data.decode('utf-8', errors='replace')
        lines = request.split('\r\n')
        first_line = lines[0].split(' ')

        if first_line[0] == 'CONNECT':
            host_port = first_line[1].split(':')
            host = host_port[0]
            port = int(host_port[1]) if len(host_port) > 1 else 443

            # Connect through upstream SOCKS5
            up = get_upstream()
            up_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            up_sock.settimeout(10)
            up_sock.connect((up["ip"], up["port"]))
            up_sock.send(b'\x05\x01\x00')
            up_sock.recv(2)
            req = b'\x05\x01\x00\x03' + bytes([len(host)]) + host.encode() + struct.pack('>H', port)
            up_sock.send(req)
            resp = up_sock.recv(10)
            if len(resp) < 2 or resp[1] != 0x00:
                up_sock.close()
                client_sock.send(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
                return

            client_sock.send(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            relay(client_sock, up_sock)
        else:
            # Regular HTTP
            host = None
            port = 80
            for line in lines[1:]:
                if line.lower().startswith('host:'):
                    host = line.split(':', 1)[1].strip()
                    break
            if host is None and '://' in first_line[1]:
                host = first_line[1].split('://')[1].split('/')[0]

            if host:
                if ':' in host:
                    host, port_str = host.split(':')
                    port = int(port_str)
                target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                target_sock.settimeout(15)
                target_sock.connect((host, port))
                target_sock.sendall(data)
                relay(client_sock, target_sock)
            else:
                client_sock.send(b'HTTP/1.1 400 Bad Request\r\n\r\n')
                client_sock.close()
    except Exception as e:
        log.debug(f"HTTP proxy error from {client_addr}: {e}")
    finally:
        try:
            client_sock.close()
        except:
            pass


def run_http_server():
    """Start HTTP proxy server."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', HTTP_PORT))
    server.listen(50)
    log.info(f"HTTP proxy on 0.0.0.0:{HTTP_PORT}")

    while True:
        try:
            client_sock, addr = server.accept()
            t = threading.Thread(target=handle_http_client, args=(client_sock, addr), daemon=True)
            t.start()
        except Exception as e:
            log.error(f"HTTP accept error: {e}")


# ============================================================
# API helpers
# ============================================================

def get_proxy_status():
    """Get proxy server status."""
    upstream = get_upstream()
    return {
        "socks5": f"0.0.0.0:{SOCKS5_PORT}",
        "http": f"0.0.0.0:{HTTP_PORT}",
        "upstream": upstream,
        "active": upstream.get("ip") is not None,
    }


def rotate_upstream():
    """Rotate to a new upstream proxy."""
    success = pick_best_upstream()
    return {"success": success, "upstream": get_upstream()}


# ============================================================
# Start
# ============================================================

def start():
    """Start proxy servers in background threads."""
    # Start proxy listener servers immediately
    threading.Thread(target=run_socks5_server, daemon=True, name="socks5-server").start()
    threading.Thread(target=run_http_server, daemon=True, name="http-server").start()
    log.info("Proxy servers listening: SOCKS5:1080, HTTP:1081")

    # Pick upstream with retries (pool may not be loaded yet)
    def pick_with_retry():
        for attempt in range(12):  # Retry for ~60 seconds
            if pick_best_upstream():
                log.info("Upstream proxy selected successfully")
                return
            log.info(f"Waiting for pool data (attempt {attempt+1}/12)...")
            time.sleep(5)
        log.warning("Could not pick upstream after 60s - pool may be empty")

    threading.Thread(target=pick_with_retry, daemon=True).start()


if __name__ == "__main__":
    # Standalone mode (for testing)
    start()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Shutting down...")
