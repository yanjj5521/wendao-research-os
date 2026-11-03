# 问道科研 · Headscale 裸机 systemd 托管（不用 Docker 时的选择）

适用：服务器不方便装 Docker，或希望 headscale 直接以系统服务运行。

## 1. 安装 headscale 二进制

```bash
# 版本与 compose 保持一致；升级时只需替换二进制（数据在 /var/lib/headscale）
curl -Lo /usr/local/bin/headscale \
  https://github.com/juanfont/headscale/releases/download/v0.29.7/headscale_0.29.7_linux_amd64
chmod +x /usr/local/bin/headscale
```

## 2. 目录与配置

```bash
mkdir -p /etc/headscale /var/lib/headscale /etc/headscale/certs
cp config.example.yaml /etc/headscale/config.yaml   # 与 Docker 模式同一份模板
# 按上文说明设置 server_url / TLS；selfsigned 模式的证书放到 /etc/headscale/certs/
```

## 3. 服务单元

把 `wendao-headscale.service` 复制到 `/etc/systemd/system/` 后：

```bash
systemctl daemon-reload
systemctl enable --now wendao-headscale
systemctl status wendao-headscale
```

服务特性（已内置在单元里）：
- `Restart=on-failure` 崩溃自动拉起；
- `After=network-online.target` 等待网络就绪；
- 安全加固：`NoNewPrivileges`、`ProtectSystem=strict`、`ProtectHome`、
  `PrivateTmp`、`ReadWritePaths` 仅放行数据目录。

## 4. 证书续期（仅 selfsigned 模式）

CA 有效期 10 年、服务端证书 825 天。重签服务端证书**不需要**客户端重新导入 CA：

```bash
# 重新执行 deploy.sh 的证书段，或手工：
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 825 -extfile <(printf "subjectAltName=IP:SERVER_IP") -out server.crt
systemctl restart wendao-headscale
```

建议把重签步骤放进 `cron`/`systemd timer`，提前 30 天轮换（可再议）。

## 5. 与 Docker 模式的关系

两种托管共享同一份 `config.example.yaml` 模板与数据格式（SQLite），
迁移 = 停容器/服务 → 拷贝 `/var/lib/headscale` → 启动另一侧。
`server_url` 不变则成员节点无感。
