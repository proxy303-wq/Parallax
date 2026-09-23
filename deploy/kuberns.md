# Deploying PARALLAX to Kuberns

This is the operator runbook for running the six production processes on
[Kuberns](https://kuberns.com). Everything in the sections below comes from Kuberns'
own documentation, with the URL next to it. Anything I could not confirm is listed
under **Not verified** at the end rather than guessed at.

---

## 1. Shape of the deployment

Kuberns organises everything as **Project -> Service -> Environment**, and the
*environment* owns the branch, the deployment configuration, the environment
variables, the resources and the domains
([application-structure](https://docs.kuberns.com/docs/guides/application-structure)).

So PARALLAX is:

~~~
Project   parallax
  Service parallax            (github.com/proxy303-wq/Parallax)
    Environment main          <- branch main, all six processes, one Postgres
~~~

**One service, six processes.** Do not create six services. Additional services and
environments are blocked during the free trial
([trial-restrictions](https://docs.kuberns.com/docs/billing/trial-restrictions)), and
multi-process in one environment is the shape Kuberns documents: its Flask tutorial
tells you to add a background worker by *extending the Procfile*
([tutorials/flask](https://docs.kuberns.com/docs/tutorials/flask)).

### Multi-process is declared in a `Procfile` at the repository root

This is the one part of the Kuberns configuration that lives in the repository. Both
Python tutorials use it, with a `name: command` line per process:

~~~
web:    gunicorn app:app --bind 0.0.0.0:$PORT
worker: celery -A app.celery worker -l info
~~~

([tutorials/flask](https://docs.kuberns.com/docs/tutorials/flask),
[tutorials/django](https://docs.kuberns.com/docs/tutorials/django))

The `Procfile` committed at the repository root declares all six processes:

~~~
web: uvicorn parallax.web.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips="*"
live: python -m parallax.apps.worker.live_runner
options: python -m parallax.apps.worker.options_supervisor
crypto: python -m parallax.apps.worker.crypto_smc --poll 60
crypto-eth: python -m parallax.apps.worker.crypto_smc --symbol ETHUSD --poll 60
chat: python -m parallax.apps.worker.telegram_chat
~~~

The `Procfile` is deliberately comment-free: the parser is closed-source and I could
not confirm it tolerates comments, so there are none to trip over.

The same six values also appear in the dashboard field **Procfile commands**
([deployment-configuration](https://docs.kuberns.com/docs/guides/deployment-configuration)).
The AI agent detects and pre-fills them; if it gets one wrong, correct it there. If the
repo `Procfile` is not picked up, paste these lines into that field instead.

---

## 2. The six processes

| # | Kuberns process | Command | Public URL |
|---|---|---|---|
| 1 | `web` | `uvicorn parallax.web.app:app --host 0.0.0.0 --port $PORT` | yes - the only HTTP process |
| 2 | `live` | `python -m parallax.apps.worker.live_runner` | no |
| 3 | `options` | `python -m parallax.apps.worker.options_supervisor` | no |
| 4 | `crypto` | `python -m parallax.apps.worker.crypto_smc --poll 60` | no |
| 5 | `crypto-eth` | `python -m parallax.apps.worker.crypto_smc --symbol ETHUSD --poll 60` | no |
| 6 | `chat` | `python -m parallax.apps.worker.telegram_chat` | no |

These are the same six commands the systemd units under `deploy/` run on the VPS; the
`ExecStart` lines there are the source of truth for the flags (`--poll 60`,
`--symbol ETHUSD`).

### The trap that will bite you

> A worker with no resource on the environment simply does not run, and nothing
> reports an error when it does not.

[tutorials/flask](https://docs.kuberns.com/docs/tutorials/flask)

Attaching the background-worker resource is a *separate step* from declaring the
process. A missing resource produces no error and no log line - the process is simply
absent. After the first deploy, confirm all five workers actually appear as running
resources. Do not assume a green build means they are up.

### Process names

`crypto-eth` uses a hyphen, matching the systemd unit `parallax-crypto-eth`. The
documented examples only ever use `web` and `worker`, so the accepted character set
is not confirmed. If the platform rejects the name, rename it to `cryptoeth` in both
the `Procfile` and the dashboard field - nothing else depends on the spelling.

## 3. Environment variables

Environment variables belong to the **environment** (not the repo), and **saving one
triggers a redeploy** - so add them all before the first real deploy
([environment-variables](https://docs.kuberns.com/docs/guides/environment-variables)).

Enter these under *Environment Variables*. The dashboard also accepts an uploaded
`.env` file, which is quicker than retyping them
([tutorials/flask](https://docs.kuberns.com/docs/tutorials/flask)). `.env.example` in
the repository root is the same list with blank values;
`.env` itself is gitignored and must never be committed.

| Variable | Used by | Notes |
|---|---|---|
| `DHAN_CLIENT_ID` | web, live, options, crypto, crypto-eth | Dhan broker account id |
| `DHAN_ACCESS_TOKEN` | same | expires daily |
| `DHAN_PIN` | same | with the TOTP secret, mints a fresh access token |
| `DHAN_TOTP_SECRET` | same | TOTP seed |
| `PARALLAX_TELEGRAM_BOT_TOKEN` | chat, live | |
| `PARALLAX_TELEGRAM_CHAT_ID` | chat, live | |
| `DEEPSEEK_API_KEY` | web, live | `parallax/core/brain/llm.py` |
| `TYPESAFE_API_KEY` | web, live | `parallax/core/brain/typesafe.py` |
| `DELTA_API_KEY` | crypto, crypto-eth | Delta Exchange India |
| `DELTA_API_SECRET` | crypto, crypto-eth | Delta Exchange India |
| `PARALLAX_DB_URL` | **all six** | Postgres DSN - see below |

### `PARALLAX_DB_URL` is the one that matters most

~~~
postgres://USER:PASSWORD@HOST:5432/DBNAME
~~~

When set, the journal, the open-position state and the Dhan access token move to
Postgres (`parallax/adapters/db.py`); when unset they fall back to `parallax.db`
and `.dhan_token.txt`. That fallback is fine on one box and **actively wrong on
Kuberns**, where each process is its own container with its own ephemeral filesystem:

* every container boots with no token file, mints its own Dhan token, and Dhan
  invalidates the previous one - so the five trading processes take turns killing each
  other's session. It looks like a Dhan outage and it is not one.
* the journal and the open positions are wiped on every redeploy, so a live condor is
  forgotten and never exited.

**Kuberns does not document injecting this for you.** Its Postgres datastore Overview
page exposes *database name, username, password and hostname*, and both tutorials then
tell you to build the URL yourself and save it as an environment variable
([datastores](https://docs.kuberns.com/docs/datastores),
[tutorials/flask](https://docs.kuberns.com/docs/tutorials/flask)). Construct it by hand
rather than waiting for a `DATABASE_URL` to appear.

Both `postgres://` and `postgresql://` schemes are accepted by this codebase - it
normalises the scheme for psycopg2 - so either form from the dashboard will work.

---

## 4. Datastore

1. Open the environment, go to **Resources**, and add a **PostgreSQL** datastore
   ([datastores](https://docs.kuberns.com/docs/datastores)).
2. Open the datastore's **Overview** page and read off database name, username,
   password and hostname.
3. Build `PARALLAX_DB_URL` from those values and save it as an environment variable on
   the service environment.

Kuberns can take a backup on demand or on a schedule, but **it will not restore one for
you** - a backup is a dump you download and load with `psql` yourself. Verify a restore
into a local database *before* you need it
([datastores](https://docs.kuberns.com/docs/datastores)). The local Postgres in
`docker-compose.yml` is a convenient target for that drill.

## 5. Dashboard settings to check after the agent proposes a config

Kuberns' AI agent analyses the repository and proposes the build config; you review and
edit it under **Build & Deployment**
([deployment-configuration](https://docs.kuberns.com/docs/guides/deployment-configuration),
[reference/builds-and-deployments](https://docs.kuberns.com/docs/reference/builds-and-deployments)).

| Field | Value |
|---|---|
| Root directory | `/` (the application is at the repository root) |
| Pre-build commands | none - the Dockerfile already installs `requirements.txt` |
| Post-build commands | none |
| Procfile commands | the six lines from section 1 |
| Port | `8000` - must equal what `web` binds |
| Dockerfile | the repository `Dockerfile` |

### The Dockerfile

`Dockerfile` at the repository root builds **one image that can run any of the six
processes**. It is `python:3.12-slim` to match the box, installs `requirements.txt`,
sets `PYTHONUNBUFFERED=1`, runs as a non-root user (uid 10001, which owns `/app` so
the Dhan token cache stays writable), and has a neutral default `CMD` - the web
process. Every worker overrides that command via the `Procfile`.

There is deliberately **no `HEALTHCHECK`** in the image. One image serves six
processes, so an image-level healthcheck would mark all five workers unhealthy, and on a
Swarm topology Swarm would then replace those tasks in a loop. The web healthcheck lives
in `docker-compose.yml`, scoped to the one service that speaks HTTP.

`.dockerignore` is a security boundary as much as a size optimisation: it keeps
`.env`, `.dhan_token*`, `parallax.db`, `data/` and the 900 MB `research/` tree
out of the build context, so they cannot be baked into a layer.

### Port

Kuberns injects `$PORT` and requires the web process to bind `0.0.0.0:$PORT` - the
tutorials are explicit that a process bound to a hard-coded port or to `localhost`
deploys successfully and is then unreachable
([tutorials/flask](https://docs.kuberns.com/docs/tutorials/flask)). The `Procfile` uses
`${PORT:-8000}`, which honours an injected `$PORT` and still works locally with no
`PORT` set. `--proxy-headers --forwarded-allow-ips="*"` is there because Kuberns
terminates TLS at its edge, so the dashboard needs to trust the forwarded headers.

---

## 6. Trial-account restrictions - read before you start

On a free trial the dashboard can block the exact settings this deployment needs
([trial-restrictions](https://docs.kuberns.com/docs/billing/trial-restrictions)):

* **edit deployment settings** - blocked
* **view or edit Dockerfile configuration** - blocked
* edit root directory, edit pre-build / post-build scripts - blocked
* create additional services or environments - blocked
* add custom domains - blocked

In other words, a trial account may not let you set the `Procfile` commands or the
Dockerfile at all, and may not let you create the background-worker resources the five
workers need. Runtime credits lift these guards. The trial is also limited to **two
processed GitHub push events per day**
([deploy-on-push](https://docs.kuberns.com/docs/guides/deploy-on-push)), so do not plan
to iterate by pushing. Check the restriction message next to each control; it is the
immediate source of truth.

---

## 7. First deploy, in order

1. Buy runtime credits if you are on a trial - otherwise steps 4 and 5 are blocked.
2. Connect the repository: install the Kuberns GitHub App, grant it this repo only, and
   pick project / service / `main` / region / plan. This is also the step that writes
   the `.kuberns` marker file into the repository
   ([ai-agent/kuberns-file](https://docs.kuberns.com/docs/ai-agent/kuberns-file)) - you
   do not author that file yourself, Kuberns does.
3. Add the Postgres datastore (section 4), then all eleven environment variables
   (section 3), including `PARALLAX_DB_URL`.
4. Confirm the config the agent proposed against section 5.
5. Attach a background-worker resource for each of the five workers, then deploy.
6. Open the live URL and check that `/api/state` returns JSON.
7. Confirm all five workers are running resources - see the trap in section 2.

After that, pushes to `main` deploy automatically: Kuberns verifies the GitHub webhook
signature and matches the pushed branch to the configured environment
([deploy-on-push](https://docs.kuberns.com/docs/guides/deploy-on-push)).

## 8. Operational notes

### Cold start re-downloads the 25 MB scrip master

`data/` is excluded from the image, and
`parallax.adapters.broker.dhan.ensure_scrip_master()` downloads
`api-scrip-master.csv` into `data/security_id_list.csv` on first use. Because
container filesystems are ephemeral, that download repeats on every cold start. Without
a scrip master, `resolve_contract()` cannot produce a `securityId` and the futures leg
**silently places nothing at all**. If cold starts are frequent, point
`PARALLAX_SCRIP_MASTER` at a copy on a persistent volume instead.

### Topology

Kuberns offers a *single node* topology (a Docker Compose path) and a *master node*
topology (Docker Swarm, replica-capable)
([resources/topology](https://docs.kuberns.com/docs/resources/topology)). Start on single
node. Do not scale this to replicas: the crypto workers share one global trade mode, one
capital figure and one journal (see `deploy/README.md`), so two replicas of `crypto`
would double the book rather than share it.

### The CI workflow is unrelated and still red

`.github/workflows/deploy.yml` builds this image and then runs
`kubectl apply -f k8s/` against a `KUBECONFIG` secret that does not exist, so it
fails on every push. That failure is GitHub-side only - Kuberns builds through its own
webhook and does not read GitHub Actions. It was left untouched, and `k8s/` remains a
dead path.

---

## 9. Local parity

`docker-compose.yml` brings up the same six commands plus a local Postgres:

~~~
cp .env.example .env      # fill in the blanks
docker compose up --build
~~~

Dashboard at http://localhost:8000. This is the cheap way to catch a per-process
misconfiguration without burning a trial push event.

---

## 10. Not verified

These are the things I could **not** confirm from Kuberns' documentation. Treat them as
open questions rather than settled behaviour, and confirm them on the first deploy:

1. **There is no `kuberns.yaml` / `kuberns.json` user-authored config format.**
   Nothing in the docs describes one, so none was invented. The `.kuberns` file is a
   Kuberns-generated connection marker, not a config file. Deployment config is
   dashboard-driven plus a root `Procfile`.
2. **Whether a root `Procfile` is honoured when a `Dockerfile` is also present.**
   Both are documented as Build & Deployment fields, but their interaction is not
   described. If the workers come up missing, paste the six commands into the
   **Procfile commands** field directly.
3. **Whether `$PORT` is injected for non-web processes.** It is documented only for
   the web process; none of the five workers need a port, so this should be harmless.
4. **The accepted Procfile process-name character set**, and whether comments are
   tolerated. Only `web` and `worker` appear in the docs.
5. **How many processes one environment supports.** Six is assumed to be fine; the docs
   give no limit.
6. **Whether Kuberns auto-injects `DATABASE_URL`.** The docs show it being built by
   hand from the datastore Overview fields, which is what this runbook assumes.
7. **Whether Kuberns honours a Dockerfile `HEALTHCHECK`.** Irrelevant now, since none
   is defined, but it is why the healthcheck was kept out of the image.
8. **What exactly a background-worker resource is**, whether it is billable, and how many
   an environment may have. The docs only say a worker without one does not run.

## Sources

* https://kuberns.com
* https://docs.kuberns.com/docs/guides/application-structure
* https://docs.kuberns.com/docs/guides/deployment-configuration
* https://docs.kuberns.com/docs/reference/builds-and-deployments
* https://docs.kuberns.com/docs/guides/environment-variables
* https://docs.kuberns.com/docs/guides/deploy-on-push
* https://docs.kuberns.com/docs/ai-agent/kuberns-file
* https://docs.kuberns.com/docs/billing/trial-restrictions
* https://docs.kuberns.com/docs/datastores
* https://docs.kuberns.com/docs/resources/topology
* https://docs.kuberns.com/docs/tutorials/flask
* https://docs.kuberns.com/docs/tutorials/django
* https://docs.kuberns.com/docs/faq
