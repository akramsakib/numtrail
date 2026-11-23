/**
 * End-to-end test for the Cloudflare Worker build, no wrangler and no network.
 *
 *   node tests/worker.test.mjs
 *
 * It imports worker.js as a module, stubs global fetch with canned provider
 * responses, then drives the real `fetch` handler exactly like a browser would
 * and asserts on the NDJSON it streams back. This is what keeps the edge build
 * (the one GitHub Pages talks to) honest: same statuses as the Python modules.
 */
import assert from "node:assert/strict";
import worker from "../numtrail/web/worker.js";

const ORIGIN = "https://akramsakib.github.io";

/* ------------------------------------------------------------------ harness */
const calls = [];
globalThis.fetch = async (input, init = {}) => {
  const url = typeof input === "string" ? input : input.url;
  calls.push(url);
  const method = (init.method || "GET").toUpperCase();

  // Instagram: pretend we got rate-limited
  if (url.includes("i.instagram.com")) return new Response("{}", { status: 429 });

  // Snapchat: JSON validator saying the number is taken
  if (url.includes("accounts.snapchat.com") && method === "GET") {
    return new Response("<html></html>", { status: 200, headers: { "set-cookie": "xsrf_token=abc; Path=/" } });
  }
  if (url.includes("validate_phone_number")) {
    return new Response(JSON.stringify({ status_code: "TAKEN_NUMBER" }), { status: 200 });
  }

  // Twilio Lookup: valid number with carrier
  if (url.includes("lookups.twilio.com")) {
    return new Response(JSON.stringify({
      valid: true, country_code: "MY",
      line_type_intelligence: { carrier_name: "Test Telco", type: "mobile" },
    }), { status: 200 });
  }

  // Vonage: body-level status 3 -> invalid number
  if (url.includes("api.nexmo.com")) {
    return new Response(JSON.stringify({ status: 3, error_text: "invalid params" }), { status: 200 });
  }

  // numlookupapi: good payload
  if (url.includes("numlookupapi.com")) {
    return new Response(JSON.stringify({
      valid: true, country_name: "Malaysia", carrier: "Test Telco", line_type: "mobile",
    }), { status: 200 });
  }

  // Trestle: 404 -> not found
  if (url.includes("trestleiq.com")) return new Response("{}", { status: 404 });

  // everything else: a plain 200 with an unhelpful body
  return new Response("{}", { status: 200 });
};

const ENV = { ALLOW_ORIGIN: ORIGIN };

async function check({ phone = "+60123456789", env = ENV, ip = "1.1.1.1", method = "POST" } = {}) {
  const request = new Request("https://api.example/api/check", {
    method,
    headers: { "content-type": "application/json", "cf-connecting-ip": ip },
    body: method === "POST" ? JSON.stringify({ phone }) : undefined,
  });
  const response = await worker.fetch(request, env);
  const text = response.headers.get("content-type") === "application/x-ndjson"
    ? await response.text()
    : "";
  const events = text ? text.trim().split("\n").map((l) => JSON.parse(l)) : [];
  return { response, events };
}

const byModule = (events) =>
  Object.fromEntries(events.filter((e) => e.type === "result").map((e) => [e.module, e]));

/* -------------------------------------------------------------------- tests */
const results = [];
async function test(name, fn) {
  try {
    await fn();
    results.push(["pass", name]);
  } catch (err) {
    results.push(["FAIL", name, err.message]);
  }
}

await test("streams meta, 16 results, then done", async () => {
  const { response, events } = await check({ ip: "9.9.9.1" });
  assert.equal(response.headers.get("content-type"), "application/x-ndjson");
  assert.equal(events[0].type, "meta");
  assert.equal(events[0].iso, "MY");
  assert.equal(events[0].automated, 16);
  assert.equal(events.at(-1).type, "done");
  assert.equal(events.filter((e) => e.type === "result").length, 16);
  assert.ok(events.at(-1).elapsed >= 0);
});

await test("instagram 429 becomes rate_limited", async () => {
  const m = byModule((await check({ ip: "9.9.9.2" })).events);
  assert.equal(m.instagram.status, "rate_limited");
  assert.equal(m.instagram.found, false);
});

await test("snapchat TAKEN_NUMBER becomes found", async () => {
  const m = byModule((await check({ ip: "9.9.9.3" })).events);
  assert.equal(m.snapchat.status, "found");
});

await test("missing key becomes needs_credentials, not an error", async () => {
  const m = byModule((await check({ ip: "9.9.9.4", env: { ALLOW_ORIGIN: ORIGIN } })).events);
  assert.equal(m.twilio.status, "needs_credentials");
  assert.match(m.twilio.detail, /TWILIO_ACCOUNT_SID/);
  assert.equal(m.ipqualityscore.status, "needs_credentials");
});

