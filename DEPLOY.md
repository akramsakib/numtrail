# Deploying numtrail

You want it on the internet, working. Three routes, in order of effort. Pick one
— they all end with a real site and real results.

The one rule that shapes everything below: **GitHub Pages serves files, it does
not run code.** Your browser also cannot call `instagram.com` itself (CORS). So
something has to sit in the middle and make the requests. That's it, that's the
whole constraint.

---

## Route 1 — Render, blueprint deploy (10 min, all 16 probes, free tier)

One deploy gives you the UI *and* the API on the same origin. No CORS, nothing to
point at, nothing to configure by hand.

1. Push this repo to GitHub (see [Publishing the repo](#publishing-the-repo)).
2. Go to <https://dashboard.render.com> → **New** → **Blueprint** → pick your repo.
3. Render reads `render.yaml`, builds the `Dockerfile`, and gives you
   `https://numtrail-xxxx.onrender.com`. That URL is the whole app.
4. Optional: in the Render dashboard add any lookup API keys as env vars
   (`NUMVERIFY_KEY`, `VERIPHONE_KEY`, `NUMLOOKUPAPI_KEY`, …) and the "needs a
   key" cards turn into real carrier results.

Free-tier notes worth knowing before you're surprised by them: the instance
sleeps after ~15 minutes of inactivity (first request after that takes ~30 s),
and it spins down/up per month if you exceed the free hours.

Same repo also works on **Fly.io**:

```bash
fly launch --copy-config --name numtrail    # uses fly.toml
fly deploy
fly open
```

…or on any machine with Docker:

```bash
docker build -t numtrail .
docker run -p 8000:8000 --rm numtrail
# http://localhost:8000
```

## Route 2 — GitHub Pages + Cloudflare Worker (15 min, 15 of 16 probes, free)

Keeps the front end on Pages. The worker does the talking. This is the only
combination that keeps *both* halves free at zero maintenance.

```bash
npm install -g wrangler
wrangler login
wrangler deploy numtrail/web/worker.js --name numtrail-api
# prints e.g. https://numtrail-api.you.workers.dev
```

Then:

1. GitHub repo → **Settings → Secrets and variables → Actions → Variables →
   New repository variable**
   - name: `NUMTRAIL_API_BASE`
   - value: `https://numtrail-api.you.workers.dev`
2. **Settings → Pages → Source: GitHub Actions.**
3. **Actions → deploy web UI to GitHub Pages → Re-run all jobs** (or push a
   commit). The workflow writes `config.js` from that variable during the build.
4. Cloudflare dashboard → your worker → **Settings → Variables**, add:
   - `ALLOW_ORIGIN` = `https://<your-user>.github.io` (CORS; without it the
     browser throws away the response)
   - `STATIC_ORIGIN` = `https://<your-user>.github.io/<repo>` (lets the worker
     serve `/api/catalog` from your Pages site)
   - optionally any lookup API keys

Worker coverage: 15 of 16 probes. Amazon's `/ax/claim` flow needs HTML form
parsing, so the edge build reports it as `skipped` and says why. Route 1 has all
15.

Before you point a public URL at it, put Cloudflare's rate-limiting rule in front
of `/api/check` — the worker's own limiter is per-isolate and best effort — and
keep `ALLOW_ORIGIN` locked to your Pages origin so nobody uses your worker as a
free enumeration proxy.

## Route 0 — GitHub Actions snapshot (nothing but GitHub, free, 5 min)

The repo ships this already; you only edit one file.

1. Put the numbers you care about in `watchlist.txt` — one per line, dial code
   included. Only numbers you're authorised to check; this file is public.
2. Optional: **Settings → Secrets and variables → Actions → Secrets** add
   `NUMVERIFY_KEY` or `VERIPHONE_KEY` (free tiers) so the run records real
   carrier data. Add `PROXY_URL` (residential proxy) if you want the three
   bot-walled web probes to answer.
3. **Actions → snapshot → Run workflow.** A form opens with a text box:

   ```
   number             +60123456789          ← type any number, with dial code
                      +60123456789, +601112345678   (comma-separated)
   skip watchlist     false                 ← tick to probe only what you typed
   ```

   Press the green **Run workflow** button. The runner probes that number,
   commits `numtrail/web/static/snapshots.json`, re-triggers the Pages deploy,
   and prints the answer in the run's own summary.

The Pages site then shows timestamped results for those numbers with no server
involved. The schedule (`cron: 23 */6 * * *`) re-probes `watchlist.txt` four
times a day, and on-demand numbers from Run workflow are carried over — the most
recent 40, so the committed file stays small.

Limit: the site serves numbers that have been probed here, not arbitrary visitor
input. A web form can't trigger a workflow without putting a `workflow`-scoped
token in the browser, which would be handing out write access to your repo — so
GitHub's own Run workflow button is the query box.

## Route 0b — username checks on GitHub (Sherlock / Maigret)

Numbers use the snapshot workflow. Usernames use their own:

1. **Actions → username → Run workflow**
   ```
   username   akram                 ← no @
   database   sherlock | maigret
   limit      blank = every vendored definition (478 or 3,268)
   ```
2. The runner writes `numtrail/web/static/usernames.json`, commits it, and
   redeploys Pages. The run's own summary lists what was found, and what refused
   to answer, with the reason.
3. The file accumulates: it keeps the newest 8 usernames (4 MB budget), so
   checking a second person does not throw away the first. On the site, the
   chips under the header are the usernames it currently serves.

Locally: `numtrail-user akram --db sherlock --limit 40 --json out.json`.

## Route 3 — Pages only, no backend

Allowed, but be clear on what you get: the page loads, shows all 16 probe cards,
and refuses to invent results. It displays the setup panel instead. That's the
honest behaviour, and it's why there is no "demo mode" any more.

---

## Publishing the repo

```bash
cd numtrail
git init
git add -A
git commit -m "numtrail: phone attribution tool, CLI + web"
git branch -M main
git remote add origin git@github.com:<you>/numtrail.git
git push -u origin main
```

Two cosmetic touches: set `repoUrl` in `numtrail/web/static/config.js` (or let the
Pages workflow fill it from the repo), and put your name in `pyproject.toml`'s
`authors` plus `CREDITS.md`.

**Keep the GPL-3.0 licence.** The three legacy modules are derived from
`megadose/ignorant`; that's not optional and `CREDITS.md` says what came from
where.

## Checking a deployment without a browser

```bash
curl -s https://your-app/healthz | python3 -m json.tool
curl -sN -X POST https://your-app/api/check \
  -H 'content-type: application/json' -d '{"phone":"+60123456789"}'
```

`/healthz` lists the probes the deployment can actually run. If `probes` is
missing a module you expected, the deploy is running old code or the module
failed to import — the CLI (`numtrail --list-modules`) tells you the same thing.

### Rate limiting / abuse defaults

| env var | default | meaning |
| --- | --- | --- |
| `NUMTRAIL_RATE_LIMIT` | `8` | runs allowed per IP per window |
| `NUMTRAIL_RATE_WINDOW` | `60` | window in seconds |
| `NUMTRAIL_MAX_INFLIGHT` | `4` | concurrent runs across all clients |
| `NUMTRAIL_ALLOW_ORIGIN` | *(unset)* | CORS origin if the UI is served elsewhere |
| `NUMTRAIL_VERBOSE` | *(unset)* | log request paths — phone numbers are never logged |

Everything the server sees is in memory only. No database, no logs of numbers, no
history.
