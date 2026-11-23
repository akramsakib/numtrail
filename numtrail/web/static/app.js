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
    backend: true,
    automated: 0, settled: 0,
  };

  if (cfg.repoUrl) el.repoLink.href = cfg.repoUrl;

  const STATUS_LABEL = {
    found: "found", not_found: "not found", rate_limited: "rate limited",
    blocked: "blocked", needs_credentials: "needs a key", error: "error",
    skipped: "skipped", manual: "manual", pending: "checking…",
  };

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
    link.textContent = site.probe ? "open sign-in →" : "check manually →";
    link.href = lookupUrl(site, el.phone.value || "");

    card.append(head, status, detail, link);
    return card;
  }

  function renderCatalog() {
    const frag = document.createDocumentFragment();
    const ordered = [
      ...state.catalog.filter((s) => s.probe),
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
    setNotice(`trailing ${phone} across ${state.automated} automated probes + ${state.catalog.length - state.automated} manual links…`);
    history.replaceState(null, "", "#" + encodeURIComponent(phone));

    const started = performance.now();
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
    el.run.disabled = true;
    // never leave a card looking "queued" when nothing is going to run
    for (const card of el.grid.children) {
      if (card.dataset.automated !== "1") continue;
      card.dataset.status = "skipped";
      card.classList.remove("pending");
      const label = card.querySelector(".status span");
      if (label) label.textContent = "no backend";
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
    try {
      const health = await fetch(apiBase + "healthz", { cache: "no-store" });
      if (!health.ok) throw new Error(`healthz ${health.status}`);
      const info = await health.json();
      state.backend = true;
      if (info.probes) state.automatedCount = info.probes.length;
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
