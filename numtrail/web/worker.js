/**
 * numtrail check API for Cloudflare Workers.
 *
 * Why this file exists: GitHub Pages is static-only, and a browser cannot call
 * instagram.com directly (CORS). So the UI can live on Pages while this worker
 * does the talking. Deploy it, then either
 *   - set window.NUMTRAIL_CONFIG.apiBase in config.js to the worker URL, or
 *   - open the Pages site with ?api=https://numtrail.<you>.workers.dev
 *
 * Same contract as numtrail/web/server.py:
 *   POST /api/check  {"phone":"+60123456789"} -> application/x-ndjson
 *   GET  /healthz
 *   GET  /api/catalog  (proxied from your Pages origin so there is one source)
 *
 * Coverage caveat, stated plainly: this edge build ports the four JSON APIs and
 * two of the three web flows. Amazon's /ax/claim flow needs HTML form parsing
 * and cookie state, so it reports `skipped` here — run the Python server if you
 * need it. Everything else behaves identically.
 *
 * Secrets (Dashboard -> Worker -> Settings -> Variables):
 *   TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, NUMVERIFY_KEY,
 *   ABSTRACTAPI_PHONE_KEY, VERIPHONE_KEY     (all optional; absent = "needs a key")
 * Vars: ALLOW_ORIGIN (e.g. https://you.github.io), STATIC_ORIGIN (catalog source)
 */

const RATE_LIMIT = 8;
const RATE_WINDOW_MS = 60_000;
const buckets = new Map(); // per-isolate only; see README for a durable option

/* ------------------------------------------------------------------ helpers */
const DIAL_CODES = {
  1: "US", 7: "RU", 20: "EG", 27: "ZA", 30: "GR", 31: "NL", 32: "BE", 33: "FR",
  34: "ES", 36: "HU", 39: "IT", 40: "RO", 41: "CH", 43: "AT", 44: "GB", 45: "DK",
  46: "SE", 47: "NO", 48: "PL", 49: "DE", 51: "PE", 52: "MX", 54: "AR", 55: "BR",
  56: "CL", 57: "CO", 60: "MY", 61: "AU", 62: "ID", 63: "PH", 64: "NZ", 65: "SG",
  66: "TH", 81: "JP", 82: "KR", 84: "VN", 86: "CN", 90: "TR", 91: "IN", 92: "PK",
  94: "LK", 98: "IR", 212: "MA", 213: "DZ", 216: "TN", 218: "LY", 220: "GM",
  221: "SN", 233: "GH", 234: "NG", 254: "KE", 255: "TZ", 256: "UG", 263: "ZW",
  351: "PT", 352: "LU", 353: "IE", 354: "IS", 358: "FI", 370: "LT", 371: "LV",
  372: "EE", 380: "UA", 385: "HR", 420: "CZ", 421: "SK", 503: "SV", 504: "HN",
  505: "NI", 506: "CR", 507: "PA", 852: "HK", 855: "KH", 880: "BD", 886: "TW",
  961: "LB", 962: "JO", 965: "KW", 966: "SA", 968: "OM", 971: "AE", 972: "IL",
  973: "BH", 974: "QA", 977: "NP", 998: "UZ",
};
const CODE_LENGTHS = Object.keys(DIAL_CODES).sort((a, b) => b.length - a.length);

// Snapchat wants a territory, not a dialling code.
const TERRITORY = {
  1: "US", 20: "EG", 27: "ZA", 30: "GR", 31: "NL", 32: "BE", 33: "FR", 34: "ES",
  36: "HU", 39: "IT", 40: "RO", 41: "CH", 43: "AT", 44: "GB", 45: "DK", 46: "SE",
  47: "NO", 48: "PL", 49: "DE", 52: "MX", 55: "BR", 60: "MY", 61: "AU", 62: "ID",
  63: "PH", 64: "NZ", 65: "SG", 66: "TH", 81: "JP", 82: "KR", 84: "VN", 86: "CN",
  90: "TR", 91: "IN", 92: "PK", 971: "AE", 972: "IL", 966: "SA", 974: "QA",
};

