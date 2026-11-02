# 同行会跨局域网、VPN 与有线/无线连接

> 适用版本：1.8.2 起。

## 一、先明确边界

同行会服务会默认绑定 `0.0.0.0`，因此同一个进程会监听本机的有线网卡、无线网卡和 VPN 虚拟网卡。应用负责：

- 在所有这些网卡上接受 HTTP 连接；
- 自动列出有线、无线、VPN 和局域网的成员入口；
- 允许管理员填写固定的 VPN 域名、VPN IP 或 HTTPS 公网入口；
- 让邀请码注册链接使用这个可分享入口；
- 支持 Tailscale/WireGuard 常用的 `100.64.0.0/10` VPN 地址。

应用不会自行创建 VPN，也不能绕过 Windows 防火墙或 NAT。跨局域网能否连通，取决于：

1. 所有成员是否在同一个 VPN 虚拟网络中；
2. VPN 是否允许成员访问中心的虚拟 IP 和端口；
3. 中心主机防火墙是否允许入站 TCP；
4. 反向代理使用 HTTPS 时，Host 与转发头是否正确。

## 二、三种推荐拓扑

> 如果希望完全由程序内置虚拟网络、不要求成员安装系统 VPN，请使用 [自建 Headscale 指南](HEADSCALE.md)。局域网与 VPN 网卡模式仍是轻量备选。

### A. 同一房间、同一路由器：有线 + 无线

管理员在“同行会”页面启动本机中心，成员使用“有线局域网”或“无线局域网”地址，例如：

```text
http://192.168.1.20:5050
```

主机可以接有线网，成员可以接 Wi-Fi；只要路由器允许同一局域网内互访即可。

Windows 首次启动时如果弹出防火墙提示，应允许**专用网络**访问。也可以以管理员身份执行：

```powershell
New-NetFirewallRule `
  -DisplayName "问道科研 同行会 5050" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 5050 `
  -Profile Private
```

如果更换了端口，把 `5050` 改为实际端口。

### B. 跨局域网：同一 VPN 虚拟网络

推荐使用 Tailscale、WireGuard、ZeroTier 或单位 VPN，让所有成员加入同一虚拟网络。管理员启动中心后，在“同行会 → 管理控制台 → 成员连接地址”里选择 **VPN / 虚拟局域网** 地址。

例如：

```text
http://10.8.0.2:5050
http://100.64.0.10:5050
```

如果 VPN 自动分配的地理解析不到，管理员可以在“固定成员入口”填写 VPN 分配的稳定 IP 或内部域名：

```text
http://100.64.0.10:5050
https://hub.vpn.example.com
```

VPN 网卡被 Windows 标为“公用网络”时，Private 防火墙规则可能不生效。只应在确认该 VPN 网络可信时，为该网卡或端口增加 Public 规则；不要把普通公网 Wi-Fi 当成可信网络。

### C. 公网访问：只允许 HTTPS

不建议把同行会直接以 HTTP 暴露到公网，因为登录密码和 API Token 会明文经过网络。公网场景应由 Caddy、Nginx、Cloudflare Tunnel 或其他反向代理提供 HTTPS。

反向代理到本机端口时设置：

```text
HUB_TRUST_PROXY=1
HUB_HTTPS_ONLY=1
```

如果启用了 `HUB_ALLOWED_HOSTS`，必须把公网域名和 VPN 访问地址写入允许列表。默认不设置该变量，不限制 Host。

也可以用环境变量预先提供固定入口：

```text
HUB_PUBLIC_URL=https://hub.example.com
```

如果 VPN 使用自定义内部域名但又不是 `.local`、`.lan`、`.internal`、`.home.arpa`、`.ts.net` 等已知后缀，可显式允许它使用 HTTP：

```text
HUB_ALLOW_HTTP_HOSTS=hub.vpn.example.com
```

## 三、在程序里配置

1. 打开个人端“同行会/联机”页面。
2. 点击“启动联机中心”。
3. 页面列出所有检测到的有线、无线和 VPN 地址。
4. 成员先用 `http://地址:端口/health` 测试能否访问。
5. 管理员进入同行会管理控制台，在“成员连接地址”确认注册基址。
6. 如果自动地址不适合，填写“固定成员入口”；保存后邀请码的“打开注册页”会使用该地址。
7. 把邀请码或注册链接发给成员。

注册表单始终使用与当前网页同源的相对提交地址；因此只要成员能打开 VPN/局域网入口，注册、登录和后续请求就会继续走同一个可达地址，不需要额外开启 CORS。

## 四、故障排查

在成员电脑上执行：

```powershell
Test-NetConnection 100.64.0.10 -Port 5050
Invoke-WebRequest http://100.64.0.10:5050/health
```

在管理员电脑上检查监听端口：

```powershell
Get-NetTCPConnection -LocalPort 5050 -State Listen
```

如果本机能访问 `127.0.0.1:5050`，但成员不能访问：

- 检查 Windows 防火墙入站规则；
- 检查 VPN 是否允许客户端互访；
- 检查路由器是否启用了 AP/客户端隔离；
- 检查地址是否选错（有线、无线和 VPN 地址可能同时存在）；
- 检查 `HUB_ALLOWED_HOSTS` 是否误拦截了域名或 IP。

## 五、隐私与账号安全

- 跨网连接只改变网络入口，不改变数据边界：论文、实验原始数据、笔记和附件仍只保存在个人电脑。
- 注册仍必须使用有效邀请码；管理控制台与邀请码删除仍有管理员权限、CSRF 和审计。
- 公网部署应使用 HTTPS；VPN 场景优先使用 VPN 自身的加密通道。
