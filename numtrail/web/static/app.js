/* numtrail web UI — vanilla, no build step, works straight off GitHub Pages. */
(() => {
  "use strict";

  const cfg = window.NUMTRAIL_CONFIG || {};
  // "" (same origin) or an absolute base that ALWAYS ends in "/" so that
  // apiBase + "api/check" cannot mangle into hostapi/check.
  const rawApiBase = String(new URLSearchParams(location.search).get("api")
    || cfg.apiBase || "").trim();
  const apiBase = rawApiBase ? rawApiBase.replace(/\/+$/, "") + "/" : "";

  const $ = (id) => document.getElementById(id);
  const el = {
    grid: $("grid"), form: $("check-form"), phone: $("phone"), run: $("run"),
    notice: $("notice"), meter: $("meter"), bar: document.querySelector(".meter .bar"),
    statusFilters: $("status-filters"), categoryFilters: $("category-filters"),
    search: $("search"), export: $("export"), siteCount: $("site-count"),
    autoCount: $("auto-count"),
    repoLink: $("repo-link"),
  };

  const state = {
    catalog: [], results: new Map(), filter: "automated", category: "all",
    search: "", running: false, lastPhone: "", elapsed: 0, automatedCount: 0,
    backend: true, snapshots: null,
    automated: 0, settled: 0,
  };

  if (cfg.repoUrl) el.repoLink.href = cfg.repoUrl;

  const PLURAL_LABEL = {
    found: "found", not_found: "not found", blocked: "blocked",
    rate_limited: "rate limited", needs_credentials: "need a key", error: "errored",
  };
  const STATUS_LABEL = {
    found: "found", not_found: "not found", rate_limited: "rate limited",
    blocked: "blocked", needs_credentials: "needs a key", error: "error",
    skipped: "skipped", manual: "manual", pending: "checking…",
  };


  /* ------------------------------------------------- offline probes (no API)
   * Mirrors numtrail/modules/offline/my_number.py. Pure, synchronous, and it
   * runs with or without a backend — that is the point: a static GitHub Pages
   * build can still answer real questions about a Malaysian number.
   */
  const MY_BLOCKS = {
    12: ["Maxis", 7, "allocated"], 17: ["Maxis", 7, "allocated"],
    13: ["Celcom", 7, "allocated"], 19: ["Celcom", 7, "allocated"],
    16: ["Digi", 7, "allocated"], 18: ["U Mobile", 7, "allocated"],
    10: ["Celcom / XOX / MVNOs", 7, "shared"],
    14: ["unifi Mobile / MVNOs", 7, "shared"],
    15: ["MVNOs (Tune Talk, XOX, redONE…)", 7, "shared"],
    11: ["all operators (universal block, 2010)", 8, "shared"],
  };
  const MY_AREAS = {
    3: "Kuala Lumpur / Selangor", 4: "Penang / Kedah / Perlis", 5: "Perak",
    6: "Melaka / Negeri Sembilan / Johor (north)", 7: "Johor",
    82: "Sarawak (Kuching)", 83: "Sarawak (Sri Aman)", 84: "Sarawak (Sibu)",
    85: "Sarawak (Miri / Limbang)", 86: "Sarawak (Bintulu)",
    87: "Sabah (interior)", 88: "Sabah (Kota Kinabalu)", 89: "Sabah (Sandakan / Tawau)",
    9: "Kelantan / Terengganu / Pahang",
  };

  function decodeMalaysia(digits) {
    if (!digits.startsWith("60")) return ["skipped", `Malaysian numbering only (this is +${digits.slice(0, 2)})`];
    const nsn = digits.slice(2).replace(/^0+/, "");
    if (!/^\d+$/.test(nsn)) return ["not_found", "not a numeric national number"];

    if (nsn.startsWith("1")) {
      if (nsn.startsWith("11")) {
        if (nsn.length !== 10) {
          return ["not_found", `011 block needs 10 NSN digits, got ${nsn.length}`];
        }
        return ["found", "mobile · 011 universal block · 8-digit subscriber · " +
                        "allocated to all operators (universal block, 2010) · MNP applies"];
      }
      const block = nsn.slice(0, 2);
      const spec = MY_BLOCKS[block];
      if (!spec) return ["not_found", `01x block 0${block} is not in the Malaysian plan`];
      const [owner, subscriber, certainty] = spec;
      const expected = 2 + subscriber;
      if (nsn.length !== expected) {
        return ["not_found", `0${block} block expects ${expected} NSN digits, got ${nsn.length}`];
      }
      const qualifier = certainty === "allocated" ? " (portable — active carrier may differ)" : "";
      return ["found", `mobile · 0${block} block · ${subscriber}-digit subscriber · ` +
                       `allocated to ${owner}${qualifier}`];
    }

    const area = (nsn.startsWith("8") && nsn.length > 2 ? nsn.slice(0, 2) : nsn.slice(0, 1));
    const where = MY_AREAS[area];
    if (!where) return ["not_found", `0${nsn.slice(0, 1)} is not an allocated Malaysian area code`];
    if (nsn.length < 7 || nsn.length > 9) {
      return ["not_found", `fixed-line NSN of ${nsn.length} digits is implausible`];
    }
    return ["found", `fixed line · area 0${area} (${where})`];
  }

  function prettyMY(digits) {
    const nsn = digits.slice(2).replace(/^0+/, "");
    const local = "0" + nsn;
    if (nsn.startsWith("11") && nsn.length === 10) return `${local.slice(0, 3)}-${local.slice(3, 7)} ${local.slice(7)}`;
    if (local.length === 10) return `${local.slice(0, 3)}-${local.slice(3, 6)} ${local.slice(6)}`;
    return `${local.slice(0, 2)}-${local.slice(2, 5)} ${local.slice(5)}`;
  }

  function relTime(iso) {
    const then = Date.parse(iso);
    if (!then) return "committed";
    const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    if (mins < 1440) return `${Math.round(mins / 60)}h ago`;
    return `${Math.round(mins / 1440)}d ago`;
  }

  function applySnapshot(phone) {
    const e164 = "+" + String(phone).replace(/\D/g, "");
    const entry = state.snapshots && state.snapshots.entries && state.snapshots.entries[e164];
    if (!entry) return 0;
    let applied = 0;
    for (const r of entry.results) {
      if (r.status === "skipped") continue;
      // the offline probe runs right here, in this browser, every time — it is
      // fresher than anything committed, so the snapshot must not overwrite it.
      const seen = state.results.get(r.module);
      if (seen && seen.local) continue;
      const card = el.grid.querySelector(`[data-site="${r.module}"]`);
      if (!card) continue;
      const stamp = `snapshot ${relTime(entry.probed_at)}`;
      updateCard({ ...r, detail: r.detail ? `${r.detail} · ${stamp}` : stamp });
      state.settled++;
      applied++;
    }
    return applied;
  }

  function runLocalProbes(phone) {
    const digits = String(phone).replace(/\D/g, "");
    const out = [];
    for (const site of state.catalog) {
      if (!site.offline || !site.probe) continue;
      const [status, detail] = decodeMalaysia(digits);
      const extra = status === "found" && digits.startsWith("60") ? ` · ${prettyMY(digits)}` : "";
      out.push({ type: "result", module: site.probe, domain: site.domain, status,
                 found: status === "found", detail: detail + extra, local: true });
    }
    return out;
  }

  /* ------------------------------------------------------------------ utils */
  const digitsOf = (raw) => String(raw).replace(/[^\d+]/g, "").replace(/(?!^)\+/g, "");
  const numberParts = (raw) => {
    const d = String(raw).replace(/\D/g, "");
    return { digits: d, plus: "+" + d };
  };

  function lookupUrl(site, phone) {
    const { digits, plus } = numberParts(phone);
    let path = site.lookup || "";
    if (path) {
      path = path
        .replace("{iso_lower}", (site.region || "").toLowerCase())
        .replace("{digits}", digits)
        .replace("{plus}", encodeURIComponent(plus));
      path = path.replace(/\/+$/, "");
    }
    return `https://${site.domain}/${path}`;
  }

  function manualCaption(site) {
    const bits = [site.category, site.region].filter(Boolean);
    if (site.note) bits.unshift(site.note);
    return bits.join(" · ");
  }

  function setNotice(text, kind) {
    el.notice.textContent = text || "";
    el.notice.className = "notice" + (kind ? " " + kind : "");
  }

  /* ----------------------------------------------------------------- render */
  function cardFor(site) {
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.site = site.id;
    card.dataset.category = site.category;
    card.dataset.status = site.probe ? "pending" : "manual";
    card.dataset.automated = site.probe ? "1" : "0";

    const head = document.createElement("div");
    head.className = "head";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = site.name;
    name.title = site.name;
    const dom = document.createElement("span");
    dom.className = "dom";
    dom.textContent = site.domain;
    head.append(name, dom);

    const status = document.createElement("div");
    status.className = "status";
    const dot = document.createElement("i");
    dot.className = "dot";
    const label = document.createElement("span");
    label.textContent = site.probe ? "queued" : "manual";
    status.append(dot, label);

    const detail = document.createElement("p");
    detail.className = "detail";
    detail.textContent = site.probe ? "" : manualCaption(site);

    const link = document.createElement("a");
    link.className = "open";
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    link.textContent = site.offline ? "numbering plan →"
      : site.probe ? "open sign-in →" : "check manually →";
    link.href = lookupUrl(site, el.phone.value || "");

    card.append(head, status, detail, link);
    return card;
  }

  function renderCatalog() {
    const frag = document.createDocumentFragment();
    const ordered = [
      ...state.catalog.filter((s) => s.probe && s.offline),
      ...state.catalog.filter((s) => s.probe && !s.offline),
      ...state.catalog.filter((s) => !s.probe),
    ];
    for (const site of ordered) frag.append(cardFor(site));
    el.grid.replaceChildren(frag);
    el.siteCount.textContent = state.catalog.length;
    state.automatedCount = state.catalog.filter((s) => s.probe).length;
    if (el.autoCount) el.autoCount.textContent = state.automatedCount;
    buildCategoryChips();
    applyFilters();
  }

  function buildCategoryChips() {
    const counts = new Map();
    for (const site of state.catalog) {
      counts.set(site.category, (counts.get(site.category) || 0) + 1);
    }
    const chips = [["all", state.catalog.length], ...counts];
    el.categoryFilters.replaceChildren(
      ...chips.map(([name, count]) => {
        const b = document.createElement("button");
        b.className = "chip" + (name === "all" ? " active" : "");
        b.dataset.category = name;
        b.innerHTML = `${name} <b>${count}</b>`;
        return b;
      })
    );
  }

  function updateCard(result) {
    const card = el.grid.querySelector(`[data-site="${result.module}"]`);
    if (!card) return;
    card.classList.remove("pending");
    if (result.local) card.dataset.local = "1";
    card.dataset.status = result.status;
    const label = card.querySelector(".status span");
    const detail = card.querySelector(".detail");
    if (label) label.textContent = STATUS_LABEL[result.status] || result.status;
    if (detail) detail.textContent = result.detail || "";
    state.results.set(result.module, result);
  }

  function applyFilters() {
    const needle = state.search.trim().toLowerCase();
    let visible = 0;
    for (const card of el.grid.children) {
      const site = state.catalog.find((s) => s.id === card.dataset.site);
      const status = card.dataset.status;
      const okStatus =
        state.filter === "all" ? true :
        state.filter === "automated" ? card.dataset.automated === "1" :
        state.filter === "manual" ? status === "manual" :
        status === state.filter;
      const okCategory = state.category === "all" || card.dataset.category === state.category;
      const okSearch = !needle ||
        (site && (site.name.toLowerCase().includes(needle) ||
                  site.domain.toLowerCase().includes(needle)));
      const show = okStatus && okCategory && okSearch;
      card.hidden = !show;
      if (show) visible++;
    }
    recount();
    return visible;
  }

  function recount() {
    const tally = { all: 0, found: 0, not_found: 0, blocked: 0, rate_limited: 0,
                    manual: 0, automated: 0 };
    for (const card of el.grid.children) {
      tally.all++;
      const s = card.dataset.status;
      if (tally[s] !== undefined) tally[s]++;
      if (card.dataset.automated === "1") tally.automated++;
    }
    for (const [key, value] of Object.entries(tally)) {
      const node = document.getElementById("c-" + key);
      if (node) node.textContent = value;
    }
  }

  /* -------------------------------------------------------------------- run */
  async function runCheck(phone) {
    if (state.running) return;
    state.running = true;
    state.results.clear();
    state.settled = 0;
    state.lastPhone = phone;
    el.run.disabled = true;
    el.meter.hidden = false;
    el.bar.style.width = "0%";

    // reset automated cards to queued, refresh manual links for the new number
    for (const card of el.grid.children) {
      const site = state.catalog.find((s) => s.id === card.dataset.site);
      if (!site) continue;
      card.dataset.status = site.probe ? "pending" : "manual";
    card.dataset.automated = site.probe ? "1" : "0";
      card.classList.toggle("pending", !!site.probe);
      const label = card.querySelector(".status span");
      if (label) label.textContent = site.probe ? "queued" : "manual";
      const detail = card.querySelector(".detail");
      if (detail) detail.textContent = site.probe ? "" : manualCaption(site);
      const link = card.querySelector("a.open");
      if (link) link.href = lookupUrl(site, phone);
    }
    state.automated = state.catalog.filter((s) => s.probe).length;
    setNotice(`trailing ${phone} across ${state.automated} probes…`);
    history.replaceState(null, "", "#" + encodeURIComponent(phone));

    const started = performance.now();

    // always: the probes that need nothing
    for (const result of runLocalProbes(phone)) {
      updateCard(result);
      state.settled++;
    }
    // then: results the GitHub Actions backend already committed
    const fromSnapshot = applySnapshot(phone);

    if (!state.backend) {
      for (const card of el.grid.children) {
        if (card.dataset.automated !== "1") continue;
        const site = state.catalog.find((x) => x.id === card.dataset.site);
        if (site && site.offline) continue;
        if (state.results.has(card.dataset.site)) continue; // snapshot answered it
        card.dataset.status = "skipped";
        card.classList.remove("pending");
        const label = card.querySelector(".status span");
        if (label) label.textContent = "needs a backend";
        const note = card.querySelector(".detail");
        if (note) note.textContent = "attach an API to run this probe";
      }
      state.running = false;
      el.run.disabled = false;
      const live = [...state.results.values()];
      const offline = state.catalog.filter((s) => s.offline && s.probe && state.results.has(s.id)).length;
      const network = live.filter((r) => !r.local);
      if (network.length > 0) {
        const tally = {};
        for (const r of network) tally[r.status] = (tally[r.status] || 0) + 1;
        const parts = Object.entries(tally)
          .sort((a, b) => b[1] - a[1])
          .map(([k, n]) => `${n} ${(n === 1 ? STATUS_LABEL[k] || k : PLURAL_LABEL[k] || k)}`);
        const keys = network.filter((r) => r.status === "needs_credentials").length;
        setNotice(
          `${network.length} of ${state.automated} from the GitHub snapshot` +
          (state.snapshots ? ` (${relTime(state.snapshots.generated_at)})` : "") +
          ` — ${parts.join(", ")}` +
          ` · the offline probe answers live` +
          (keys > 0
            ? `. ${keys} need an API key: add NUMVERIFY_KEY or PROXY_URL as a repo secret and the runner will ask them itself`
            : `. add a number to watchlist.txt, or click Run workflow in the Actions tab`),
          ""
        );
      } else if (offline > 0) {
        setNotice(
          `answered from the offline probe · the other 15 have no committed result for ` +
          `this number yet — add it to watchlist.txt, or run the snapshot workflow ` +
          `with it filled in`, ""
        );
      } else {
        setNotice("nothing could run: no backend, and the number is not in watchlist.txt", "err");
      }
      applyFilters();
      return;
    }

    try {
      await streamCheck(phone);
    } catch (err) {
      if (err && err.name === "AbortError") return;
      setNotice(`check failed: ${err && err.message ? err.message : err}`, "err");
      if (/Failed to fetch|NetworkError|load failed|backend answered 404/i.test(String(err && err.message))) {
        backendMissing();
      }
    } finally {
      state.elapsed = (performance.now() - started) / 1000;
      state.running = false;
      el.run.disabled = false;
      el.bar.style.width = "100%";
      setTimeout(() => { el.meter.hidden = true; }, 700);
      applyFilters();
    }
  }

  async function streamCheck(phone) {
    const res = await fetch(apiBase + "api/check", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ phone }),
    });

    if (res.status === 429) {
      const retry = res.headers.get("retry-after");
      throw new Error(`rate limited by this deployment${retry ? ` — retry in ${retry}s` : ""}`);
    }
    if (res.status === 400) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || "that number did not parse");
    }
    if (!res.ok || !res.body) throw new Error(`backend answered ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (!line) continue;
        handleEvent(JSON.parse(line));
      }
    }
    if (buffer.trim()) handleEvent(JSON.parse(buffer));
  }

  function handleEvent(evt) {
    if (evt.type === "meta") {
      setNotice(`trailing ${evt.phone} (${evt.iso || "??"}) — ${evt.automated} automated probes`);
      return;
    }
    if (evt.type === "result") {
      updateCard(evt);
      state.settled++;
      el.bar.style.width = Math.min(100, (state.settled / Math.max(1, state.automated)) * 100) + "%";
      return;
    }
    if (evt.type === "done") {
      state.elapsed = evt.elapsed || state.elapsed;
      const found = [...state.results.values()].filter((r) => r.status === "found").length;
      const blocked = [...state.results.values()].filter((r) => r.status === "blocked" || r.status === "rate_limited").length;
      setNotice(
        `${found} found · ${blocked} refused to answer · ${state.automated} probes in ${state.elapsed.toFixed(2)}s`
      );
    }
  }

  function backendMissing(detail) {
    state.backend = false;
    const panel = document.getElementById("setup");
    if (panel) panel.hidden = false;
    // Offline probes still run, so the button stays live — a number like +60…
    // gets a real answer even on a static-only deploy.
    el.run.disabled = false;

    // There is a backend, just not a live one: GitHub Actions committed results
    // for watchlist.txt. Say that instead of claiming there is nothing at all.
    const snapCount = state.snapshots && state.snapshots.entries
      ? Object.keys(state.snapshots.entries).length : 0;
    if (snapCount > 0) {
      const h = document.getElementById("setup-title");
      if (h) h.textContent = "Results come from the GitHub Actions snapshot";
      const lede = document.querySelector("#setup p");
      if (lede) {
        lede.innerHTML =
          `Nobody is answering live: GitHub Pages serves files only, so it cannot make the ` +
          `outbound requests these checks need. Instead a scheduled workflow in this repo ` +
          `probes <code>watchlist.txt</code> and any number typed into Actions → snapshot → ` +
          `Run workflow (${snapCount} number${snapCount === 1 ? "" : "s"} committed), ` +
          `commits <code>snapshots.json</code>, and this page reads it. ` +
          `The <strong>offline Malaysian numbering probe runs here, live</strong>, on any number. ` +
          `For live answers on every number, attach one of these:`;
      }
    }
    for (const card of el.grid.children) {
      if (card.dataset.automated !== "1") continue;
      const site = state.catalog.find((x) => x.id === card.dataset.site);
      if (site && site.offline) continue;
      if (state.results.has(card.dataset.site)) continue; // snapshot answered it
      card.dataset.status = "skipped";
      card.classList.remove("pending");
      const label = card.querySelector(".status span");
      if (label) label.textContent = "needs a backend";
      const note = card.querySelector(".detail");
      if (note) note.textContent = "attach an API to run this probe";
    }
    recount();
    applyFilters();
    setNotice(detail || "no API configured on this build — see the setup steps below", "err");
  }

  /* --------------------------------------------------------------- listeners */
  el.form.addEventListener("submit", (e) => {
    e.preventDefault();
    const phone = digitsOf(el.phone.value).trim();
    if (!phone) return;
    runCheck(phone.startsWith("+") ? phone : "+" + phone.replace(/^\+/, ""));
  });

  el.statusFilters.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    for (const c of el.statusFilters.children) c.classList.toggle("active", c === chip);
    state.filter = chip.dataset.status;
    applyFilters();
  });

  el.categoryFilters.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    for (const c of el.categoryFilters.children) c.classList.toggle("active", c === chip);
    state.category = chip.dataset.category;
    applyFilters();
  });

  el.search.addEventListener("input", () => {
    state.search = el.search.value;
    applyFilters();
  });

  el.export.addEventListener("click", () => {
    const payload = {
      phone: state.lastPhone,
      elapsed: state.elapsed,
      snapshot_generated_at: state.snapshots ? state.snapshots.generated_at : null,
      results: [...state.results.values()],
      manual: state.catalog.filter((s) => !s.probe).map((s) => ({
        site: s.name, domain: s.domain, category: s.category,
      })),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `numtrail-${state.lastPhone || "run"}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== el.phone && document.activeElement !== el.search) {
      e.preventDefault();
      el.search.focus();
    }
  });

  /* ------------------------------------------------------------------- boot */
  (async function boot() {
    try {
      const res = await fetch("catalog.json", { cache: "no-cache" });
      if (!res.ok) throw new Error("catalog.json " + res.status);
      const data = await res.json();
      state.catalog = data.sites || [];
    } catch (err) {
      el.grid.innerHTML = `<article class="card"><p class="detail">catalog.json could not be loaded (${err.message}).</p></article>`;
      return;
    }

    const fromHash = decodeURIComponent(location.hash.replace(/^#/, ""));
    if (fromHash) el.phone.value = fromHash;

    renderCatalog();
    const manual = state.catalog.length - state.automatedCount;
    setNotice(
      `${state.automatedCount} automated probes ready — the other ${manual} services are ` +
      `link-out only; press "everything" to see them`
    );
    // Committed results from the GitHub Actions backend (watchlist.txt), if any.
    try {
      const snap = await fetch("snapshots.json", { cache: "no-store" });
      if (snap.ok) state.snapshots = await snap.json();
    } catch (err) {
      state.snapshots = null;
    }

    try {
      const health = await fetch(apiBase + "healthz", { cache: "no-store" });
      if (!health.ok) throw new Error(`healthz ${health.status}`);
      await health.json();   // shape check only; the catalogue decides card count
      state.backend = true;
    } catch (err) {
      backendMissing(
        `this build has no API at ${apiBase || "this origin"} — GitHub Pages cannot run checks. ` +
        `Attach a backend (one repo variable) or self-host; steps are panel-side.`
      );
      return;
    }

    if (fromHash) runCheck(fromHash);
  })();
})();
