# Deployment (single VPS: Hostinger KVM, Hetzner, DigitalOcean…)

Target: Ubuntu 24.04, 2 vCPU / 4 GB RAM (1 vCPU / 2 GB works for private testing), a domain you control.

## 1. Server setup

```bash
ssh root@YOUR_SERVER_IP
adduser deploy && usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy   # reuse your SSH key
# Harden SSH: disable password + root login
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/; s/^#\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh
apt update && apt -y upgrade && apt -y install ufw fail2ban unattended-upgrades git
ufw allow OpenSSH && ufw allow 80,443/tcp && ufw allow 443/udp && ufw --force enable
dpkg-reconfigure -plow unattended-upgrades
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

## 2. Docker

```bash
curl -fsSL https://get.docker.com | sh
usermod -aG docker deploy   # log out/in as deploy afterwards
```

## 3. Domain + DNS

Create an `A` record (and `AAAA` if you have IPv6) for `intel.yourdomain.com` → server IP. Caddy obtains the
Let’s Encrypt certificate automatically on first start, and the certificate needs the record to resolve first.

## 4. App + environment

```bash
sudo mkdir -p /opt/company-intel && sudo chown deploy /opt/company-intel
git clone <your-repo-url> /opt/company-intel && cd /opt/company-intel
cp .env.example .env
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/; s/^JWT_SECRET=.*/JWT_SECRET=$(openssl rand -hex 32)/" .env
nano .env        # DOMAIN, CORS_ORIGINS, SEC_USER_AGENT (real contact), optional API keys
chmod 600 .env
```

## 5. Start (Postgres, backend, worker, frontend, reverse proxy + HTTPS)

```bash
docker compose up -d --build
docker compose ps
docker compose exec api python -m app.tools.seed --sec --nse     # securities master (~13k listings)
curl -s https://intel.yourdomain.com/api/health
```

Optional masters: download BSE “List of Scrips” CSV from bseindia.com in a browser, `docker compose cp` it into
the api container, then `python -m app.tools.seed --bse-csv /app/data/bse.csv`.

## 6. Workers & scheduled jobs

* `worker` container runs queued research jobs and schedules the daily watchlist refresh at `DAILY_REFRESH_HOUR_UTC`.
* Host cron for backups:

```bash
crontab -e
15 2 * * * /opt/company-intel/deploy/backup.sh >> /var/log/ci-backup.log 2>&1
0 4 * * 0  cd /opt/company-intel && docker compose exec -T api python -m app.tools.seed --sec --nse >> /var/log/ci-seed.log 2>&1
```

Offsite copy (recommended): `apt install restic` and push `backups/` to Backblaze B2 / S3 nightly.
Restore test: `docker compose exec -T db pg_restore -U companyintel -d companyintel --clean < backups/db-YYYY-MM-DD.dump`.

## 7. Logs & monitoring

```bash
docker compose logs -f api worker      # structured app logs
docker compose exec caddy tail -f /data/access.log
```

* Docker’s default json-file driver: cap it in `/etc/docker/daemon.json` → `{"log-driver":"json-file","log-opts":{"max-size":"20m","max-file":"5"}}`.
* Uptime: free UptimeRobot / Better Stack check on `https://intel.yourdomain.com/api/health`.
* Errors: add Sentry (free tier) DSN later; job failures are visible in the `jobs` table (`status='failed'`, `error`).

## 8. Updates (zero-drama deploy)

```bash
cd /opt/company-intel && git pull && docker compose up -d --build && docker image prune -f
```

## 9. Security checklist

- [x] Only 22/80/443 open; Postgres not published
- [x] HTTPS + HSTS via Caddy; secure, httpOnly, SameSite=Lax session cookie in prod
- [x] Secrets only in `.env` (chmod 600); never shipped to the browser (Next rewrites `/api` server-side)
- [x] bcrypt password hashing; JWT secret required in prod (app refuses to start otherwise)
- [x] Pydantic validation on every input; ORM parameterised queries (no string SQL)
- [x] Per-client rate limit + daily research quotas (anonymous vs signed-in)
- [x] Non-root containers
- [ ] Before scaling to >1 API host: move rate limiting to Redis

## Local development

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
cp ../.env.example .env   # set DATABASE_URL=sqlite:///./data/dev.db, RUN_JOBS_INLINE=true, SEC_USER_AGENT=...
.venv/bin/python -m app.tools.seed --sec --nse
.venv/bin/uvicorn app.main:app --port 8000
.venv/bin/python -m pytest -q tests
cd ../frontend && npm install && npm run dev      # http://localhost:3000
backend/scripts/e2e_analyze.sh "Kinetic Engineering" 365 research
```