function parsePhone(raw) {
  const digits = String(raw || "").replace(/\D/g, "");
  if (digits.length < 4) throw new Error("no digits in that number");
  let dial = "";
  for (const code of CODE_LENGTHS) {
    if (digits.startsWith(code)) { dial = code; break; }
  }
  if (!dial) throw new Error("no dialling code found; send it like +60123456789");
  const national = digits.slice(dial.length).replace(/^0+/, "") || "0";
  if (national.length < 3 || national.length > 12) throw new Error("implausible national part");
  return {
    dial, national, iso: DIAL_CODES[dial] || null,
    e164: `+${dial}${national}`, digits: `${dial}${national}`,
  };
}

const cors = (env) => {
  const headers = { "access-control-allow-headers": "content-type",
                    "access-control-allow-methods": "POST, GET, OPTIONS",
                    vary: "Origin" };
  if (env.ALLOW_ORIGIN) headers["access-control-allow-origin"] = env.ALLOW_ORIGIN;
  return headers;
};

function limited(ip) {
  const now = Date.now();
  const hits = (buckets.get(ip) || []).filter((t) => now - t < RATE_WINDOW_MS);
  if (hits.length >= RATE_LIMIT) {
    return Math.max(1, Math.ceil((RATE_WINDOW_MS - (now - hits[0])) / 1000));
  }
  hits.push(now);
  buckets.set(ip, hits);
  return null;
}

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
           "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36";

const TIMEOUT = (ms = 20_000) => AbortSignal.timeout(ms);

function res(module, status, detail) {
  return { type: "result", module, status, found: status === "found",
           detail: detail || null };
}

/* ------------------------------------------------------------------- probes */
const IG_KEY = "e6358aeede676184b9fe702b30f4fd35e71744605e39d2181a34cede076b3c33";

async function hmacHex(key, message) {
  const enc = new TextEncoder();
  const cryptoKey = await crypto.subtle.importKey(
    "raw", enc.encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", cryptoKey, enc.encode(message));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function probeInstagram(phone) {
  const payload = {
    login_attempt_count: "0", directly_sign_in: "true", source: "default",
    q: phone.digits, ig_sig_key_version: "4",
  };
  const body = JSON.stringify(payload);
  const signed = `ig_sig_key_version=4&signed_body=${await hmacHex(IG_KEY, body)}.${encodeURIComponent(body)}`;

  const r = await fetch("https://i.instagram.com/api/v1/users/lookup/", {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
      "user-agent": "Instagram 219.0.0.12.117 Android",
      "x-fb-http-engine": "Liger", "accept-language": "en-US",
    },
    body: signed, signal: TIMEOUT(),
  });
  if (r.status === 429) return res("instagram", "rate_limited", "429 from the lookup endpoint");
  if (r.status === 401 || r.status === 403) return res("instagram", "blocked", `HTTP ${r.status}`);
  let data = {};
  try { data = await r.json(); } catch { return res("instagram", "blocked", "non-JSON response"); }
  const message = String(data.message || "");
  if (message === "No users found") return res("instagram", "not_found", "no account on this number");
  if (data.users || data.status === "ok") return res("instagram", "found", message || "account exists");
  if (/checkpoint|challenge/i.test(message)) return res("instagram", "blocked", message);
  return res("instagram", "error", message || `unexpected payload (${r.status})`);
}

async function probeSnapchat(phone) {
  const territory = TERRITORY[phone.dial] || phone.iso;
  if (!territory) return res("snapchat", "skipped", `no territory mapping for +${phone.dial}`);

  const base = "https://accounts.snapchat.com";
  const prime = await fetch(`${base}/`, { headers: { "user-agent": UA }, signal: TIMEOUT() });
  if (prime.status >= 500) return res("snapchat", "blocked", `HTTP ${prime.status}`);
  const xsrf = /xsrf_token=([^;]+)/.exec(prime.headers.get("set-cookie") || "")?.[1] || "";

  const r = await fetch(`${base}/accounts/validate_phone_number`, {
    method: "POST",
    headers: {
      "user-agent": UA, origin: base, referer: `${base}/`,
      "content-type": "application/x-www-form-urlencoded; charset=utf-8",
      "x-requested-with": "XMLHttpRequest",
      cookie: `xsrf_token=${xsrf}`,
    },
    body: new URLSearchParams({
      phone_country_code: territory, phone_number: phone.national, xsrf_token: xsrf,
    }).toString(),
    signal: TIMEOUT(),
  });
  if (r.status === 429) return res("snapchat", "rate_limited", "HTTP 429");
  if (r.status >= 400) return res("snapchat", "blocked", `HTTP ${r.status}`);
  let data = {};
  try { data = await r.json(); } catch {
    return res("snapchat", "blocked",
      "answered HTML instead of JSON: endpoint now needs a real session");
  }
  if (data.status_code === "TAKEN_NUMBER") return res("snapchat", "found", "number already attached to an account");
  if (data.status_code === "OK") return res("snapchat", "not_found", "number still available at signup");
  return res("snapchat", /RATE/.test(String(data.status_code)) ? "rate_limited" : "error",
             String(data.status_code || "unexpected payload"));
}

