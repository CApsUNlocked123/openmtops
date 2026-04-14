# Server Installation Guide

This guide walks through deploying OpenMTOps on a Linux VPS using systemd + nginx.

## Prerequisites

- Ubuntu 22.04+ (or equivalent)
- Python 3.10+
- nginx
- certbot (for HTTPS)

---

## 1. Clone the repository

```bash
git clone <repo-url> /home/$USER/openmtops
cd /home/$USER/openmtops
```

## 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Start the app to run the setup wizard

```bash
python app.py
```

Open `http://<your-server-ip>:5000` in a browser. The setup wizard runs automatically and writes credentials to `.env` when complete.

Alternatively, if you already have credentials, copy and fill `.env.example`:

```bash
cp .env.example .env
nano .env   # fill in TELEGRAM_API_APP, TELEGRAM_API_HASH, DHAN_CLIENTID, DHAN_ACCESSTOKEN
```

## 4. Install the systemd service

Edit `deployment/openmtops.service` — replace the three path/user lines with your actual values:

```ini
User=ubuntu
WorkingDirectory=/home/ubuntu/openmtops
ExecStart=/home/ubuntu/openmtops/.venv/bin/python app.py
```

Then install and start:

```bash
sudo cp deployment/openmtops.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable openmtops
sudo systemctl start openmtops
sudo systemctl status openmtops
```

Check logs:

```bash
sudo journalctl -u openmtops -f
# or check the log files:
tail -f /home/ubuntu/openmtops/logs/app.log
```

## 5. Configure nginx

Edit `deployment/nginx.conf` — update `server_name` with your domain and `alias` with your actual `static/` path:

```nginx
server_name your-domain.com;
alias /home/ubuntu/openmtops/static/;
```

Install the config:

```bash
sudo cp deployment/nginx.conf /etc/nginx/sites-available/openmtops
sudo ln -s /etc/nginx/sites-available/openmtops /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## 6. Enable HTTPS with certbot

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

certbot patches the nginx config automatically and sets up auto-renewal.

## 7. Verify

Open `https://your-domain.com`. If the setup wizard has already run, you should see the app. If not, the wizard will guide you through credential entry.

---

## Updating

```bash
cd /home/$USER/openmtops
git pull
source .venv/bin/activate
pip install -r requirements.txt   # only needed if requirements changed
sudo systemctl restart openmtops
```

Credentials in `config.json` and `.env` are gitignored — they are not affected by `git pull`.

---

## Logs directory

The service writes logs to `logs/app.log` and `logs/error.log`. Create the directory if it doesn't exist:

```bash
mkdir -p /home/ubuntu/openmtops/logs
```

systemd also captures output via `journalctl -u openmtops`.
