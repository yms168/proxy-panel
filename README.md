# ⚡ Unified Proxy Manager v2.0

> 一键搭建住宅代理管理系统 | 3x-ui + 多源代理聚合 + 智能路由面板

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Blog](https://img.shields.io/badge/Blog-零点博客-blue)](https://blog.lingdian168.online/)

---

## 📖 项目简介

Unified Proxy Manager 是一套完整的**住宅代理管理系统**，将以下三个强大工具整合为一个统一面板：

| 组件 | 功能 | 端口 |
|---|---|---|
| **3x-ui** | Xray/V2Ray 核心管理面板，支持多协议、多用户、流量控制 | `2506` |
| **Multi-Source Proxy Aggregator** | 多源代理聚合器，从 4+ 个免费源抓取并测试代理 | `5001` |
| **Unified Panel** | 统一管理面板，一键智能连接、节点池管理、实时监控 | `8888` |

### 🎯 核心功能

- 🌐 **多源代理聚合** — VPN Gate + ProxyScrape + OpenProxyList 等多源并发抓取
- 🏠 **住宅 IP 识别** — 通过 ip-api.com 自动分类住宅/机房 IP
- ⚡ **智能最优连接** — 一键选择延迟最低的住宅 IP 作为出口
- 📊 **实时监控面板** — 出口 IP、延迟、节点数、Xray 流量一目了然
- 🔄 **自动健康检查** — 每 15 分钟自动测试所有节点，剔除失效代理
- 🎛️ **强大筛选** — 按国家、IP 类型、延迟、评分排序和搜索
- 🔌 **RESTful API** — 所有功能提供 JSON API，可编程调用

---

## 🏗️ 架构图

```
┌─────────────────────────────────────────────────────────┐
│              🌐 你的应用 (浏览器/爬虫/脚本)                │
└─────────────────────┬───────────────────────────────────┘
                      │ SOCKS5 / HTTP 代理
                      ▼
┌─────────────────────────────────────────────────────────┐
│           ⚡ Xray Core (3x-ui 管理)                       │
│           入站 → 路由规则 → 出站链                         │
│           端口 2506 (管理面板)                             │
└─────────────────────┬───────────────────────────────────┘
                      │ 出站代理选择
          ┌───────────┼───────────┐
          ▼           ▼           ▼
    ┌─────────┐ ┌─────────┐ ┌─────────┐
    │住宅 IP #1│ │住宅 IP #2│ │机房 IP #N│  ← 代理池
    │🇺🇸 120ms │ │🇯🇵 85ms  │ │🇩🇪 200ms │
    └─────────┘ └─────────┘ └─────────┘
          ▲           ▲           ▲
          └───────────┼───────────┘
                      │
┌─────────────────────────────────────────────────────────┐
│         🔄 Multi-Source Proxy Aggregator (端口 5001)       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ VPN Gate │  │ProxyScrape│  │OpenProxy │  │ 自定义源  │ │
│  │ (免费VPN) │  │(SOCKS5)  │  │  (HTTP)  │  │ (可扩展) │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘ │
│       健康检查 → 延迟排序 → 住宅IP标记 → 自动剔除         │
└─────────────────────────────────────────────────────────┘
                      │
┌─────────────────────────────────────────────────────────┐
│           🎛️ Unified Panel (端口 8888)                    │
│     一键智能连接 · 节点池管理 · 实时监控 · 流量统计        │
└─────────────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 前提条件

- **VPS**：Ubuntu 24.04（推荐 2核4G+，40G 硬盘）
- **域名**（可选）：如果有域名可以配置 HTTPS
- **基础知识**：会用 SSH 连接服务器

### 第一步：安装 3x-ui

SSH 连接到你的 VPS，运行：

```bash
bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh)
```

安装完成后：
1. 记下面板 URL、用户名和密码
2. 登录 3x-ui 面板（默认端口 2053 或 54321）
3. 先在 3x-ui 中创建入站规则（SOCKS5 或 HTTP 代理）

> **提示**：安装后可以用 `x-ui settings` 查看面板信息，用 `x-ui setting -username admin -password 你的密码` 修改密码

### 第二步：部署代理聚合器 + 统一面板

在**本地电脑**上克隆项目并运行部署脚本：

```bash
# 1. 克隆项目
git clone https://github.com/YOUR_USERNAME/proxy-panel.git
cd proxy-panel