async function probeTwilio(phone, env) {
  if (!env.TWILIO_ACCOUNT_SID || !env.TWILIO_AUTH_TOKEN) {
    return res("twilio", "needs_credentials", "set TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN");
  }
  const auth = btoa(`${env.TWILIO_ACCOUNT_SID}:${env.TWILIO_AUTH_TOKEN}`);
  const r = await fetch(
    `https://lookups.twilio.com/v2/PhoneNumbers/${encodeURIComponent(phone.e164)}?Fields=line_type_intelligence`,
    { headers: { authorization: `Basic ${auth}` }, signal: TIMEOUT() });
  if (r.status === 401) return res("twilio", "needs_credentials", "twilio rejected the SID/token pair");
  if (r.status === 429) return res("twilio", "rate_limited", "HTTP 429");
  if (r.status === 404) return res("twilio", "not_found", "not a valid number");
  const data = await r.json().catch(() => ({}));
  if (data.valid === false) return res("twilio", "not_found", "invalid number");
  const intel = data.line_type_intelligence || {};
  const detail = [intel.carrier_name || "unknown carrier", intel.type ||
                  "unknown type", data.country_code].filter(Boolean).join(" · ");
  return res("twilio", "found", detail || "valid number");
}

async function probeNumverify(phone, env) {
  if (!env.NUMVERIFY_KEY) return res("numverify", "needs_credentials", "set NUMVERIFY_KEY");
  const url = `https://apilayer.net/api/validate?access_key=${env.NUMVERIFY_KEY}` +
              `&number=${phone.digits}&country_code=${phone.iso || ""}&format=1`;
  const r = await fetch(url, { signal: TIMEOUT() });
  if (r.status === 429) return res("numverify", "rate_limited", "HTTP 429");
  const data = await r.json().catch(() => ({}));
  if (data.error) {
    const code = data.error.code;
    if ([101, 102, 103].includes(code)) return res("numverify", "needs_credentials", data.error.type);
    if ([104, 105].includes(code)) return res("numverify", "rate_limited", data.error.type);
    return res("numverify", "error", String(data.error.info || data.error.type || code));
  }
  if (!data.valid) return res("numverify", "not_found", "not a valid number");
  const detail = [data.carrier, data.line_type, data.location].filter(Boolean).join(" · ");
  return res("numverify", "found", detail || "valid number");
}

async function probeAbstractapi(phone, env) {
  if (!env.ABSTRACTAPI_PHONE_KEY) {
    return res("abstractapi", "needs_credentials", "set ABSTRACTAPI_PHONE_KEY");
  }
  const r = await fetch(
    `https://phonevalidation.abstractapi.com/v1/?api_key=${env.ABSTRACTAPI_PHONE_KEY}` +
    `&phone=${encodeURIComponent(phone.e164)}`, { signal: TIMEOUT() });
  if ([401, 422].includes(r.status)) return res("abstractapi", "needs_credentials", "key rejected");
  if (r.status === 429) return res("abstractapi", "rate_limited", "HTTP 429");
  const data = await r.json().catch(() => ({}));
  if (data.error) return res("abstractapi", "error", String(data.error).slice(0, 120));
  if (!data.valid) return res("abstractapi", "not_found", "not a valid number");
  const detail = [data.carrier, data.type, data.location].filter(Boolean).join(" · ");
  return res("abstractapi", "found", detail || "valid number");
}

async function probeVeriphone(phone, env) {
  if (!env.VERIPHONE_KEY) return res("veriphone", "needs_credentials", "set VERIPHONE_KEY");
  const r = await fetch(
    `https://api.veriphone.io/v2/verify?phone=${encodeURIComponent(phone.e164)}` +
    `&key=${env.VERIPHONE_KEY}`, { signal: TIMEOUT() });
  if (r.status === 429) return res("veriphone", "rate_limited", "HTTP 429");
  const data = await r.json().catch(() => ({}));
  if (String(data.status).toLowerCase() === "error") {
    return res("veriphone", "needs_credentials", String(data.message || "request rejected").slice(0, 120));
  }
  if (data.phone_valid === false) return res("veriphone", "not_found", "not a valid number");
  const detail = [data.carrier, data.phone_type, data.phone_region].filter(Boolean).join(" · ");
  return res("veriphone", "found", detail || "valid number");
}


