# 自建 Headscale 虚拟专网

> 📖 **同办公室混合组网（局域网+虚拟网并用）的零基础操作手册**：`docs/OFFICE-DEPLOY-MANUAL.md`。

> 适用版本：1.8.3 起；部署脚本与无域名方案适用 **1.9.2 起**（`deploy/headscale/` v2）。

> 💡 **同一办公室的同事直连**不需要本方案：优先使用局域网模式，见 `docs/LAN-DEPLOY.md`（最简，5 分钟）。

v1.8.3 把 Headscale 客户端封装进程序（`native/headnet`，基于 tsnet 用户态网络），
成员无需安装系统级 VPN 网卡。中心端把同行会发布到 `100.64.x.x` 虚拟 IP，
成员端只在 `127.0.0.1` 建立代理入口。

## 一、部署方式总览（v2）

| 方式 | 适用 | TLS 证书 | 客户端要做的事 |
| --- | --- | --- | --- |
| **Docker 一键脚本** | 有 Linux 服务器 | 见下表三模式 | 见下表 |
| **systemd 裸机** | 不用 Docker 的服务器 | 同上 | 同上 |

### 无域名三路径（重点）

| 路径 | 控制地址 | 证书来源 | 前提 | 客户端操作 |
| --- | --- | --- | --- | --- |
| **A. sslip.io（推荐）** | `https://<SERVER_IP>.sslip.io` | Let's Encrypt 真证书（Caddy 自动签发） | 80/443 公网可达；**无需购买域名** | 无（系统信任链直接可用） |
| **B. IP 自签（纯内网）** | `https://<SERVER_IP>` | 自建 CA（10 年）+ IP SAN 证书（825 天），headscale 原生 TLS | 无需公网 | **每台客户端导入一次 CA**（Windows：管理员运行 `client-install-ca.ps1`） |
| **C. 自有域名** | `https://你的域名` | Let's Encrypt | 有域名 | 无 |

> 路径 B 的原理：成员端 `headnet` 助手基于 Go/tsnet，TLS 校验走操作系统证书库；
> 把 deploy.sh 生成的 CA 导入系统"受信任的根证书存储"后，`https://<IP>` 即被信任，
> 助手程序本身无需任何改动。CA 10 年有效，重签服务端证书不需要客户端重导。

## 二、Docker 一键部署（推荐）

```bash
# 路径 A：无域名 + 真证书（最省事）
MODE=sslip SERVER_IP=<你的公网IP> ./deploy.sh

# 路径 B：纯内网自签
MODE=selfsigned SERVER_IP=<内网IP> ./deploy.sh

# 路径 C：自有域名
MODE=domain SERVER_IP=<公网IP> DOMAIN=vpn.example.com ./deploy.sh
```

脚本自动完成：渲染 `config/config.yaml` 与 `Caddyfile` → 生成证书（路径 B）→
`docker compose up` → 等待健康检查 → **打印成员接入所需的控制地址、CA 导入脚本与预授权密钥命令**。
幂等可重复执行；`./data`（成员节点、密钥、数据库）不受影响。

验证：

```bash
curl -fsS https://<SERVER_IP>.sslip.io/health     # 或对应控制地址
docker inspect -f '{{.State.Health.Status}}' wendao-headscale
```

## 三、创建用户与预授权密钥

```bash
docker exec wendao-headscale headscale users create wendao
docker exec wendao-headscale headscale preauthkeys create --user wendao --reusable --expiration 24h
```

密钥只在终端显示一次：不要进 Git、聊天记录或公开文档。正式使用建议每人一把。

## 四、在程序中接入

- **中心端**：同行会页 → Headscale 虚拟专网 → 填控制地址 + 中心节点预授权密钥 →
  "作为中心启动" → 得到虚拟地址（如 `http://100.64.0.1:5050`），
  程序自动把它写为固定成员入口，邀请码链接随之更新；
- **成员端**：同页填同一控制地址 + 中心虚拟 IP + 自己的预授权密钥 → "作为成员连接"。
  路径 B 的成员先双击 `client-install-ca.ps1` 导入 CA（每台一次）。

## 五、systemd 裸机托管（不用 Docker）

见 `deploy/headscale/systemd/README.md` + `wendao-headscale.service`。
要点：`Restart=on-failure` 自动拉起、`ProtectSystem=strict` 安全加固、
证书重签 825 天一轮（CA 不变则客户端无感）。与 Docker 模式共享数据格式，
迁移 = 停一侧 → 拷 `/var/lib/headscale` → 启另一侧（`server_url` 不变则成员无感）。

## 六、常见故障

| 现象 | 处理 |
| --- | --- |
| 容器不 healthy | `docker logs wendao-headscale`；多数是 config.yaml 语法或证书路径错误 |
| sslip 模式证书签发失败 | 确认 80/443 公网可达、IP 正确；国内部分网络对 sslip.io 解析不稳定时可改路径 C |
| 成员 TLS 报 x509 错误（路径 B） | 客户端未导入 CA：重跑 `client-install-ca.ps1`（需管理员） |
| 成员能启动但连不上中心 | 检查控制地址与 `server_url` 是否一字不差；跨 NAT 检查 3478/UDP 与 DERP |
| Windows 防火墙提示 | 允许 `headnet` 助手出站即可，不需要开入站 |

## 七、从 v1 配置升级（CHANGE_ME 域名方式 → v2）

**第一步永远是把 `server_url` 定下来**——它是节点身份的一部分，变更会让所有成员重新登录。

| 配置项 | v1（旧） | v2（新） | 说明 |
| --- | --- | --- | --- |
| `server_url` | `https://CHANGE_ME_HEADSCALE_DOMAIN` | 按三路径选定的固定地址 | 升级前先确定，别再动 |
| `tls_cert_path` / `tls_key_path` | 空（Caddy 终结 TLS） | 路径 B 下指向 `/etc/headscale/certs/`；其余为空 | 路径 A/C 仍由 Caddy 终结 |
| `Caddyfile` | `CHANGE_ME_HEADSCALE_DOMAIN` | deploy.sh 渲染；Caddy 移入 `profiles: [caddy]` | 路径 B 不再启动 Caddy |
| compose 健康检查 | 无 | `headscale healthcheck` 每 30s | `restart: unless-stopped` 已有 |
| 数据 | `./data` | 不变，直接沿用 | 成员节点与密钥保留 |

升级步骤（Docker）：

```bash
cd 旧目录 && docker compose down            # 数据在 ./data
cp -r data /备份路径                        # 保险起见备份一次
cd 新目录（deploy/headscale）               # 放入 v2 的 docker-compose.yml / config.example.yaml / deploy.sh
MODE=… SERVER_IP=… ./deploy.sh              # 用与旧部署一致的 server_url 模式渲染
docker exec wendao-headscale headscale users list   # 确认老用户还在
```

成员端：控制地址没变就**什么都不用做**；变了才需要重新登录一次。

## 八、安全边界

- 预授权密钥只保存在本机个人数据库；不进入同步事件、个性化包或发布资产；
- `verify_clients: true` 保持开启：只有预授权注册的节点能进 DERP；
- 控制端必须 HTTPS（三条路径均为 HTTPS），不要把 HTTP 控制端口暴露公网；
- 路径 B 的 CA 私钥（`certs/ca.key`）只存在于服务器，权限 600；泄露需换 CA 并重导全部客户端。