# 2. 安装本地依赖
pip install paramiko requests

# 3. 一键部署到服务器
python deploy.py <你的服务器IP> <root密码>
```

部署脚本会自动：
- ✅ 上传所有代码到服务器
- ✅ 安装 Python 依赖
- ✅ 创建 systemd 服务（开机自启）
- ✅ 启动代理聚合器和统一面板
- ✅ 验证所有服务运行状态

### 第三步：打开面板

部署完成后，浏览器访问：

```
http://你的服务器IP:8888/
```

你会看到：

- **🌐 当前出口 IP** — 你的服务器公网 IP（首次显示服务器 IP）
- **⏱ 延迟** — 到当前代理的延迟
- **🔢 可用节点** — 自动抓取的代理数量（首次部署约 30-50 个）
- **📊 Xray 流量** — 从 Xray 读取的累计流量

### 第四步：使用智能连接

1. 点击 **🔄 抓取最新节点** — 首次部署后等待 30 秒让系统自动抓取
2. 点击 **🎯 一键智能最优连接** — 自动选择延迟最低的住宅 IP
3. 在 3x-ui 面板中配置出站规则，将流量路由到选中的代理

---

## 📋 使用指南

### Web 面板功能

#### 仪表盘
- 实时显示当前出口 IP、延迟、可用节点数、Xray 流量
- 每 30 秒自动刷新

#### 最优候选节点
- 按「住宅 IP 优先 + 最低延迟」排序
- 显示 Top 10 最佳代理
- 点击「一键智能最优连接」自动选择 #1

#### 节点池管理
- **国家筛选** — 按国家过滤代理
- **类型筛选** — 住宅 IP vs 机房 IP
- **排序方式** — 按延迟 / 评分 / 类型排序
- **搜索框** — 搜索 IP、位置、ISP
- 每个节点显示：状态、延迟、IP:端口、位置、ISP、类型、协议、评分

### API 参考

#### Proxy Pool API (端口 5001)

```bash
# 获取代理池状态
curl http://服务器IP:5001/api/status

# 列出所有存活代理
curl http://服务器IP:5001/api/proxies?alive_only=true&limit=50

# 获取最优代理（住宅优先）
curl http://服务器IP:5001/api/proxies/best?count=5

# 按国家筛选
curl "http://服务器IP:5001/api/proxies?country=US&ip_type=residential"

# 触发抓取
curl -X POST http://服务器IP:5001/api/fetch

# 删除指定代理
curl -X DELETE http://服务器IP:5001/api/proxies/1.2.3.4:1080
```

#### Unified Panel API (端口 8888)

```bash
# 综合状态（代理池 + Xray）
curl http://服务器IP:8888/api/unified/status

# 节点列表（带筛选和排序）
curl "http://服务器IP:8888/api/unified/nodes?country=JP&sort=latency"

# 最优节点
curl "http://服务器IP:8888/api/unified/best?count=10"

# 智能连接
curl -X POST http://服务器IP:8888/api/unified/smart-connect

# 刷新代理池
curl -X POST http://服务器IP:8888/api/unified/refresh
```

### 3x-ui 面板

3x-ui 面板地址（部署时安装）：`http://服务器IP:2506/你的路径/`

在 3x-ui 中可以：
- 创建入站代理（VMess、VLESS、SOCKS5、HTTP 等）
- 管理出站规则（将流量路由到代理池中的节点）
- 查看流量统计
- 配置 TLS 证书
- 多用户管理

> **推荐**：在 3x-ui 中创建一个 SOCKS5 入站（端口如 10808），然后在出站中配置「通过代理池节点」路由。

---

## 🔧 高级配置

### 添加自定义代理源

编辑 `/opt/proxy-pool/server.py`，在 `run_fetch_cycle()` 函数中添加新的 fetcher：

```python
def fetch_my_custom_source() -> list[dict]:
    """从你的自定义源抓取代理"""
    results = []
    # 你的抓取逻辑
    results.append({
        "ip": "1.2.3.4",
        "port": 1080,
        "protocol": "socks5",
        "country": "US",
        "source": "my-source"
    })
    return results

# 然后在 run_fetch_cycle() 中添加：
# threading.Thread(target=lambda: all_raw.extend(fetch_my_custom_source())),
```

### 接入付费住宅代理

