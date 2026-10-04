# Deploying Race Engineer AI

How to put the site, the API and the Claude connector online, keep them running, and take them
down. The recommended stack is all free plans except the Anthropic API:

| Part | Where | Why |
|---|---|---|
| The site (`web/`) | [Vercel](https://vercel.com) Hobby | static Next.js pages; every push to `main` rebuilds it |
| The API (`engine/`): tools, chat, `/mcp` | [Modal](https://modal.com) Starter | 2 GiB containers that scale to zero, streaming responses, and the data on a private volume; $30 of compute credit a month |
| The chat's limits | [Upstash](https://upstash.com) Redis, free | question counts and the day's spend, kept across container restarts |
| The chat's model | the Claude API | your own key, with a monthly spend limit |

The fallback for the API is [Google Cloud Run](#fallback-google-cloud-run). The first deploy
takes about 75 minutes, most of it the data upload, and under $1 of API spend.

```
 Vercel: the site ──(browser, CORS)──► Modal: race-engineer-api ──► Claude API
                                          │  volume race-engineer-serve (/data, read-only)
 Claude (custom connector) ──► /mcp ──────┤  secret race-engineer-api
                                          └─► Upstash Redis (the limits)
```

Contents: [before you start](#before-you-start) · [the first deploy](#the-first-deploy) (steps
1-10) · [running it](#running-it) · [updating the data](#updating-the-data-and-the-saved-answers)
· [GitHub Actions](#deploying-from-github-actions) ·
[rotating the chat secret](#rotating-the-chat-secret) ·
[stopping and rolling back](#stopping-and-rolling-back) ·
[Cloud Run](#fallback-google-cloud-run) · [costs](#costs) · [known risks](#known-risks) ·
[troubleshooting](#troubleshooting)

## Before you start

- **Your own terminal, with `ANTHROPIC_BASE_URL` unset.** Run every command here from a terminal
  you opened yourself. Some terminals, such as one an AI coding assistant runs commands in, set
  `ANTHROPIC_BASE_URL` to a gateway, and an API started there would send your key and every
  question through it. `make deploy-api` refuses to run while it is set.
- **Never paste a key, token or secret** into a chat with an assistant, an issue, a commit or a
  shell command line. Read them into the shell with `read -rs` (nothing is echoed, nothing goes
  into the history), or type them into the provider's dashboard.
- **The data.** The API serves your local processed data and the model's results, so build them
  first (`make data`, `make select-model CHECKPOINT=…`, `make m4`; see the
  [README](../README.md#quick-start)). The served part is about 6.8 GB.
- **Tools:** the repository's own (`make setup`), `curl` and `openssl`. Nothing else is
  installed: the Modal CLI comes with the engine's `cloud` dependency group
  (`uv run --group cloud modal …`, run from `engine/`).

Start every session the same way:

```bash
cd race-engineer
git checkout main && git pull
unset ANTHROPIC_BASE_URL
make mcp-app          # the chart bundles (gitignored): /mcp serves them, and make deploy-api checks them
```

## The first deploy

### 1. A dedicated Anthropic key, and Modal's billing (5 min)

In the [Anthropic Console](https://console.anthropic.com), create an API key for this site
(ideally in a workspace of its own) and set a monthly spend limit there, for example $20. It is
a backstop that holds whatever happens in this code; the site's own cap is $3 a day. Then, in
this terminal only:

```bash
read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY    # paste, then Enter
```

If you have no Modal account yet, create one at [modal.com](https://modal.com) and log the CLI
in once: `cd engine && uv run --group cloud modal setup && cd ..`. Then open the workspace's
usage and billing page in Modal's dashboard. If a payment method is on file, usage past the $30
monthly credit is charged to it: set a workspace budget there if Modal offers one. Kept warm all
month, the API uses about $29 of the credit (see [costs](#costs)).

### 2. Record the saved answers, and check the chat (15 min, about $0.20-0.55)

The site shows saved example answers whenever the live chat is limited, paused or off. They are
recorded by asking your local API the seven example questions with your key:

```bash
make api                                     # this tab (it has the key); /api/health says "mode": "anthropic"
make saved-record API=http://127.0.0.1:8000  # a second tab (it needs no key): 7 answers, ~$0.05-0.15 -> data/saved/
make chat-eval URL=http://127.0.0.1:8000     # also keyless: the 15 accuracy questions, ~$0.15-0.40
```

The deploy itself doesn't need `make chat-eval`, but it is the only measure of how the real chat
uses the tools: it writes `report/m7_chat_accuracy.md` and exits non-zero if any question failed a
check; the report is written either way. Stop `make api` afterwards (Ctrl-C). The recordings stay
in `data/saved/`, which is gitignored: their charts carry data drawn from car positions.

### 3. Build and upload the served data (5 min, then about 17 min of upload at 50 Mbps)

```bash
make serve-data       # data/serve: hard links to the files the API reads, plus the saved answers, checked
make serve-upload     # creates the Modal volume race-engineer-serve and uploads (in its own tab)
```

`make serve-data` rebuilds `data/serve/` from scratch and checks that the bundle's health matches
the source data's. `make serve-upload` refuses a bundle with scripted (fake) saved answers, and
prints "uploaded N files" when it is done. Steps 4 and 5 can be done while it uploads; step 6
needs the upload finished, because the deploy mounts the volume.

### 4. Upstash (5 min)

Sign up at [upstash.com](https://upstash.com) (no card) and create a Redis database on the free
plan, primary region AWS us-east-1, with no read regions. Keep its page open: step 5 needs the
**REST URL** and the **read-write REST token** (not the read-only one).

Optionally, check the store from this terminal before the API uses it:

```bash
read -rs UPSTASH_REDIS_REST_URL && read -rs UPSTASH_REDIS_REST_TOKEN
export UPSTASH_REDIS_REST_URL UPSTASH_REDIS_REST_TOKEN
make limits-check     # runs the limits' store tests on the real database, under re:test:..., then deletes those keys
```

### 5. The Modal Secret (3 min)

In Modal's dashboard: Secrets → Create new secret → Custom, named `race-engineer-api`, with four
keys:

| Key | Value |
|---|---|
| `ANTHROPIC_API_KEY` | the key from step 1 |
| `RACE_ENGINEER_CHAT_SECRET` | the output of `openssl rand -hex 32` (it signs the conversations browsers hold, and salts the visitor codes) |
| `UPSTASH_REDIS_REST_URL` | from step 4 |
| `UPSTASH_REDIS_REST_TOKEN` | from step 4 |

### 6. Deploy the API (3 min)

```bash
make deploy-api       # prints https://<workspace>--race-engineer-api.modal.run; no browser origin allowed yet
export API=https://<workspace>--race-engineer-api.modal.run
curl -s $API/api/health | python3 -m json.tool
```

Health should show `"status": "ok"`, `chat.mode` `anthropic` with `chat.available` true, and
`limits.enabled` true with `limits.store` `upstash` and `limits.problems` empty. The first
request after an idle spell takes a few seconds (a cold start).

What `make deploy-api` deploys (`engine/modal_api.py`): the app `race-engineer-api`, one
container at most, a request of half a CPU core and 2 GiB of memory (allowed up to 4 GiB),
scaled to zero after 5 idle minutes, with the volume mounted read-only at `/data`, the Secret,
and a baked environment: a public host, stateless MCP, the limits on with the Upstash store, and
the browser origins from `SITE_ORIGIN` (none when it is unset). It refuses to deploy while
`ANTHROPIC_BASE_URL` is set or a chart bundle is missing.

### 7. Vercel (10 min)

Sign up at [vercel.com](https://vercel.com) with GitHub (the Hobby plan is for non-commercial
use like this one) and let its GitHub app access only this repository. Then Add New → Project →
import the repository:

| Setting | Value |
|---|---|
| Root Directory | `web` |
| Include files outside the Root Directory in the Build Step | on (the report pages read `../report`) |
| Framework, build command, Node | nothing to set: Next.js is detected, the build command (`npm run build:vercel`) comes from `web/vercel.json` and Node 24 from `web/package.json` |
| Environment Variables | `NEXT_PUBLIC_API_URL` = the value of `$API`, for Production and Preview |

Deploy. The build fails on purpose when `NEXT_PUBLIC_API_URL` is missing or isn't `https://`
(`web/scripts/check-env.ts`), because the site would otherwise ship pointing at
`http://127.0.0.1:8000`. Each build also copies the featured corners, the landing page's card
of real corners and the saved answers from the API into the site (`public/snapshots/`,
`public/saved/`, both gitignored); a call that fails is skipped, never failing the build.
Note the production URL, `https://<project>.vercel.app`. `NEXT_PUBLIC_SITE_URL` is needed only
for a custom domain.

### 8. Let the site call the API (1 min)

```bash
export SITE=https://<project>.vercel.app
make deploy-api SITE_ORIGIN=$SITE
```

**Pass `SITE_ORIGIN` on every later deploy too:** without it the API is deployed with no browser
origin allowed, and the site's pages can't reach it.

### 9. Check it (5 min, about $0.01)

```bash
make deploy-check SITE=$SITE API=$API
```

It checks every page, `hero.json` and the saved answers on the site, the security headers, CORS
from the site (and refused from other origins), `/mcp` with every tool and chart, the limits
(`store: upstash`, no problems), the client address the limits see (spoofed `X-Forwarded-For` and
`X-Real-IP` headers must not change it), a cold start and one heavy `explain_corner` call, and it
asks one real question to check that the answer streams through Modal. `CHAT=skip` asks nothing.
It prints your `visitor` tag.

Then open the site on a computer and on a phone (Safari, on cellular rather than Wi-Fi): ask one
question on `/chat` and watch the answer stream in, and open `$API/api/chat/status` on the
phone: its `visitor` tag must differ from the computer's. If the check reports `explain_corner`
over 2 s, or Modal's memory graph for the app sits above 2 GiB while it runs, see
[troubleshooting](#troubleshooting).

### 10. The Claude connector (5 min)

In Claude: Settings → Connectors → Add custom connector → name "Race Engineer", URL `$API/mcp`,
no sign-in (leave the OAuth fields empty) → enable it in a new chat from the tools menu. Ask, for
example: "What's the most recent race you can analyse?", "Summarise the 2023 Monaco Grand Prix",
"Where did Gasly make mistakes in the 2025 Abu Dhabi race?", "What went wrong at the corner where
he lost the most?", "Where did Norris gain on Piastri in 2025 Abu Dhabi qualifying?", "How does
Hamilton's driving style differ from Leclerc's?" and "Who made the biggest mistakes in the last
race?". Each chart should render in the conversation. Custom connectors work on Claude's Free
plan (one connector), Pro, Max, Team and Enterprise; Claude calls the connector from Anthropic's
cloud, so `/mcp` has a global rate limit rather than a per-visitor one.

### Afterwards

- **When the repository is public,** set `NEXT_PUBLIC_REPO_URL` in Vercel to its URL and redeploy
  (Deployments → the latest → Redeploy): report references become links and the header shows the
  source link.
- **The demo video:** `make demo-video SITE=$SITE API=$API` films the live site (one real
  question, about $0.02) into `data/demo/race-engineer-demo.mp4`; see [demo.md](demo.md).
- **Preview deployments** don't call the production API unless you allow them:
  `make deploy-api SITE_ORIGIN=$SITE PREVIEW_REGEX='^https://race-engineer-[a-z0-9-]+-<your-team-slug>\.vercel\.app$'`.
  Off by default: anyone can name a Vercel project to match a pattern (harmless here, with no
  cookies and per-visitor limits, but not needed).

## Running it

**The chat's limits** (the settings and their defaults: [report/m7_ship.md](../report/m7_ship.md#settings)):
10 questions an hour and 25 a day per visitor, 8 per conversation; $3 a day of model spending in
all, with $0.10 reserved per question in flight; $0.30 at most for one question; 1,000 questions a
day from everyone together. A visitor is a salted hash of the IP address that changes every UTC
day. In production the limits fail closed: when Upstash can't be read, the chat turns off and the
site shows the saved answers, while the tools, the explorers and `/mcp` keep working.

**Pausing the chat,** from the fastest to the bluntest:

1. **The kill switch,** from your terminal with the Upstash variables exported (step 4):
   ```bash
   make chat-status      # today's spend, reservations, questions, and whether the switch is on
   make chat-pause       # new questions get 503 chat_paused within 60 s; saved answers show
   make chat-resume      # questions are answered again within 60 s
   ```
   The Upstash console works too: `SET re:v1:kill 1` pauses, `DEL re:v1:kill` resumes.
2. **Close it for the day or for good:** add `RACE_ENGINEER_DAILY_CAP_USD` = `0`,
   `RACE_ENGINEER_PAUSED` = `1` or `RACE_ENGINEER_CHAT` = `off` as a key of the
   `race-engineer-api` Secret, then `make deploy-api SITE_ORIGIN=$SITE` so the container restarts
   with it. Remove the key and redeploy to reopen.
3. **Disable the key** in the Anthropic Console: every question then fails, and the spend stops.
4. **Stop the API** altogether (below): the site stays up, showing what it can without it.

**Logs and history:**

```bash
cd engine
uv run --group cloud modal app logs race-engineer-api       # one line per question (tools, tokens, cost, quota left), never its text
uv run --group cloud modal app history race-engineer-api    # every deployed version
```

## Updating the data and the saved answers

After new races, or when the saved answers get old (each shows the date it was recorded):

```bash
make data && make m4                          # the new sessions, processed and scored
make api                                      # with your key, in another tab
make saved-record API=http://127.0.0.1:8000   # fresh saved answers (stop make api afterwards)
make serve-data && make serve-upload
make deploy-api SITE_ORIGIN=$SITE             # restarts the API, which reads the new files
```

Then redeploy the site in Vercel (Deployments → the latest → Redeploy), so its copies of the
featured corners and saved answers are taken from the updated API. The upload overwrites files
but never deletes them: a file you removed locally stays on the volume until you delete it.

```bash
cd engine
uv run --group cloud modal volume ls race-engineer-serve /saved
uv run --group cloud modal volume rm race-engineer-serve /saved/<id>.json
```

## Deploying from GitHub Actions

`make deploy-api` from your own terminal is the usual way. The repository also has a manual
workflow, [deploy-api.yml](../.github/workflows/deploy-api.yml), that runs the same deploy on
GitHub's runners: it builds the chart bundles, then runs `modal deploy modal_api.py`. Only you
start it (Actions → Deploy API → Run workflow, or `gh workflow run deploy-api.yml`); no push,
pull request or schedule does. It stops at its first step, without contacting Modal, until the
repository has:

- the secrets `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`, a Modal API token made for this
  workflow in Modal's dashboard: `gh secret set MODAL_TOKEN_ID`, then
  `gh secret set MODAL_TOKEN_SECRET` (each asks for the value, which isn't echoed; never pass it
  with `--body`);
- the variable `SITE_ORIGIN`, the site's origin (`gh variable set SITE_ORIGIN --body "$SITE"`),
  and `PREVIEW_REGEX` if you allow previews. Without `SITE_ORIGIN` the API is deployed with no
  browser origin allowed, as with `make deploy-api` alone.

The data upload stays on your Mac (`make serve-upload`), and the chat's own secrets stay in the
Modal Secret, never in GitHub. To retire the workflow's access, delete the two repository secrets
(`gh secret delete MODAL_TOKEN_ID`, `gh secret delete MODAL_TOKEN_SECRET`) and revoke the token in
Modal's dashboard.

## Rotating the chat secret

Generate a new value (`openssl rand -hex 32`), paste it over `RACE_ENGINEER_CHAT_SECRET` in the
`race-engineer-api` Secret in Modal's dashboard, then `make deploy-api SITE_ORIGIN=$SITE`. Two
things happen, both expected: every open conversation ends (its next question is refused as
`invalid_history`, and the site says the conversation can't continue and offers a new one), and
today's per-visitor counts start again, because the visitor codes are salted with the secret.

## Stopping and rolling back

| To | Do |
|---|---|
| Roll the API back to its previous version | `cd engine && uv run --group cloud modal app rollback race-engineer-api` (or name a version from `modal app history`, e.g. `… rollback race-engineer-api v3`). It restores that version's code and settings, CORS included, not the data on the volume |
| Stop the API now | `cd engine && uv run --group cloud modal app stop race-engineer-api` stops it and its containers for good; `make deploy-api SITE_ORIGIN=$SITE` brings it back |
| Roll the site back | Vercel's Instant Rollback: the project's Deployments tab, an earlier production deployment's menu |
| Take the site down | delete the Vercel project (in the project's Settings) |
| Remove the data | `cd engine && uv run --group cloud modal volume delete race-engineer-serve` (after stopping the API) |
| Remove the limits store | delete the Upstash database; a running API then fails closed (the chat off, saved answers shown) |

## Fallback: Google Cloud Run

Use this only if Modal doesn't suit: Cloud Run needs a Google Cloud billing account with a card.
The same image serves any container host (`engine/Dockerfile`, built from the repository root;
its header shows a plain `docker run`).

| | Modal (recommended) | Cloud Run (fallback) |
|---|---|---|
| Container | 0.5 core requested, 2-4 GiB, one container | 1 vCPU, 2 GiB, `--max-instances 1`, 300 s request timeout |
| Data | the volume `race-engineer-serve` | a Cloud Storage bucket mounted read-only at `/data` |
| Secrets | the Modal Secret | Secret Manager |
| Client address | the connection's own | the last `X-Forwarded-For` entry (`RACE_ENGINEER_TRUSTED_PROXY_HOPS=1`, set in the image) |

The served bundle (about 6.8 GB) is past Cloud Storage's 5 GB of free storage, so the rest is
billed at the Standard storage rate, a few cents a month. Steps 1, 2, 4 and 7-10 above are the
same, with the Cloud Run URL as `$API`. From your own terminal (the variables keep these
commands on the new project without changing gcloud's active one):

```bash
gcloud auth login                                             # if gcloud isn't logged in
export CLOUDSDK_CORE_PROJECT=race-engineer-<unique-suffix>    # a new project, for this API only
export REGION=us-central1 BUCKET=$CLOUDSDK_CORE_PROJECT-serve
export IMAGE=$REGION-docker.pkg.dev/$CLOUDSDK_CORE_PROJECT/race-engineer/api
export SA=race-engineer-api@$CLOUDSDK_CORE_PROJECT.iam.gserviceaccount.com

gcloud projects create $CLOUDSDK_CORE_PROJECT
gcloud billing projects link $CLOUDSDK_CORE_PROJECT --billing-account=<BILLING_ACCOUNT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    secretmanager.googleapis.com storage.googleapis.com iam.googleapis.com
gcloud iam service-accounts create race-engineer-api      # the API runs as this account

# The data: the bundle of step 3 (make serve-data), synced to the bucket's root.
gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access
gcloud storage rsync data/serve gs://$BUCKET --recursive
gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:$SA --role=roles/storage.objectViewer

# The secrets: typed into read -rs (nothing echoed), piped into Secret Manager.
openssl rand -hex 32 | gcloud secrets create race-engineer-chat-secret --data-file=-
read -rs ANTHROPIC_API_KEY && printf %s "$ANTHROPIC_API_KEY" | gcloud secrets create race-engineer-anthropic-key --data-file=-
read -rs UPSTASH_REDIS_REST_URL && printf %s "$UPSTASH_REDIS_REST_URL" | gcloud secrets create race-engineer-upstash-url --data-file=-
read -rs UPSTASH_REDIS_REST_TOKEN && printf %s "$UPSTASH_REDIS_REST_TOKEN" | gcloud secrets create race-engineer-upstash-token --data-file=-
for s in chat-secret anthropic-key upstash-url upstash-token; do
  gcloud secrets add-iam-policy-binding race-engineer-$s --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor
done

# The image, built by Cloud Build from the committed tree (no data, no node_modules).
gcloud artifacts repositories create race-engineer --repository-format=docker --location=$REGION
git archive --format=tgz -o /tmp/race-engineer-src.tgz HEAD
cat > /tmp/race-engineer-cloudbuild.yaml <<'EOF'
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-f", "engine/Dockerfile", "-t", "$_IMAGE", "."]
images: ["$_IMAGE"]
EOF
gcloud builds submit /tmp/race-engineer-src.tgz --config=/tmp/race-engineer-cloudbuild.yaml --substitutions=_IMAGE=$IMAGE

# The service: public, one instance, the bucket at /data, the secrets as variables.
gcloud run deploy race-engineer-api --image=$IMAGE --region=$REGION --service-account=$SA \
    --allow-unauthenticated --cpu=1 --memory=2Gi --max-instances=1 --concurrency=32 --timeout=300 \
    --execution-environment=gen2 \
    --add-volume=name=data,type=cloud-storage,bucket=$BUCKET,readonly=true \
    --add-volume-mount=volume=data,mount-path=/data \
    --set-secrets=ANTHROPIC_API_KEY=race-engineer-anthropic-key:latest,RACE_ENGINEER_CHAT_SECRET=race-engineer-chat-secret:latest,UPSTASH_REDIS_REST_URL=race-engineer-upstash-url:latest,UPSTASH_REDIS_REST_TOKEN=race-engineer-upstash-token:latest \
    --set-env-vars=RACE_ENGINEER_CORS_ORIGINS=none \
    --startup-probe=httpGet.path=/api/ready,httpGet.port=8080,periodSeconds=10,timeoutSeconds=5,failureThreshold=18
```

It prints the service URL (`https://race-engineer-api-<hash>.<region>.run.app`): use it as
`$API` for steps 6, 7 and 9. Once the site exists (step 8):

```bash
gcloud run services update race-engineer-api --region=$REGION --update-env-vars=RACE_ENGINEER_CORS_ORIGINS=$SITE
```

`make deploy-check SITE=$SITE API=$API` then checks the client address: if the spoofed headers
change the `visitor` tag, Cloud Run's `X-Forwarded-For` layout differs from the one the image
assumes, and `RACE_ENGINEER_TRUSTED_PROXY_HOPS` needs changing (`--update-env-vars`).

| On Cloud Run, to | Do |
|---|---|
| Update the data | `gcloud storage rsync data/serve gs://$BUCKET --recursive --delete-unmatched-destination-objects`, then run the `gcloud run deploy` command again (a new revision rereads it) |
| Ship new code | rebuild the image (`git archive` and `gcloud builds submit` above), then `gcloud run deploy` again |
| Roll back | `gcloud run revisions list --service=race-engineer-api --region=$REGION`, then `gcloud run services update-traffic race-engineer-api --region=$REGION --to-revisions=<revision>=100` |
| Pause the chat | `make chat-pause`, as on Modal; or `--update-env-vars=RACE_ENGINEER_PAUSED=1` |
| Stop public access | `gcloud run services remove-iam-policy-binding race-engineer-api --region=$REGION --member=allUsers --role=roles/run.invoker` |
| Delete it all | `gcloud projects delete $CLOUDSDK_CORE_PROJECT` |

If `gcloud builds submit` fails on a permission, the error names the role its service account
lacks (usually Artifact Registry Writer); grant it to that account and submit again.

## Costs

As of October 2026 (free tiers change; check each provider's pricing page):

| Service | Free | This project |
|---|---|---|
| Modal Starter | $30 of compute credit a month; volumes free up to 1 TiB | about $0.040 an hour while a container runs (0.5 core, 2 GiB), so a visit costs well under a cent with scale to zero. Kept warm all month (traffic at least every 5 minutes, crawlers included, or `min_containers=1`), about $29; at 1 core, about $46 |
| Upstash Redis | 500K commands a month, no card | about 25 commands per question, so about 18,000 questions a month; past the quota the chat fails closed until the month resets, at no charge |
| Claude API | — | $0.0035-0.0196 per question measured with Sonnet 5.5; at most $3 a day by the site's cap, and your key's monthly limit above that |
| Vercel Hobby | non-commercial use; 100 deployments a day, 45 minutes per build | free |
| Cloud Run (fallback) | 180,000 vCPU-seconds, 360,000 GiB-seconds and 2 million requests a month (about 50 hours at 1 vCPU and 2 GiB); 5 GB of Cloud Storage in us-central1; 0.5 GB of Artifact Registry | the bundle's storage past 5 GB and the image past 0.5 GB, a few cents a month; time past the free hours |

Modal is expected to bill the higher of a container's request and its use (check
[modal.com/pricing](https://modal.com/pricing)): the API requests half a core and 2 GiB, and may
use more while a heavy tool runs.

## Known risks

- **The client address on Modal.** The limits count visitors by the address Modal hands the app,
  which is expected to be the real client (inferred from Modal's code, not documented). Step 9
  checks it on every deploy; if it fails, the chat refuses questions (`chat_unavailable`) rather
  than lumping every visitor into one quota.
- **Upstash's monthly commands.** The chat-status route has a per-visitor rate (60 a minute) but
  no global one, so someone rotating through many IPv6 networks could use up Upstash's 500K
  commands with status reads alone. The chat would then fail closed for the rest of the month,
  with no money spent. Watch the database's usage in the Upstash console; a new free database
  (a new URL and token in the Secret, then `make deploy-api SITE_ORIGIN=$SITE`) restores it.
- **Visitors behind one network** (an office, a mobile carrier's NAT) share a quota; when it runs
  out they get the saved answers.
- **A question can pass the cap by one call:** the ceiling is checked before each call to the
  model, so the day's spend can overshoot $3 by at most one call per question in flight. The key's
  monthly limit is the backstop.
- **Safari may hold back the first events of a stream.** If answers on an iPhone arrive all at
  once, add `RACE_ENGINEER_SSE_PAD` = `2048` to the Secret and redeploy.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `make deploy-api`: "not deploying", then "ANTHROPIC_BASE_URL is set" | `unset ANTHROPIC_BASE_URL`, or use a terminal you opened yourself |
| `make deploy-api`: "chart bundle … not built" or "is the placeholder" | `make mcp-app` |
| `make deploy-api`: "SITE_ORIGIN must be the site's origin" | pass `https://<host>` with no path and no trailing slash |
| The deploy fails to find the volume | `make serve-upload` creates it; let it finish first |
| `make serve-upload`: "holds scripted answers" | record real ones (step 2), then `make serve-data` without `ALLOW_FAKE` |
| Health: `limits.problems` "limits store not configured" or "unreachable" | the Upstash keys are missing from the Secret, or wrong (the read-write REST URL and token); the chat stays off until fixed, then redeploy |
| Health: `limits.problems` "chat secret not set" | `RACE_ENGINEER_CHAT_SECRET` is missing from the Secret, so every restart would end every conversation: add it (step 5), then redeploy |
| The site's pages say they can't reach the API, and the browser console shows CORS errors | the last deploy had no `SITE_ORIGIN`: `make deploy-api SITE_ORIGIN=$SITE` |
| The Vercel build fails at check-env | set `NEXT_PUBLIC_API_URL` to the API's `https://` URL, then redeploy |
| The landing page draws no real corners, or the chat offers no saved answers | the build couldn't reach the API (a cold start, or the API was down): redeploy the site once the API answers |
| `deploy-check`: `explain_corner` over 2 s | the half-core request is too small: set `cpu=1.0` in `engine/modal_api.py`, then redeploy (about $46 a month if always warm) |
| Modal's memory graph sits above 2 GiB | set `memory=(3072, 4096)` in `engine/modal_api.py`, then redeploy (about half as much again in memory cost) |
| `deploy-check`: the `visitor` tag changes with spoofed headers, or is missing | see [known risks](#known-risks); the chat stays on saved answers until the address is right |
| Claude's connector fails to connect, or drops between tool calls | it may need MCP sessions: set `RACE_ENGINEER_MCP_STATELESS` to `"0"` in `serve_env()` in `engine/modal_api.py`, then redeploy (one container keeps the sessions in one process) |
