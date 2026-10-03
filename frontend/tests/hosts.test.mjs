// Run by frontend-tests: node --experimental-strip-types --test tests/hosts.test.mjs
// Same cases as test_public_host_names_are_refused (backend/tests/test_send_to_steno.py).
import test from "node:test";
import assert from "node:assert/strict";
import { hostAllowed } from "../lib/hosts.ts";

const CASES = [
  ["127.0.0.1:3000", true], ["localhost:3000", true], ["192.168.1.20:8443", true], ["[::1]:8000", true], ["api:8000", true],
  ["web", true], ["pc-salon.local", true], ["nas.home.arpa:8443", true], [null, true],
  ["attacker.example", false], ["evil.com:3000", false], ["127.0.0.1.nip.io", false], ["steno.example.org.", false], [":80", false],
];

test("public host names are refused, local ones accepted", () => {
  for (const [host, allowed] of CASES) assert.equal(hostAllowed(host), allowed, String(host));
});