如果你有付费代理（如 Bright Data、IPRoyal、Oxylabs），可以在代理池中添加：

```python
def fetch_paid_proxies() -> list[dict]:
    """从付费代理服务获取住宅 IP"""
    results = []
    # 调用付费服务的 API
    api_response = requests.get(
        "https://api.your-proxy-provider.com/proxies",
        headers={"Authorization": "Bearer YOUR_API_KEY"}
    )
    for p in api_response.json():
        results.append({
            "ip": p["ip"],
            "port": p["port"],
            "protocol": "socks5",
            "country": p.get("country", "Unknown"),
            "source": "paid-residential"
        })
    return results
```

### 修改刷新间隔

编辑 systemd service 或修改代码中的 `time.sleep(900)`（单位：秒，默认 15 分钟）。

### Webhook 通知

可以在 `run_fetch_cycle()` 末尾添加 webhook 通知：

```python
# 在 pool.save() 之后
if tested > 0:
    requests.post("https://your-webhook-url", json={
        "event": "fetch_complete",
        "new_proxies": tested,
        "total_alive": len([p for p in pool.get_all() if p.alive])
    })
```

---

## 📁 项目结构

```
proxy-panel/
├── README.md                  # 本文档
├── deploy.py                  # 一键部署脚本
├── proxy-pool/                # 多源代理聚合器
│   ├── server.py              #   主程序（API + 抓取 + 测试 + 后台循环）
│   └── requirements.txt       #   Python 依赖
├── unified-panel/             # 统一管理面板
│   └── server.py              #   主程序（API + Dashboard HTML）
└── install-3xui.sh            # 3x-ui 安装参考脚本
```

---

## 🛠️ 运维命令

```bash
# 查看服务状态
systemctl status proxy-pool
systemctl status unified-panel
systemctl status x-ui

# 查看日志
journalctl -u proxy-pool -f       # 实时日志
journalctl -u proxy-pool -n 50    # 最近 50 行
journalctl -u unified-panel -f

# 重启服务
systemctl restart proxy-pool
systemctl restart unified-panel

# 手动触发代理抓取
curl -X POST http://127.0.0.1:5001/api/fetch

# 检查代理池
curl http://127.0.0.1:5001/api/status | python3 -m json.tool

# 重设 3x-ui 密码
sqlite3 /etc/x-ui/x-ui.db "UPDATE users SET username='admin', password='你的bcrypt哈希' WHERE id=1"
systemctl restart x-ui
```

---

## ❓ 常见问题

### Q: 为什么代理数量不稳定？

A: 免费代理（如 ProxyScrape、OpenProxyList）的可用性波动很大。代理池每 15 分钟自动刷新并剔除死节点。如果数量骤降，可以手动点「抓取最新节点」。

### Q: 如何确保代理真的是住宅 IP？

A: 我们使用 ip-api.com 的数据判断 — 如果 IP 被标记为 `proxy=true` 且 `hosting=false`，则归类为住宅 IP。但这并非 100% 准确。要确保真实住宅 IP，建议接入付费代理服务。

### Q: 面板打不开？

A: 检查防火墙规则：
```bash
ufw allow 8888/tcp
ufw allow 5001/tcp
ufw allow 2506/tcp
```

### Q: Xray 显示 "Down"？

A: 检查 Xray 状态：
```bash
systemctl status x-ui
x-ui status
```

### Q: 磁盘空间不够？

A: 清理 Docker 垃圾和 apt 缓存：
```bash
docker system prune -af
apt-get clean
```

### Q: 如何升级？

A: 重新运行部署脚本即可覆盖更新：
```bash
python deploy.py 服务器IP 密码
```

---

## 🔗 相关链接

- 📝 [作者博客 - 零点博客](https://blog.lingdian168.online/)
- 🔧 [3x-ui 项目](https://github.com/mhsanaei/3x-ui)
- 📡 [Xray 核心](https://github.com/XTLS/Xray-core)
- 🌐 [VPN Gate](https://www.vpngate.net/)
- 🔍 [ip-api.com](https://ip-api.com/)

---

## 📜 License

MIT License — 自由使用、修改和分发。

---

## ⭐ Star History

如果这个项目对你有帮助，请给一个 Star ⭐

---

*Made with ❤️ by [零点博客](https://blog.lingdian168.online/)*
