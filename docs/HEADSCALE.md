# 自建 Headscale 虚拟专网

> 适用版本：1.8.3 起。

v1.8.3 把 Headscale 客户端封装进程序，成员无需安装系统级 VPN 网卡。程序使用 `tsnet` 用户态网络助手：

- 中心端：把同行会发布到 Headscale 分配的 `100.64.x.x` 虚拟 IP；
- 成员端：在本机回环地址建立代理，通过虚拟网络访问中心；
- 有线、无线、不同局域网都可以使用，只要双方都加入同一个 Headscale 网络。

## 一、服务端条件

Headscale 是控制端，必须有公网可访问服务器。推荐至少 1 核 1 GiB 内存的 Linux VPS，并准备一个域名：

```text
headscale.example.com  A    你的公网 IPv4
headscale.example.com  AAAA 你的公网 IPv6（可选）
```

防火墙/安全组至少开放：

| 端口 | 协议 | 用途 |
| --- | --- | --- |
| 80 | TCP | Caddy 申请 TLS 证书 |
| 443 | TCP | Headscale 控制端 HTTPS |
| 3478 | UDP | 内置 DERP/STUN 中继（重要） |

如果 3478/UDP 被封，跨 NAT 时可能无法中继；Headscale 必须配置可用的 DERP。生产服务器必须使用有效 HTTPS，不能把控制端的 HTTP 直接暴露到公网。

## 二、Docker Compose 部署

仓库提供示例：

```text
deploy/headscale/docker-compose.yml
deploy/headscale/config.example.yaml
deploy/headscale/Caddyfile.example
```

在服务器上执行：

```bash
mkdir -p wendao-headscale/config
cd wendao-headscale
# 将 docker-compose.yml、config.example.yaml、Caddyfile.example 放到当前目录
cp config.example.yaml config/config.yaml
cp Caddyfile.example Caddyfile
```

编辑 `config/config.yaml` 和 `Caddyfile`，把所有 `CHANGE_ME_HEADSCALE_DOMAIN` 替换为真实域名。然后：

```bash
docker compose up -d
docker compose logs -f headscale
```

检查服务：

```bash
curl -fsS https://headscale.example.com/health
```

## 三、创建用户和预授权密钥

在服务器执行：

```bash
docker compose exec headscale \
  headscale users create wendao

docker compose exec headscale \
  headscale users list

docker compose exec headscale \
  headscale preauthkeys create --user 1 --reusable --expiration 30d
```

命令输出的 `preauthkey` 只在终端显示，不要提交到 Git、聊天记录或公开文档。正式使用建议每个成员单独生成一把密钥；测试时可以暂时使用一把短期 reusable key。

## 四、在程序中建立虚拟网络

### 中心端

1. 打开问道科研“同行会”页。
2. 在“Headscale 虚拟专网”中填写控制端地址。
3. 粘贴中心节点的预授权密钥。
4. 节点名称填例如 `wendao-hub`。
5. 点击“作为中心启动”。
6. 成功后页面会显示中心虚拟地址，例如：

```text
http://100.64.0.1:5050
```

程序会把这个虚拟地址写为同行会固定成员入口，之后生成邀请码注册链接会自动使用它。中心端本机仍保留 `127.0.0.1` 管理入口。

### 成员端

每个成员都需要：

1. 自己的 Headscale 预授权密钥；
2. 在“Headscale 虚拟专网”中填写同一个控制端地址；
3. 填写中心节点启动后显示的虚拟 IP；
4. 点击“作为成员连接”。

成功后页面显示：

```text
本机入口：http://127.0.0.1:<动态端口>
```

点击“打开虚拟网络同行会”即可登录或使用邀请码注册。程序会在本机建立仅绑定 `127.0.0.1` 的代理，不会把成员本机端口暴露给局域网。

## 五、常见故障

### 中心启动失败

- 检查控制端地址是否能打开；
- 检查预授权密钥是否过期、是否已被使用；
- 检查 Headscale 日志和 DERP/STUN 端口；
- 检查中心节点是否已经启动了同行会中心。

### 成员能启动但打不开中心

- 确认中心端“作为中心启动”已成功；
- 确认成员填写的是中心虚拟 IP，而不是中心局域网 IP；
- 检查 Headscale ACL 是否允许节点互访；
- 检查服务器 3478/UDP 和 DERP 状态；
- 在中心端查看 `headscale nodes list` 是否两个节点都 online。

### Windows 提示防火墙

tsnet 用户态助手不需要安装系统 VPN 网卡，但中心本机仍需允许同行会 TCP 端口被本机代理访问。它通常只访问 `127.0.0.1`；跨网络流量由 Headscale/DERP 处理。

## 六、安全边界

- Headscale 控制端和 DERP 是你自己的基础设施；备份 `data/` 目录中的 SQLite、私钥和 ACL。
- 预授权密钥等同入网凭据，必须按成员分发并定期轮换。
- 本程序把密钥保存在本机个人数据库，不上传 GitHub、不进入个性化包、不进入科研同步事件。
- 科研论文、实验数据、笔记和附件仍只保存在个人数据库；虚拟网络只承载账号、注册、论坛、聊天和虚拟资产等同行会流量。
- 公网 HTTP 不安全；Headscale 控制端必须 HTTPS，同行会虚拟地址建议仅走 Headscale 加密网络。