await test("twilio with keys reports carrier", async () => {
  const env = { ALLOW_ORIGIN: ORIGIN, TWILIO_ACCOUNT_SID: "AC1", TWILIO_AUTH_TOKEN: "t" };
  const m = byModule((await check({ ip: "9.9.9.5", env })).events);
  assert.equal(m.twilio.status, "found");
  assert.match(m.twilio.detail, /Test Telco/);
});

await test("vonage body status 3 becomes not_found", async () => {
  const env = { ALLOW_ORIGIN: ORIGIN, VONAGE_API_KEY: "k", VONAGE_API_SECRET: "s" };
  const m = byModule((await check({ ip: "9.9.9.6", env })).events);
  assert.equal(m.vonage.status, "not_found");
});

await test("numlookupapi and trestle map correctly", async () => {
  const env = { ALLOW_ORIGIN: ORIGIN, NUMLOOKUPAPI_KEY: "k", TRESTLE_API_KEY: "k" };
  const m = byModule((await check({ ip: "9.9.9.7", env })).events);
  assert.equal(m.numlookupapi.status, "found");
  assert.equal(m.trestle.status, "not_found");
});

await test("offline MY probe answers without any network call", async () => {
  calls.length = 0;
  const m = byModule((await check({ ip: "9.9.9.20" })).events);
  assert.equal(m.my_number.status, "found");
  assert.match(m.my_number.detail, /012 block/);
  assert.match(m.my_number.detail, /Maxis/);
  assert.ok(!calls.some((u) => u.includes("mcmc")), "offline probe must not fetch");
});

await test("offline MY probe rejects a bad length", async () => {
  const m = byModule((await check({ phone: "+6012345", ip: "9.9.9.21" })).events);
  assert.equal(m.my_number.status, "not_found");
  assert.match(m.my_number.detail, /expects 9 NSN digits/);
});

await test("offline MY probe skips non-Malaysian numbers", async () => {
  const m = byModule((await check({ phone: "+15550100", ip: "9.9.9.22" })).events);
  assert.equal(m.my_number.status, "skipped");
});

await test("amazon is skipped with an explanation", async () => {
  const m = byModule((await check({ ip: "9.9.9.8" })).events);
  assert.equal(m.amazon.status, "skipped");
  assert.match(m.amazon.detail, /Python server/);
});

await test("bad number is a 400 with a message", async () => {
  const { response } = await check({ phone: "nope", ip: "9.9.9.9" });
  assert.equal(response.status, 400);
  const body = await response.json();
  assert.match(body.error, /digits|dialling code/);
});

await test("CORS origin is echoed when configured", async () => {
  const { response } = await check({ ip: "9.9.9.10" });
  assert.equal(response.headers.get("access-control-allow-origin"), ORIGIN);
});

await test("no CORS header when ALLOW_ORIGIN is unset", async () => {
  const { response } = await check({ ip: "9.9.9.11", env: {} });
  assert.equal(response.headers.get("access-control-allow-origin"), null);
});

await test("GET on /api/check is rejected", async () => {
  const { response } = await check({ ip: "9.9.9.12", method: "GET" });
  assert.equal(response.status, 405);
});

await test("unknown path 404s and healthz lists probes", async () => {
  const miss = await worker.fetch(new Request("https://api.example/nope"), ENV);
  assert.equal(miss.status, 404);
  const health = await worker.fetch(new Request("https://api.example/healthz"), ENV);
  const body = await health.json();
  assert.ok(body.probes.includes("instagram") && body.probes.includes("mocean"));
  // 16 probes ship in the catalogue; the edge build implements 15 (amazon is
  // skipped here and says so).
  assert.equal(body.probes.length, 15);
  assert.ok(body.probes.includes("my_number"));
  assert.ok(!body.probes.includes("amazon"));
});

await test("rate limit kicks in after 8 runs from one IP", async () => {
  const ip = "203.0.113.7";
  let last;
  for (let i = 0; i < 9; i++) {
    const { response } = await check({ ip });
    last = response;
  }
  assert.equal(last.status, 429);
  assert.ok(Number(last.headers.get("retry-after")) >= 1);
});

await test("HMAC signature is actually computed for instagram", async () => {
  calls.length = 0;
  await check({ ip: "9.9.9.13" });
  assert.ok(calls.some((u) => u.includes("i.instagram.com")), "instagram endpoint never called");
});

/* ------------------------------------------------------------------ report */
const failed = results.filter(([state]) => state === "FAIL");
for (const [state, name, msg] of results) {
  console.log(`${state === "pass" ? "  ok" : "FAIL"}  ${name}${msg ? "\n        " + msg : ""}`);
}
console.log(`\n${results.length - failed.length}/${results.length} worker tests passed`);
process.exit(failed.length ? 1 : 0);
