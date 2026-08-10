# 部署文档

This deployment uses four subdomains:

- `voice.ivaris.top` - customer web
- `merchant.ivaris.top` - merchant web
- `platform.ivaris.top` - platform web
- `api.ivaris.top` - FastAPI and WebSocket endpoints

## 1. DNS

Create four `A` records pointing to the Ubuntu server public IP:

```text
voice.ivaris.top
merchant.ivaris.top
platform.ivaris.top
api.ivaris.top
```

## 2. Ubuntu server

Install Docker Engine with the Compose plugin, Nginx, and Certbot. The GitHub
secret `SERVER_USER` can be any existing user that belongs to the `docker`
group. Creating a dedicated `deploy` user is optional:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx
sudo useradd --create-home --shell /bin/bash deploy
sudo usermod -aG docker deploy
sudo mkdir -p /opt/voice-shopping-agents/deploy
sudo chown -R deploy:deploy /opt/voice-shopping-agents
```

If you already have a suitable account, skip the `useradd` command and run
`sudo usermod -aG docker <your-server-user>` instead. Set `SERVER_USER` to
that account in GitHub Actions.

Copy `production.env.example` to
`/opt/voice-shopping-agents/.env`, replace every placeholder, and protect it:

```bash
sudo chmod 600 /opt/voice-shopping-agents/.env
```

The `deploy` user must be able to SSH in with the private key stored in the
GitHub secret `SERVER_SSH_KEY`.

## 3. GHCR access

If the GitHub Container Registry packages are private, log in once on the
server as the same user configured in `SERVER_USER` (usually `deploy`). Use
a GitHub token with `read:packages`; the token is read interactively and is
not written into shell history:

```bash
read -rsp 'GHCR token: ' GHCR_READ_TOKEN
echo
test -n "$GHCR_READ_TOKEN" || { echo 'GHCR token is empty' >&2; exit 1; }
printf '%s' "$GHCR_READ_TOKEN" | docker login ghcr.io -u ivaris03 --password-stdin
unset GHCR_READ_TOKEN
```

If you prefer to use an environment variable in a non-interactive session,
export it first and verify that it is non-empty without printing the token:

```bash
export GHCR_READ_TOKEN='paste-token-here'
test -n "$GHCR_READ_TOKEN" || exit 1
printf '%s' "$GHCR_READ_TOKEN" | docker login ghcr.io -u ivaris03 --password-stdin
unset GHCR_READ_TOKEN
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
  -d voice.ivaris.top \
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
