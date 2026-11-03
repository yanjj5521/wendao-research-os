#!/usr/bin/env bash
# 问道科研 · Headscale 中心一键部署（v2，2026-11-03）
#
# 用法（在 deploy/headscale/ 目录下）：
#   MODE=sslip     SERVER_IP=203.0.113.7 ./deploy.sh            # 推荐：无域名，Let's Encrypt 真证书
#   MODE=domain    SERVER_IP=203.0.113.7 DOMAIN=vpn.example.com ./deploy.sh
#   MODE=selfsigned SERVER_IP=192.168.1.20 ./deploy.sh          # 纯内网：IP SAN 自签 + 客户端导入 CA
#
# 脚本完成的事：渲染 config.yaml / Caddyfile → 生成证书（selfsigned）→
#   docker compose up → 等待健康检查 → 打印成员接入所需的全部信息。
# 幂等：重复运行只会重渲染配置并重建容器，./data 数据库不受影响。
set -euo pipefail

MODE="${MODE:-sslip}"
SERVER_IP="${SERVER_IP:-}"
DOMAIN="${DOMAIN:-}"
HEADSCALE_VERSION="${HEADSCALE_VERSION:-0.29.7}"
BASE="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE"

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m错误：%s\033[0m\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "未找到 docker，请先安装 Docker Engine。"
docker compose version >/dev/null 2>&1 || die "缺少 docker compose 插件。"

[[ -n "$SERVER_IP" ]] || die "请设置 SERVER_IP（服务器对外 IP）。"

case "$MODE" in
  sslip)
    [[ -n "$(command -v curl || true)" ]] || true
    SERVER_URL="https://${SERVER_IP}.sslip.io"
    REQUIRE_PUBLIC_80443=1
    ;;
  domain)
    [[ -n "$DOMAIN" ]] || die "MODE=domain 需要同时设置 DOMAIN。"
    SERVER_URL="https://${DOMAIN}"
    REQUIRE_PUBLIC_80443=1
    ;;
  selfsigned)
    SERVER_URL="https://${SERVER_IP}"
    REQUIRE_PUBLIC_80443=0
    ;;
  *) die "MODE 仅支持 sslip / domain / selfsigned。" ;;
esac

log "模式：$MODE  ·  server_url = $SERVER_URL"

# ---- 1. 渲染配置 ----
mkdir -p config data certs
sed "s|CHANGE_ME_SERVER_URL|${SERVER_URL}|g" config.example.yaml > config/config.yaml
if [[ "$MODE" == "selfsigned" ]]; then
  # headscale 原生 TLS：终止在 8080（宿主机 443 映射进来）
  sed -i \
    -e "s|^tls_cert_path: \"\"|tls_cert_path: /etc/headscale/certs/server.crt|" \
    -e "s|^tls_key_path: \"\"|tls_key_path: /etc/headscale/certs/server.key|" \
    config/config.yaml
fi

# Caddyfile：sslip / domain 模式渲染出实际站点块
if [[ "$REQUIRE_PUBLIC_80443" == "1" ]]; then
  if [[ "$MODE" == "sslip" ]]; then
    HOSTBLOCK="${SERVER_IP}.sslip.io"
  else
    HOSTBLOCK="$DOMAIN"
  fi
  cat > Caddyfile <<EOF
${HOSTBLOCK} {
    reverse_proxy headscale:8080
    encode gzip
}
EOF
fi

# ---- 2. 证书（仅 selfsigned 模式） ----
if [[ "$MODE" == "selfsigned" ]]; then
  if [[ ! -f certs/ca.key ]]; then
    log "生成根 CA（10 年）与 IP SAN 服务端证书（约 825 天）"
    openssl req -x509 -newkey rsa:4096 -nodes -days 3650 \
      -keyout certs/ca.key -out certs/ca.crt \
      -subj "/CN=Wendao Headnet CA" >/dev/null 2>&1
  fi
  openssl req -newkey rsa:2048 -nodes \
    -keyout certs/server.key -out certs/server.csr \
    -subj "/CN=${SERVER_IP}" >/dev/null 2>&1
  openssl x509 -req -in certs/server.csr -CA certs/ca.crt -CAkey certs/ca.key \
    -CAcreateserial -days 825 \
    -extfile <(printf "subjectAltName=IP:%s,DNS:headnet.internal,DNS:%s\nbasicConstraints=CA:FALSE" \
      "$SERVER_IP" "$SERVER_IP") \
    -out certs/server.crt >/dev/null 2>&1
  chmod 600 certs/ca.key certs/server.key
  rm -f certs/server.csr

  # 生成 Windows 成员端 CA 导入脚本（一行命令，免手工）
  {
    echo "# 双击运行或在管理员 PowerShell 中执行：把 Wendao Headnet CA 导入受信任的根证书存储"
    echo "certutil -addstore -f Root \"\$PSScriptRoot\wendao-headnet-ca.crt\""
  } > client-install-ca.ps1
  cp certs/ca.crt client/wendao-headnet-ca.crt 2>/dev/null || { mkdir -p client; cp certs/ca.crt client/wendao-headnet-ca.crt; }
  log "已生成 client/wendao-headnet-ca.crt 与 client-install-ca.ps1（发给成员执行一次即可）"
fi

# ---- 3. 启动 ----
log "拉取镜像并启动（headscale ${HEADSCALE_VERSION}）"
if [[ "$REQUIRE_PUBLIC_80443" == "1" ]]; then
  docker compose --profile caddy up -d --force-recreate
else
  # selfsigned：headscale 直接对外 TLS，443 → 容器 8080
  HEADSCALE_HTTP_BIND=127.0.0.1:8080 TLS_BIND=0.0.0.0:443 DERP_UDP_BIND=3478:3478/udp \
    docker compose up -d --force-recreate
fi

# ---- 4. 健康等待 ----
log "等待健康检查…"
for _ in $(seq 1 30); do
  status="$(docker inspect -f '{{.State.Health.Status}}' wendao-headscale 2>/dev/null || echo starting)"
  [[ "$status" == "healthy" ]] && break
  sleep 2
done
[[ "$status" == "healthy" ]] || die "headscale 未进入 healthy，请查看：docker logs wendao-headscale"

# ---- 5. 输出接入信息 ----
log "部署完成。下一步："
cat <<EOF

  1) 创建用户与预授权密钥（在服务器上执行）：
       docker exec wendao-headscale headscale users create CHANGE_ME_USER
       docker exec wendao-headscale headscale preauthkeys create --user CHANGE_ME_USER --reusable --expiration 24h

  2) 成员接入：
$( [[ "$MODE" == "selfsigned" ]] && echo "       - 先执行 client-install-ca.ps1 导入 CA（每台客户端一次）" )
       - 打开问道科研 → 同行会 → Headscale → 填入：
           控制地址：${SERVER_URL}
           预授权密钥：上面生成的 key

  3) 验证：${SERVER_URL}/health 应返回 ok。

  数据（成员节点、密钥、数据库）都在 ./data —— 升级/重建容器不会丢失。
EOF
