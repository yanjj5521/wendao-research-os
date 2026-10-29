# VPS deployment assets

This folder contains an optional Docker deployment for the lightweight collaboration hub only.

1. Copy the whole project to the VPS.
2. Copy `deploy/.env.example` to `deploy/.env` and set `HUB_DOMAIN`.
3. From `deploy/`, run `docker compose --env-file .env -f docker-compose.hub.yml up -d --build`.
4. Install Caddy on the host, copy `Caddyfile.example` to its configuration, and replace the domain.
5. Keep ports 80/443 public; the hub itself binds only to `127.0.0.1:5050`.
6. Back up `instance/hub.db`, `instance/hub_secret.txt`, and `storage/hub_backups/` off the VPS periodically.

The first start creates `instance/HUB_ADMIN_CREDENTIALS.txt`.
