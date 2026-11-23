# numtrail

Trace where a phone number is registered, with a pluggable module registry.

*(sample output, illustrative — what you get depends on your IP, proxies and keys)*

```
$ numtrail +60123456789
+60 123456789  (MY)
------------------------------------------------
    module       domain           status             detail
+   instagram    instagram.com    found              account exists
-   amazon       amazon.com       not found          Amazon does not recognise…
#   snapchat     snapchat.com     blocked            endpoint now needs a real session…
k   numverify    numverify.com    needs credentials  set NUMVERIFY_KEY
x   twilio       twilio.com       rate limited; rotate IP or key

1/7 yes · 1 no · 1 limited · 1 blocked · 3 no key
finished in 2.73s
[+] found   [-] not registered   [x] rate limited   [#] blocked   [k] needs key   [!] error   [.] skipped
```

numtrail is a rewrite of [ignorant](https://github.com/megadose/ignorant) that
answers the question it can actually answer. Public endpoints get bot-walled,
rate-limit, or migrate; a tool that turns every one of those into "not found"
is lying to you. numtrail reports **seven** distinct outcomes, so you can tell
"this number has no Instagram account" apart from "Instagram told me to go away".

## Install

```bash
pip install -e ".[dev]"      # from this checkout
numtrail --list-modules
```

Requires Python 3.9+. Two runtime dependencies (`httpx`, `beautifulsoup4`), plus
`rich` if you want the pretty table.

## Usage

```bash
numtrail +60123456789              # E.164-ish, no validation theatre
numtrail 60 123456789              # dial code + national number
numtrail +60123456789 --only-found
numtrail +60123456789 --json
numtrail +60123456789 --csv out.csv
numtrail +60123456789 -m instagram,twilio
numtrail +60123456789 -x snapchat --tags social
numtrail +60123456789 --concurrency 4 --timeout 8 --retries 2
numtrail +60123456789 --proxy http://user:pass@host:8080
numtrail --list-modules
```

Exit codes: `0` ran, `2` bad number / unknown module.

### Statuses

| symbol | status | meaning |
| --- | --- | --- |
| `+` | `found` | the module's question answered yes |
| `-` | `not_found` | answered no |
| `x` | `rate_limited` | 429 / quota spent — rotate IP or key, or retry |
| `#` | `blocked` | captcha, WAF, login wall, migrated endpoint |
| `k` | `needs_credentials` | module wants an env var |
| `!` | `error` | timeout, DNS, unparsable response |
| `.` | `skipped` | deselected, or prerequisite missing |

Only `found` / `not_found` are conclusive. `--retries` only re-runs `rate_limited`
and `error`.

### Semantics

Modules carry a `semantic` field because they answer two different questions:

- `account_exists` — is this number attached to an account on this service
  (the legacy web modules).
- `number_valid` — is this line real, and what is it (the keyed API modules).

The table never mixes the two up, and JSON output keeps them separate.

## Modules

| module | tier | semantic | needs | notes |
| --- | --- | --- | --- | --- |
| `instagram` | legacy | account_exists | — | HMAC-signed account lookup; 429s easily |
| `snapchat` | legacy | account_exists | `NUMTRAIL_SNAPCHAT_COOKIE` (optional) | signup phone validator |
| `amazon` | legacy | account_exists | `NUMTRAIL_AMAZON_COOKIE` (optional) | `/ax/claim` sign-in flow |
| `twilio` | api | number_valid | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | carrier + line type |
| `numverify` | api | number_valid | `NUMVERIFY_KEY` | carrier + location |
| `abstractapi` | api | number_valid | `ABSTRACTAPI_PHONE_KEY` | validity + carrier |
| `veriphone` | api | number_valid | `VERIPHONE_KEY` | free tier available |
| `vonage` | api | number_valid | `VONAGE_API_KEY`, `VONAGE_API_SECRET` | Number Insight Basic |
| `ipqualityscore` | api | number_valid | `IPQS_KEY` | validity + active line + fraud score |
| `telesign` | api | number_valid | `TELESIGN_CUSTOMER_ID`, `TELESIGN_API_KEY` | PhoneID |
| `plivo` | api | number_valid | `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN` | Lookup v1 |
| `bird` | api | number_valid | `BIRD_ACCESS_KEY` | Lookup HLR (ex-MessageBird) |
| `numlookupapi` | api | number_valid | `NUMLOOKUPAPI_KEY` | free tier available |
| `trestle` | api | number_valid | `TRESTLE_API_KEY` | validity + prepaid flag |
| `mocean` | api | number_valid | `MOCEAN_API_KEY`, `MOCEAN_API_SECRET` | carrier + reachability |

Cookie-backed modules accept a browser `Cookie:` header through the env var, so
you can drive them with a real session instead of fighting the bot-wall.

**Tier honesty:** `legacy` modules hit undocumented endpoints that each company
changes at will. They were re-verified against the live flows while this was
written (Nov 2025) from a datacentre IP, which is the worst case: Instagram
answers 429, Snapchat answers HTML to scripted clients, Amazon answers 406 on the
claim POST. From a residential IP, or with `--proxy` and the session cookies
above, they answer for real. If one starts returning `blocked` forever, the flow
changed — the fix belongs in `numtrail/modules/legacy/<site>.py`.

```bash
export NUMVERIFY_KEY=... TWILIO_ACCOUNT_SID=... TWILIO_AUTH_TOKEN=...
numtrail +60123456789 -m numverify,twilio
```


## Web version

A browser UI ships in this repo: it opens showing **only the 15 automated
probes**, all live, in a filterable grid. Press "everything" if you also want the
145 link-out services. JSON export, shareable run links, mobile layout.

```bash
numtrail-web --port 8000        # after: pip install -e .
# open http://localhost:8000
```

![numtrail web UI: 15 automated probes, each reporting found / blocked / limited / needs a key](docs/screenshot.png)

Deployed as static files only (no backend attached), the same page refuses to
fake it and shows the deploy steps instead:

![the setup panel shown when no API is attached](docs/screenshot-no-backend.png)

Static-only hosting is not enough on its own, and the docs are blunt about why:
GitHub Pages can serve the UI, but a browser can't call `instagram.com` (CORS),
so the checks need a server. Two supported shapes:

| shape | probes | cost | how |
| --- | --- | --- | --- |
| Render blueprint (`render.yaml` → `Dockerfile`) | all 15 | free tier | one click, UI + API on one origin |
| GitHub Pages UI + Cloudflare Worker (`worker.js`) | 14 (no `amazon`) | free | one repo variable wires them together |
| Docker / VPS | all 15 | your box | `docker run -p 8000:8000 numtrail` |

Deploying takes one of three routes; **[DEPLOY.md](DEPLOY.md)** has the exact
commands for each.

Full deployment walkthrough — Pages workflow, `wrangler deploy`, the two worker
variables, and the rate-limit/key warnings — is in
**[numtrail/web/README.md](numtrail/web/README.md)**.

If the UI is deployed without a backend it says so and shows deploy steps rather
than inventing results. Deployment walkthrough: [DEPLOY.md](DEPLOY.md).

## The catalogue

`numtrail/catalog.py` is the single source of truth for the 160 sites, grouped
into 11 categories, and `catalog.json` is generated from it:

```bash
python -m numtrail.catalog            # coverage by category
python -m numtrail.catalog --write    # regenerate the JSON the web UI serves
```

Each entry is either **automated** (a module in `numtrail/modules/` asks the
service) or **manual** (the card links to that service's own sign-in flow and you
check it yourself). 15 are automated today — 3 web flows and 12 keyed APIs — and
the other 145 are link-outs because they are captcha'd, undocumented, or behind a
login, and inventing endpoints for them would be fabrication. `tests/test_catalog.py` fails if a catalogue entry
claims a probe that doesn't exist — the catalogue is not allowed to lie.

Promoting one to automated = write a module, set `probe` in `catalog.py`.

## Library use

```python
import asyncio
from numtrail import Settings, run_checks

async def main():
    results = await run_checks("+60123456789",
                               settings=Settings(timeout=8, concurrency=4))
    for r in results:
        print(r.module, r.status.value, r.detail or "")

asyncio.run(main())
```

Blocking shortcut: `from numtrail import check; check("+60123456789")`.

Streaming: `run_checks(..., on_result=lambda r: print(r.module, r.status))`.

## Writing a module

A module is one coroutine plus a decorator. Drop a file anywhere under
`numtrail/modules/`, register it, done — discovery walks the package.

```python
from numtrail import CheckContext, Status
from numtrail.registry import module

@module(name="example", domain="example.com", method="api",
        semantic="account_exists", tags=("api",),
        summary="does example.com know this number")
async def example(ctx: CheckContext):
    r = await ctx.get("https://example.com/api/lookup",
                      params={"msisdn": ctx.phone.e164},
                      headers={"X-Api-Key": ctx.credential("EXAMPLE_KEY")})
    if r.status_code == 404:
        return Status.NOT_FOUND, "no account"
    if r.status_code == 429:
        return Status.RATE_LIMITED
    return Status.FOUND, str(r.json().get("username", ""))
```

Return `Status`, `(Status, detail)` or a full `Result`. Raise
`MissingCredential` for a missing key; any other exception is caught and
reported as `error`, never crashing the run. See `examples/custom_module.py`.

## Layout

```
numtrail/
  phone.py      E.164 parsing, dial-code -> ISO map
  models.py     Status + Result
  registry.py   @module, discovery, selection, return-value normalisation
  engine.py     httpx client, semaphore, per-module timeout, retries
  output.py     table / json / csv / domains
  cli.py        argparse front end
  catalog.py    160 sites: category, domain, lookup path, probe
  modules/
    legacy/     instagram, snapchat, amazon
    api/        twilio, numverify, abstractapi, veriphone, vonage,
                ipqualityscore, telesign, plivo, bird, numlookupapi,
                trestle, mocean
  web/
    static/     the whole front end (no build step)
    server.py   local server: static + streaming /api/check
    worker.js   same API on Cloudflare Workers
DEPLOY.md      Render / Fly / Docker / Pages+Worker, step by step
Dockerfile     UI + API in one container
render.yaml    one-click Render blueprint
fly.toml       fly launch --copy-config
.github/workflows/pages.yml   Pages deploy, wires NUMTRAIL_API_BASE into config.js
tests/          281 python tests + 15 worker tests, all offline
Makefile        install / test / serve / build / docker / check
.github/        ci.yml (tests, packaging) + pages.yml (deploy)
```

## Tests

```bash
python3 -m pytest            # 281 python tests, offline
node tests/worker.test.mjs   # 15 tests for the Cloudflare Worker build
make check                   # both, plus a catalogue-sync check
```

Every module is tested against recorded-shape responses: the 429 path, the
HTML-instead-of-JSON path, the 406 claim path, bad keys, quota errors, garbage
bodies, timeouts, and the retry loop. The web server is tested over real HTTP
with the engine stubbed, and the edge build is tested by driving `worker.js`'s
fetch handler with a stubbed `fetch` and asserting on the NDJSON it streams.

## Build

```bash
make install                 # editable install + dev extras
make build                   # sdist + wheel in dist/, verified in a clean venv
make serve                   # web app on :8000
make docker                  # build and run the container
make check                   # what CI runs
```

`.github/workflows/ci.yml` runs the python suite on 3.9 / 3.12 / 3.13, the worker
tests on node 20, fails if `catalog.json` drifts from `catalog.py`, and builds a
wheel that it installs and smoke-tests before uploading it as an artifact.

## What changed from ignorant

| | ignorant 1.2 | numtrail 0.1 |
| --- | --- | --- |
| concurrency | `trio` + nursery | `asyncio` + semaphore, bounded |
| outcomes | `exists` / `rateLimit` | 7 statuses, `conclusive` and `retryable` properties |
| failures | bare `except:` sets `rateLimit: True` | per-module timeout, bounded retries, module errors contained |
| adding a site | make a file, get auto-imported, function name *is* the module id | `@module(...)` decorator with declared metadata, auto-discovered |
| output | coloured terminal only | table, `--json`, `--csv`, `--only-found`, `--quiet` |
| selection | all modules, always | `-m`, `-x`, `--tags` |
| network | fixed UA string, no proxy | UA pool, `--proxy`, per-module timeouts |
| config | none | env vars per module, `needs_credentials` instead of a silent skip |
| self-update | ran `pip install --upgrade` on every start | removed; that is what package managers are for |
| tests | none | 281, all offline |
| semantics | one question per module | `account_exists` vs `number_valid`, never conflated |

## Next steps to make it fully yours

1. `git init && git add -A && git commit -m "initial"` — the tree is ready for your remote.
2. Rename: change `name`/`console_scripts` in `pyproject.toml`, the package dir, and `numtrail/cli.py`'s `prog=` / `__version__` in `__init__.py`.
3. Add your handle to `pyproject.toml` `authors` and `CREDITS.md`.
4. Publish: `python -m build && twine upload dist/*` (needs a PyPI token).
5. Keep it GPL-3.0 — the three legacy modules are derived from ignorant.

## Scope and law

These checks are the same requests a browser makes at signup, and none of them
notify the target number. That does not make it appropriate to run against
numbers you have no business checking: use it on your own numbers, in a
penetration test you are contracted for, or on synthetic ranges such as
`+1 555 0100`. Your jurisdiction's rules on personal data (GDPR, PDPA, CFAA,
…), plus each site's terms, apply to you, not to this README.

## Licence

GPL-3.0-or-later. It is a derivative of ignorant — see [CREDITS.md](CREDITS.md).