/* ------------------------------------------- keyed lookups (compact registry) */
// Each entry builds a request and maps the provider's documented payload onto a
// status. Same shape rules as the Python modules, so results match.
const KEYED = {
  vonage: {
    key: ["VONAGE_API_KEY", "VONAGE_API_SECRET"],
    build: (ph, env) => `https://api.nexmo.com/ni/basic/json?api_key=${env.VONAGE_API_KEY}` +
      `&api_secret=${env.VONAGE_API_SECRET}&number=${encodeURIComponent(ph.e164)}`,
    parse: (d) => d.status === 0
      ? ["found", [d.country_code, d.national_format].filter(Boolean).join(" · ") || "valid number"]
      : d.status === 3 ? ["not_found", d.error_text || "invalid number"]
      : d.status === 4 ? ["needs_credentials", d.error_text || "bad key pair"]
      : d.status === 1 ? ["rate_limited", d.error_text || "throttled"]
      : ["error", `unexpected status ${d.status}`],
  },
  ipqualityscore: {
    key: ["IPQS_KEY"],
    build: (ph, env) => `https://ipqualityscore.com/api/json/phone/${env.IPQS_KEY}/` +
      encodeURIComponent(ph.e164),
    parse: (d) => {
      if (d.success === false) {
        const m = String(d.message || "request rejected");
        return [/key|authori/i.test(m) ? "needs_credentials"
                : /quota|limit/i.test(m) ? "rate_limited" : "error", m.slice(0, 120)];
      }
      if (d.valid === false) return ["not_found", "not a valid number"];
      const bits = [d.carrier, d.line_type, d.active != null ? `active=${d.active}` : "",
                    d.fraud_score != null ? `fraud=${d.fraud_score}` : ""].filter(Boolean);
      return ["found", bits.join(" · ") || "valid number"];
    },
  },
  telesign: {
    key: ["TELESIGN_CUSTOMER_ID", "TELESIGN_API_KEY"],
    build: (ph) => `https://rest-ww.telesign.com/v1/phoneid/${encodeURIComponent(ph.e164)}`,
    init: (env) => ({
      method: "POST",
      headers: { authorization: `Basic ${btoa(`${env.TELESIGN_CUSTOMER_ID}:${env.TELESIGN_API_KEY}`)}`,
                 "content-type": "application/x-www-form-urlencoded" },
      body: "account_lifecycle_event=create",
    }),
    parse: (d) => {
      const code = d.status && d.status.code;
      const desc = (d.status && d.status.description) || "unexpected status";
      if (code === 300) {
        const bits = [(d.phone_type || {}).description, (d.carrier || {}).name,
                      (d.location || {}).city].filter(Boolean);
        return ["found", bits.join(" · ") || "valid number"];
      }
      return [[101, 102, 103, 104].includes(code) ? "not_found"
              : [201, 202].includes(code) ? "rate_limited" : "error", desc];
    },
  },
  plivo: {
    key: ["PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN"],
    build: (ph) => `https://lookups.plivo.com/v1/Number/${encodeURIComponent(ph.e164)}/?type=carrier`,
    init: (env) => ({
      headers: { authorization: `Basic ${btoa(`${env.PLIVO_AUTH_ID}:${env.PLIVO_AUTH_TOKEN}`)}` },
    }),
    notFound: [404],
    parse: (d) => {
      const bits = [(d.carrier || {}).name, d.country, d.phone_number].filter(Boolean);
      return ["found", bits.join(" · ") || "valid number"];
    },
  },
  bird: {
    key: ["BIRD_ACCESS_KEY"],
    build: (ph) => `https://lookup.messagebird.com/1/lookup/${encodeURIComponent(ph.e164)}`,
    init: (env) => ({ headers: { authorization: `AccessKey ${env.BIRD_ACCESS_KEY}` } }),
    notFound: [404],
    parse: (d) => ["found", [d.countryCode, d.carrier, d.type, d.ported]
      .filter(Boolean).join(" · ") || "valid number"],
  },
  numlookupapi: {
    key: ["NUMLOOKUPAPI_KEY"],
    build: (ph, env) => `https://api.numlookupapi.com/v1/validate/` +
      `${encodeURIComponent(ph.e164)}?apikey=${env.NUMLOOKUPAPI_KEY}`,
    parse: (d) => {
      if (d.error) {
        const code = typeof d.error === "object" ? d.error.code : d.error;
        return [/key/i.test(String(code)) ? "needs_credentials" : "error", String(code)];
      }
      if (d.valid === false) return ["not_found", "not a valid number"];
      return ["found", [d.country_name, d.carrier, d.line_type].filter(Boolean).join(" · ")
              || "valid number"];
    },
  },
  trestle: {
    key: ["TRESTLE_API_KEY"],
    build: (ph, env) => `https://api.trestleiq.com/3.0/phone_intel?phone=` +
      `${encodeURIComponent(ph.e164)}&api_key=${env.TRESTLE_API_KEY}`,
    notFound: [404],
    parse: (d) => d.is_valid === false ? ["not_found", "not a valid number"]
      : ["found", [d.carrier, d.line_type, d.country, d.is_prepaid ? "prepaid" : ""]
          .filter(Boolean).join(" · ") || "valid number"],
  },
  mocean: {
    key: ["MOCEAN_API_KEY", "MOCEAN_API_SECRET"],
    build: (ph, env) => `https://rest.moceanapi.com/rest/2/number/lookup?mocean-api-key=` +
      `${env.MOCEAN_API_KEY}&mocean-api-secret=${env.MOCEAN_API_SECRET}` +
      `&mocean-to=${encodeURIComponent(ph.e164)}`,
    parse: (d) => {
      if (d.status !== 0) {
        const m = String(d.err_msg || d.msg || "request rejected");
        return [/auth|key/i.test(m) ? "needs_credentials" : "not_found", m.slice(0, 120)];
      }
      return ["found", [d.country, d.carrier, d.number].filter(Boolean).join(" · ")
              || "valid number"];
    },
  },
};

