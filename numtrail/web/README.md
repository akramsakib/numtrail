# numtrail web

The browser version of numtrail: enter a number with its dial code, watch 16
probes fire in parallel, filter by outcome. 161 services are catalogued; the 145
that can't be automated are one click away behind the "everything" chip.

```
numtrail/web/
  static/        the whole front end — no build step, no framework, no bundler
    index.html
    app.js
    styles.css
    catalog.json   161 sites (generated from numtrail/catalog.py)
    config.js      where the API lives
  server.py      stdlib HTTP server: static files + streaming check API
  worker.js      the same API as a Cloudflare Worker, for Pages hosting
```

## The constraint you have to design around

**GitHub Pages cannot run these checks.** It serves static files and nothing
else, and a browser cannot call `instagram.com` directly — the request is blocked
by CORS before it leaves the tab. So there are two halves:

| half | what it does | where it can live |
| --- | --- | --- |
| front end | input, cards, filters, export | GitHub Pages (free) |
| API | actually talks to the 16 automated probes | a server you control |

If you deploy the front end alone it loads and shows every probe card, then
displays a setup panel instead of results. There is no demo mode and no fake
data: a page that cannot check must not pretend it did.

## Run it locally (real checks)

```bash
pip install -e .
numtrail-web --port 8000
# open http://localhost:8000
```

Optionally give it API keys first — those twelve probes go from `needs a key` to
real answers:

```bash
export TWILIO_ACCOUNT_SID=... TWILIO_AUTH_TOKEN=...
export NUMVERIFY_KEY=... ABSTRACTAPI_PHONE_KEY=... VERIPHONE_KEY=... IPQS_KEY=...
export VONAGE_API_KEY=... VONAGE_API_SECRET=... TELESIGN_CUSTOMER_ID=... TELESIGN_API_KEY=...
export PLIVO_AUTH_ID=... PLIVO_AUTH_TOKEN=... BIRD_ACCESS_KEY=... NUMLOOKUPAPI_KEY=...
export TRESTLE_API_KEY=... MOCEAN_API_KEY=... MOCEAN_API_SECRET=...
numtrail-web
```

Server options: `--host`, `--port`, `--no-ratelimit`. Defaults: per-IP limit of
8 runs/minute, 4 concurrent runs, 20 s per-probe timeout. Numbers are never
logged or written to disk; `NUMTRAIL_VERBOSE=1` logs paths only, still not
bodies.

## Host the front end on GitHub Pages

1. Push this repo to GitHub.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. `.github/workflows/pages.yml` runs on every push to `main`: it regenerates
   `catalog.json` from `catalog.py` (so the site can never drift from the source
   of truth) and publishes `numtrail/web/static` to
   `https://<you>.github.io/<repo>/`.
4. Set `repoUrl` in `static/config.js` to your repo so the "source" link works.

## Give Pages a backend (Cloudflare Worker, free tier)

GitHub Pages can't proxy, so put the API on a worker:

```bash
npm install -g wrangler
wrangler login
wrangler deploy numtrail/web/worker.js --name numtrail-api
# note the URL it prints, e.g. https://numtrail-api.you.workers.dev
```

Then either:

- **permanent:** set `apiBase` in `static/config.js` to that URL, or
- **per-visit:** open `https://<you>.github.io/<repo>/?api=https://numtrail-api.you.workers.dev`

Set two worker variables (Dashboard → Worker → Settings → Variables):

| variable | value | why |
| --- | --- | --- |
| `ALLOW_ORIGIN` | `https://<you>.github.io` | CORS — without it the browser blocks the response |
| `STATIC_ORIGIN` | `https://<you>.github.io/<repo>` | so `/api/catalog` proxies your catalogue |
| `TWILIO_ACCOUNT_SID` etc. | your keys | optional, unlocks the 4 API probes |

### What the worker covers

| probe | in the edge build? |
| --- | --- |
| my_number | yes — and unlike the rest it also runs in the browser, so a static build answers Malaysian numbers with no backend |
| instagram | yes — WebCrypto HMAC, same signature as the Python module |
| snapchat | yes |
| twilio, numverify, abstractapi, veriphone | yes, with worker secrets |
| vonage, ipqualityscore, telesign, plivo, bird, numlookupapi, trestle, mocean | yes — a compact `KEYED` registry, same request and status mapping as the Python modules |
| amazon | **no** — `/ax/claim` needs HTML form parsing and cookie state; reports `skipped` and tells you to run the Python server |

So Pages + Worker gives you 15 of 16 probes. Pages + a Python host gives you all 16.
Don't let the UI pretend otherwise: the skipped card says exactly that.

### Do not skip this part

- **Rate limiting in the worker is per-isolate and best effort.** For anything
  public, put Cloudflare's rate-limiting rule (or a Durable Object / KV counter)
  in front of `/api/check`, and keep `ALLOW_ORIGIN` locked to your Pages origin
  so other sites can't use your worker as a free enumeration proxy.
- **Your keys are your bill.** Anyone who can call the endpoint spends your
  Twilio/Numverify credits.
- **A public instance is a public service.** A URL that turns any phone number
  into a list of accounts is useful to people with no legitimate reason to have
  it, and it violates most of these services' terms. The safe defaults are:
  keep it private (Pages behind a password / an unlisted worker, or run it
  locally), don't add a bulk/paste-many-numbers endpoint, and log nothing.
  If you do make it public, expect takedowns from the frontend sites and expect
  to be the one answering for it.

## Options

| URL | effect |
| --- | --- |
| `?api=https://...` | override the API base for this visit |
| `#+60123456789` | the run number is stored in the fragment, so a link re-runs it |

Keyboard: `Enter` runs, `/` jumps to the filter box.

## Regenerating the catalogue

`catalog.json` is generated, never hand-edited:

```bash
python -m numtrail.catalog --write     # rewrite static/catalog.json
python -m numtrail.catalog             # coverage report by category
```

Promoting a site from `manual` to `automated` is two edits: write a module in
`numtrail/modules/`, then set the site's `probe` field in `catalog.py` to the
module name. `tests/test_catalog.py` fails if a catalogue entry names a probe
that doesn't exist, which is the guard against the catalogue lying.
