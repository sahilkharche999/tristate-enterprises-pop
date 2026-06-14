# Production VM Runbook

This runbook targets the existing GCP VM:

- Project: `budgeting-01`
- VM: `budgeting`
- Zone: `us-west2-c`

It assumes:

- temporary SQLite deployment
- boot disk only
- host nginx + Certbot
- Docker Compose app runtime
- Vertex/ADC auth for Gemini in production

## 1. GCP prerequisites

- VM service account should have:
  - `roles/aiplatform.user`
  - `roles/logging.logWriter`
  - `roles/monitoring.metricWriter`
  - `roles/secretmanager.secretAccessor` if secrets are read from Secret Manager
- Vertex/Gemini API must be enabled for project `budgeting-01`
- Firewall should expose only:
  - `80`
  - `443`
- Do not expose `8000`

## 2. SSH into the VM

```bash
gcloud auth login
gcloud config set project budgeting-01
gcloud compute ssh budgeting --zone us-west2-c
```

## 3. Install base packages

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx git
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
newgrp docker
```

## 4. Create boot-disk directories

```bash
sudo mkdir -p /opt/tristate/{app,data/sqlite,data/budget-storage,secrets,backups}
sudo chown -R "$USER":"$USER" /opt/tristate
```

## 5. Pull the app

```bash
cd /opt/tristate/app
git clone https://github.com/sahilkharche999/tristate-enterprises-pop.git .
git checkout testingparser
```

## 6. Create production env

```bash
cp deploy/production.env.example /opt/tristate/secrets/app.env
chmod 600 /opt/tristate/secrets/app.env
```

Edit `/opt/tristate/secrets/app.env` with real values.

Keep these production port values so only host nginx is public:

```bash
BACKEND_PUBLISH_HOST=127.0.0.1
BACKEND_PUBLISH_PORT=8000
FRONTEND_PUBLISH_HOST=127.0.0.1
FRONTEND_PUBLISH_PORT=8080
```

## 7. Build and start the app

```bash
cd /opt/tristate/app
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  --env-file /opt/tristate/secrets/app.env \
  up -d --build
```

## 8. Configure host nginx

Create `/etc/nginx/sites-available/tristate`:

```nginx
server {
    listen 80;
    server_name your-domain.example;

    client_max_body_size 100m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
}
```

Enable it:

```bash
sudo ln -sf /etc/nginx/sites-available/tristate /etc/nginx/sites-enabled/tristate
sudo nginx -t
sudo systemctl reload nginx
```

## 9. Point DNS

Point your domain A record to the VM public IP.

## 10. Enable HTTPS

```bash
sudo certbot --nginx -d your-domain.example
```

## 11. Smoke tests

```bash
curl -I http://127.0.0.1:8000/healthz
curl -I http://127.0.0.1:8000/readyz
curl -I https://your-domain.example
```

Then test in browser:

- login
- refresh/logout
- HOA load
- upload workbook
- upload reserve study
- upload DRE
- generate disclosure package
- download generated artifacts

## 12. Restart validation

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file /opt/tristate/secrets/app.env restart
```

Confirm:

- SQLite data persists
- uploaded files persist
- generated package downloads still work

## 13. Backup guidance

At minimum:

- daily boot-disk snapshots
- app-level backups of:
  - `/opt/tristate/data/sqlite`
  - `/opt/tristate/data/budget-storage`
