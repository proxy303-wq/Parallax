# PARALLAX -- one image, six processes.
#
# The same image runs the dashboard and all five workers.  The per-process
# command does NOT live here: the platform supplies it (Kuberns "Procfile
# commands", or the Procfile at the repository root -- see deploy/kuberns.md).
# Nothing in this file is web-specific.
#
# python:3.12-slim on purpose.  Production runs 3.12; building on 3.11 here
# would let a 3.12-only syntax error reach the box before anyone saw it.  Never
# bump this without bumping the box first.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Kolkata

WORKDIR /app

# ca-certificates -- every broker, Telegram and LLM call is HTTPS, and the Dhan
#   scrip master is fetched over TLS.
# tzdata -- zoneinfo ships no database on slim, and the market-hours logic is
#   IST, so TZ=Asia/Kolkata above would silently fall back to UTC without it.
# curl -- operator debugging: docker exec <id> curl -fsS localhost:8000/api/state
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata curl \
 && rm -rf /var/lib/apt/lists/*

# Requirements first, so a source-only edit reuses this layer instead of
# reinstalling pandas and numpy on every deploy.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# .dockerignore keeps .env, .dhan_token*, parallax.db, data/ and the 900 MB
# research/ tree out of this context.  If that file is ever weakened, secrets
# land in this layer.
COPY . .

# Non-root.  /app must stay owned by the app user: the Dhan token cache
# (.dhan_token.txt) and, when PARALLAX_DB_URL is unset, the SQLite journal are
# written into the working directory.  A read-only /app makes the token write
# fail, and then every worker re-authenticates -- Dhan invalidates the previous
# token, so the workers take turns killing each other's session.  It looks like
# a Dhan outage and it is not one.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin parallax \
 && chown -R parallax:parallax /app
USER parallax

# Informational.  The real port is whatever $PORT the platform injects; this is
# only the fallback used when nothing is injected.
EXPOSE 8000

# Neutral default: the web process.  Override the command per service for the
# five workers (Procfile / deploy/kuberns.md).
#
# Deliberately NO HEALTHCHECK here.  One image serves six processes, so an
# image-level HEALTHCHECK would mark all five worker containers unhealthy, and
# on a Swarm topology (Kuberns "master node") Swarm would replace those tasks in
# a loop.  The web healthcheck belongs in docker-compose.yml, where it can be
# scoped to the one service that actually speaks HTTP.
CMD ["sh", "-c", "exec uvicorn parallax.web.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