async function probeKeyed(name, phone, env) {
  const spec = KEYED[name];
  if (!spec) return res(name, "skipped", "not implemented in the edge build");
  if (!spec.key.every((k) => env[k])) {
    return res(name, "needs_credentials", "set " + spec.key.join(" + "));
  }
  const init = spec.init ? spec.init(env) : {};
  const r = await fetch(spec.build(phone, env), { ...init, signal: TIMEOUT() });
  if (r.status === 401 || r.status === 403) {
    return res(name, "needs_credentials", "provider rejected the credentials");
  }
  if (r.status === 429) return res(name, "rate_limited", "HTTP 429");
  if ((spec.notFound || []).includes(r.status)) return res(name, "not_found", `HTTP ${r.status}`);
  if (r.status >= 500) return res(name, "error", `HTTP ${r.status}`);
  const data = await r.json().catch(() => null);
  if (!data) return res(name, "error", "non-JSON response");
  const [status, detail] = spec.parse(data);
  return res(name, status, detail);
}


/* --------------------------------------------- offline probes (no fetch at all) */
const MY_BLOCKS = {
  12: ["Maxis", 7, "allocated"], 17: ["Maxis", 7, "allocated"],
  13: ["Celcom", 7, "allocated"], 19: ["Celcom", 7, "allocated"],
  16: ["Digi", 7, "allocated"], 18: ["U Mobile", 7, "allocated"],
  10: ["Celcom / XOX / MVNOs", 7, "shared"], 14: ["unifi Mobile / MVNOs", 7, "shared"],
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

function decodeMalaysia(phone) {
  if (phone.dial !== "60") return ["skipped", `Malaysian numbering only (this is +${phone.dial})`];
  const nsn = phone.national.replace(/^0+/, "");
  if (!/^\d+$/.test(nsn)) return ["not_found", "not a numeric national number"];
  if (nsn.startsWith("1")) {
    if (nsn.startsWith("11")) {
      if (nsn.length !== 10) return ["not_found", `011 block needs 10 NSN digits, got ${nsn.length}`];
      return ["found", "mobile · 011 universal block · 8-digit subscriber · allocated to all operators · MNP applies"];
    }
    const block = nsn.slice(0, 2);
    const spec = MY_BLOCKS[block];
    if (!spec) return ["not_found", `01x block 0${block} is not in the Malaysian plan`];
    const [owner, subscriber, certainty] = spec;
    if (nsn.length !== 2 + subscriber) {
      return ["not_found", `0${block} block expects ${2 + subscriber} NSN digits, got ${nsn.length}`];
    }
    const qualifier = certainty === "allocated" ? " (portable — active carrier may differ)" : "";
    return ["found", `mobile · 0${block} block · ${subscriber}-digit subscriber · allocated to ${owner}${qualifier}`];
  }
  const area = (nsn.startsWith("8") && nsn.length > 2) ? nsn.slice(0, 2) : nsn.slice(0, 1);
  const where = MY_AREAS[area];
  if (!where) return ["not_found", `0${nsn.slice(0, 1)} is not an allocated Malaysian area code`];
  if (nsn.length < 7 || nsn.length > 9) return ["not_found", `fixed-line NSN of ${nsn.length} digits is implausible`];
  return ["found", `fixed line · area 0${area} (${where})`];
}

const LOCAL = {
  my_number: (phone) => decodeMalaysia(phone),
};

const PROBES = {
  instagram: probeInstagram,
  snapchat: probeSnapchat,
  twilio: probeTwilio,
  numverify: probeNumverify,
  abstractapi: probeAbstractapi,
  veriphone: probeVeriphone,
};

async function safeProbe(name, phone, env) {
  if (name === "amazon") {
    return res("amazon", "skipped", "HTML claim flow not ported to the edge build; run the Python server");
  }
  if (LOCAL[name]) {
    const [status, detail] = LOCAL[name](phone);
    return res(name, status, detail);
  }
  if (KEYED[name]) {
    try {
      return await probeKeyed(name, phone, env);
    } catch (err) {
      return res(name, "error", `${err.name}: ${err.message}`.slice(0, 240));
    }
  }
  const fn = PROBES[name];
  if (!fn) return res(name, "skipped", "not implemented in the edge build");
  try {
    return await fn(phone, env);
  } catch (err) {
    return res(name, "error", `${err.name}: ${err.message}`.slice(0, 240));
  }
}

/* --------------------------------------------------------------- entrypoint */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const headers = cors(env);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers });

    if (url.pathname === "/healthz") {
      return Response.json({
        ok: true,
        probes: [...Object.keys(PROBES), ...Object.keys(KEYED), ...Object.keys(LOCAL)],
        sites: 161, runtime: "cloudflare-worker",
      }, { headers });
    }

    if (url.pathname === "/api/catalog" && env.STATIC_ORIGIN) {
      const upstream = await fetch(env.STATIC_ORIGIN.replace(/\/$/, "") + "/catalog.json");
      return new Response(upstream.body, {
        status: upstream.status,
        headers: { ...headers, "content-type": "application/json" },
      });
    }

    if (url.pathname !== "/api/check") {
      return Response.json({ error: "not found" }, { status: 404, headers });
    }
    if (request.method !== "POST") {
      return Response.json({ error: "use POST" }, { status: 405, headers });
    }

    const ip = request.headers.get("cf-connecting-ip") || "unknown";
    const wait = limited(ip);
    if (wait !== null) {
      return Response.json({ error: `rate limited, retry in ${wait}s` },
        { status: 429, headers: { ...headers, "retry-after": String(wait) } });
    }

    let phone;
    try {
      const body = await request.json().catch(() => ({}));
      phone = parsePhone(body.phone);
    } catch (err) {
      return Response.json({ error: err.message }, { status: 400, headers });
    }

    const names = ["my_number", "instagram", "snapchat", "amazon", "twilio",
                   "numverify", "abstractapi", "veriphone", "vonage", "ipqualityscore",
                   "telesign", "plivo", "bird", "numlookupapi", "trestle", "mocean"];
    const started = Date.now();
    const encoder = new TextEncoder();

    const stream = new ReadableStream({
      async start(controller) {
        const send = (obj) => controller.enqueue(encoder.encode(JSON.stringify(obj) + "\n"));
        send({ type: "meta", phone: phone.e164, iso: phone.iso,
               automated: names.length, total: 161 });
        await Promise.all(names.map(async (name) => {
          send(await safeProbe(name, phone, env));
        }));
        send({ type: "done", elapsed: (Date.now() - started) / 1000 });
        controller.close();
      },
    });

    return new Response(stream, {
      headers: { ...headers, "content-type": "application/x-ndjson", "cache-control": "no-store" },
    });
  },
};
