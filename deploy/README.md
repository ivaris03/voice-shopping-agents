# Ubuntu deployment

This deployment uses four subdomains:

- `app.ivaris.top` - customer web
- `merchant.ivaris.top` - merchant web
- `platform.ivaris.top` - platform web
- `api.ivaris.top` - FastAPI and WebSocket endpoints

## 1. DNS

Create four `A` records pointing to the Ubuntu server public IP:

```text
app.ivaris.top
merchant.ivaris.top
platform.ivaris.top
api.ivaris.top
```

## 2. Ubuntu server

Install Docker Engine with the Compose plugin, Nginx, and Certbot. Then create a
dedicated deployment user that belongs to the `docker` group:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx
sudo useradd --create-home --shell /bin/bash deploy
sudo usermod -aG docker deploy
sudo mkdir -p /opt/voice-shopping-agents/deploy
sudo chown -R deploy:deploy /opt/voice-shopping-agents
```

Copy `production.env.example` to
`/opt/voice-shopping-agents/.env`, replace every placeholder, and protect it:

```bash
sudo chmod 600 /opt/voice-shopping-agents/.env
```

The `deploy` user must be able to SSH in with the private key stored in the
GitHub secret `SERVER_SSH_KEY`.

## 3. GHCR access

If the GitHub Container Registry packages are private, log in once on the
server using a GitHub token with `read:packages`:

```bash
echo "$GHCR_READ_TOKEN" | docker login ghcr.io -u ivaris03 --password-stdin
```

## 4. Nginx and HTTPS

Copy `nginx/ivaris.top.conf` to `/etc/nginx/sites-available/ivaris.top.conf`,
enable it, and reload Nginx:

```bash
sudo ln -s /etc/nginx/sites-available/ivaris.top.conf /etc/nginx/sites-enabled/ivaris.top.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

After DNS resolves, issue certificates:

```bash
sudo certbot --nginx \
  -d app.ivaris.top \
  -d merchant.ivaris.top \
  -d platform.ivaris.top \
  -d api.ivaris.top
```

## 5. GitHub configuration

Add these repository secrets:

```text
SERVER_HOST
SERVER_USER
SERVER_SSH_KEY
SERVER_PORT       # optional; defaults to 22
DEPLOY_PATH       # optional; defaults to /opt/voice-shopping-agents
```

Pushes to `main` run validation, build four images, push them to GHCR, upload
the Compose/deploy scripts, run migrations, and restart the application
containers. Database and Redis data remain in Docker volumes.

