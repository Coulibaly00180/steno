// Run in Docker by the frontend-tests service: node --test /extension/tests/lib.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const lib = require("../lib.js");

const ROOT = path.join(__dirname, "..");
const read = name => fs.readFileSync(path.join(ROOT, name), "utf8");

test("the address is reduced to its origin, http(s) only", () => {
  assert.equal(lib.normalizeOrigin("http://127.0.0.1:3000/videos?x=1"), "http://127.0.0.1:3000");
  assert.equal(lib.normalizeOrigin(" https://192.168.1.20:8443 "), "https://192.168.1.20:8443");
  assert.equal(lib.normalizeOrigin("127.0.0.1:3000"), "http://127.0.0.1:3000");
  for (const bad of ["javascript:alert(1)", "file:///etc/passwd", "ftp://steno", "http://user:pw@steno.lan", ""]) {
    assert.throws(() => lib.normalizeOrigin(bad), undefined, bad);
  }
  assert.equal(lib.permissionPattern("https://192.168.1.20:8443"), "https://192.168.1.20/*");
});

test("requests only reach the configured Sténo, under /api/", () => {
  const origin = "http://127.0.0.1:3000";
  assert.equal(lib.apiUrl(origin, "/api/imports/url"), "http://127.0.0.1:3000/api/imports/url");
  assert.equal(lib.apiUrl(origin, "/api/jobs/abc"), "http://127.0.0.1:3000/api/jobs/abc");
  for (const bad of ["https://evil.example/api/x", "//evil.example/api/x", "/api//evil.example", "/api/../x", "/videos",
    "api/x", "/api/x@evil.example", "/api\\..\\x", "/api/ x", null]) {
    assert.throws(() => lib.apiUrl(origin, bad), undefined, String(bad));
  }
});

test("stenoFetch sends the token as a header, no cookie, no redirect", async () => {
  const calls = [];
  const fake = async (url, init) => { calls.push({ url, init }); return { ok: true, status: 200, text: async () => '{"id":"j"}' }; };
  const result = await lib.stenoFetch({ origin: "https://192.168.1.20:8443/x", token: "steno_abc" }, "/api/imports/url",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }, fake);
  assert.deepEqual(result, { id: "j" });
  assert.equal(calls[0].url, "https://192.168.1.20:8443/api/imports/url");
  assert.equal(calls[0].init.headers.Authorization, "Bearer steno_abc");
  assert.equal(calls[0].init.credentials, "omit");
  assert.equal(calls[0].init.redirect, "error");
  // Without a token, no Authorization header at all.
  await lib.stenoFetch({ origin: "http://127.0.0.1:3000", token: "" }, "/api/templates", { method: "GET" }, fake);
  assert.equal(calls[1].init.headers.Authorization, undefined);
});

test("a token never travels in clear over the network", async () => {
  assert.ok(lib.tokenTransportOk("http://127.0.0.1:3000") && lib.tokenTransportOk("http://localhost:3000"));
  assert.ok(lib.tokenTransportOk("https://192.168.1.20:8443"));
  assert.ok(!lib.tokenTransportOk("http://192.168.1.20:3000"));
  let called = false;
  const fake = async () => { called = true; return { ok: true, status: 200, text: async () => "{}" }; };
  await assert.rejects(lib.stenoFetch({ origin: "http://192.168.1.20:3000", token: "steno_x" }, "/api/templates", {}, fake), /HTTPS/);
  assert.equal(called, false);
  // Without a token, nothing secret leaves: allowed.
  await lib.stenoFetch({ origin: "http://192.168.1.20:3000", token: "" }, "/api/templates", {}, fake);
  assert.equal(called, true);
});

test("errors are explained", async () => {
  const answer = (status, body) => async () => ({ ok: false, status, text: async () => body });
  const config = { origin: "http://127.0.0.1:3000", token: "steno_x" };
  await assert.rejects(lib.stenoFetch(config, "/api/templates", {}, answer(401, '{"detail":"Jeton d\'accès invalide ou révoqué"}')), /révoqué/);
  await assert.rejects(lib.stenoFetch(config, "/api/templates", {}, answer(401, '{"detail":"Connexion requise"}')), /jeton d'accès/);
  await assert.rejects(lib.stenoFetch(config, "/api/templates", {}, answer(422, '{"detail":"Ce lien mène à une page web"}')), /page web/);
  await assert.rejects(lib.stenoFetch(config, "/api/templates", {}, async () => { throw new TypeError("Failed to fetch"); }), /ne répond pas/);
});

test("only web pages are sent, never Sténo itself", () => {
  assert.ok(lib.isSendable("https://www.youtube.com/watch?v=abc", "http://127.0.0.1:3000"));
  for (const bad of ["chrome://extensions", "about:blank", "file:///C:/x.mp4", "http://127.0.0.1:3000/videos/1", "https://u:p@site.org/a", "pas une adresse"]) {
    assert.ok(!lib.isSendable(bad, "http://127.0.0.1:3000"), bad);
  }
});

test("titles lose the site name and the notification count; options are optional", () => {
  assert.equal(lib.cleanTitle("(3) Séminaire, jour 3 - YouTube"), "Séminaire, jour 3");
  assert.equal(lib.cleanTitle("  Une   conférence | Vimeo "), "Une conférence");
  assert.equal(lib.cleanTitle("x".repeat(300)).length, 200);
  assert.deepEqual(lib.importBody("https://a.org/e.mp3", "", null), { url: "https://a.org/e.mp3" });
  assert.deepEqual(lib.importBody("https://a.org/e.mp3", "Épisode - YouTube", { templateId: "t1", diarize: true, numSpeakers: "3" }),
    { url: "https://a.org/e.mp3", title: "Épisode", template_id: "t1", diarize: true, num_speakers: 3 });
  assert.deepEqual(lib.importBody("https://a.org/e.mp3", "", { diarize: false, numSpeakers: "3" }), { url: "https://a.org/e.mp3" });
  assert.equal(lib.importBody("https://a.org/e.mp3", "", { diarize: true, numSpeakers: "99" }).num_speakers, undefined);
});

test("the manifest asks for no site in advance and runs nothing in web pages", () => {
  const manifest = JSON.parse(read("manifest.json"));
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.host_permissions, undefined);
  assert.equal(manifest.content_scripts, undefined);
  assert.equal(manifest.externally_connectable, undefined);
  assert.equal(manifest.web_accessible_resources, undefined);
  assert.deepEqual([...manifest.permissions].sort(), ["activeTab", "alarms", "contextMenus", "notifications", "storage"]);
  assert.ok(!manifest.permissions.includes("tabs") && !manifest.permissions.includes("cookies"));
  assert.match(manifest.content_security_policy.extension_pages, /script-src 'self'/);
  for (const file of [manifest.background.service_worker, ...manifest.background.scripts, "options.html", "send.html"]) {
    assert.ok(fs.existsSync(path.join(ROOT, file)), file);
  }
});

test("every request goes through stenoFetch, the token never through storage.sync", () => {
  for (const file of ["background.js", "options.js", "send.js"]) {
    const source = read(file);
    assert.ok(!/\bfetch\(|XMLHttpRequest|sendBeacon|WebSocket|EventSource/.test(source), `${file} fetches directly`);
    assert.ok(!/storage\.sync/.test(source), `${file} uses storage.sync`);
    assert.ok(!/innerHTML|eval\(|new Function/.test(source), `${file} builds code or HTML`);
  }
});
