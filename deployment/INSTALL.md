# VPS Deployment Guide — Docker + Nginx + SSL

Tested on: **Ubuntu 24.04 LTS**, Hostinger KVM VPS (2 CPU / 8 GB RAM)

---

## Prerequisites

- A VPS with a public IP and root SSH access
- One or more domains with DNS managed via your registrar
- Docker + Docker Compose installed on the VPS
- `rsync` installed on your local machine (`sudo apt install rsync`)

---

## Phase 1 — DNS

In your domain registrar, add A records for every domain pointing to your VPS IP:

| Type | Name | Value |
|------|------|-------|
| A | `@` | `<your-vps-ip>` |
| A | `www` | `<your-vps-ip>` |

> DNS propagation takes 10–30 minutes. Do this first.

---

## Phase 2 — Install Docker on the VPS

**Hostinger users:** use the Docker Manager panel → Install. Docker + Docker Compose are installed automatically.

**Other VPS providers:**
```bash
apt update && apt install -y docker.io docker-compose-plugin
systemctl enable --now docker
```

---

## Phase 3 — SSL Certificates

```bash
ssh root@<your-vps-ip>
apt install -y certbot
```

Get certs for all your domains. Port 80 must be free (stop system nginx/apache first if running):

```bash
certbot certonly --standalone \
  -d YOUR_PRIMARY_DOMAIN.com -d www.YOUR_PRIMARY_DOMAIN.com \
  -d YOUR_SECONDARY_DOMAIN.com -d www.YOUR_SECONDARY_DOMAIN.com \
  --agree-tos -m your@email.com --non-interactive
```

> If system nginx is already running on port 80, use `--nginx` instead of `--standalone`.
> If certbot times out, ensure ports 80 and 443 are open in your VPS firewall panel.

Certs are saved to `/etc/letsencrypt/live/YOUR_PRIMARY_DOMAIN.com/`.

---

## Phase 4 — Configure Nginx

Edit `nginx/nginx.conf` — replace the placeholder domain names with your actual domains:

```
YOUR_PRIMARY_DOMAIN.com    → your main domain (e.g. example.in)
YOUR_SECONDARY_DOMAIN.com  → your secondary domain (e.g. example.com)
```

---

## Phase 5 — Transfer Code to VPS

Run from your **local machine**. Transfers everything including secrets:

```bash
rsync -avz \
  --exclude='venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  /path/to/openmtops/ \
  root@<your-vps-ip>:/opt/openmtops/
```

---

## Phase 6 — Build & Launch

```bash
ssh root@<your-vps-ip>
cd /opt/openmtops
mkdir -p data
docker compose build
docker compose up -d
```

Check containers:
```bash
docker compose ps
docker compose logs -f app
```

---

## Phase 7 — Auto-renew SSL

```bash
(crontab -l 2>/dev/null; echo "0 3 * * * certbot renew --quiet && docker compose -f /opt/openmtops/docker-compose.yml restart nginx") | crontab -
```

---

## Phase 8 — Dhan API Whitelist

Whitelist your VPS public IP in the Dhan partner portal → API settings. Without this the Dhan WebSocket and REST API will reject connections from the server.

---

## Verification

```bash
curl -I https://YOUR_PRIMARY_DOMAIN.com        # 200 OK
curl -I https://YOUR_SECONDARY_DOMAIN.com      # 301 → YOUR_PRIMARY_DOMAIN.com
curl -I http://YOUR_PRIMARY_DOMAIN.com         # 301 → https://
```

Open `https://YOUR_PRIMARY_DOMAIN.com` in a browser. The dashboard loads if `config.json` has credentials, otherwise the setup wizard runs.

---

## Updating the App

From your local machine, rsync again after changes:

```bash
rsync -avz \
  --exclude='venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  /path/to/openmtops/ \
  root@<your-vps-ip>:/opt/openmtops/
```

Then rebuild on the VPS:

```bash
ssh root@<your-vps-ip>
cd /opt/openmtops
docker compose build --no-cache
docker compose up -d
```

---

## Useful Commands

```bash
docker compose logs -f app                          # live app logs
docker compose restart app                          # restart without rebuild
docker compose build --no-cache && docker compose up -d   # full rebuild
docker compose down                                 # stop everything
docker compose exec app bash                        # shell in container
```

---

## Troubleshooting

**Port 80/443 already in use**
Stop system nginx/apache — this does not affect your VPS dedicated IP:
```bash
systemctl stop nginx && systemctl disable nginx
```

**Werkzeug production crash**
Ensure `app.py` has `allow_unsafe_werkzeug=True` in the `socketio.run()` call — this is set in the current codebase.

**Dhan WebSocket not connecting**
Confirm VPS IP is whitelisted in Dhan API portal. Tokens expire annually — regenerate on the Dhan portal and update via Profile → Settings → Dhan.

**Telegram session errors**
`anon.session` must exist at `/opt/openmtops/anon.session`. If missing, rsync it from local or re-authenticate via Profile → Settings → Telegram.
