import { test } from "node:test";
import assert from "node:assert/strict";
import { apiOrigin, contentSecurityPolicy, securityHeaders } from "./csp.ts";

/** The policy as directive name → sources. */
function parse(policy: string): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const part of policy.split(";")) {
    const [name, ...sources] = part.trim().split(/\s+/);
    assert.ok(name, `an empty directive in ${policy}`);
    assert.ok(!out.has(name), `${name} twice`);
    out.set(name, sources);
  }
  return out;
}

test("next dev gets no headers", () => {
  assert.deepEqual(securityHeaders({ dev: true, apiUrl: "http://127.0.0.1:8000" }), []);
});

test("a local production build allows the http API and doesn't upgrade requests", () => {
  assert.equal(
    contentSecurityPolicy("http://127.0.0.1:8001"),
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; " +
      "font-src 'self'; connect-src 'self' http://127.0.0.1:8001; object-src 'none'; base-uri 'self'; " +
      "form-action 'self'; frame-ancestors 'none'; manifest-src 'self'",
  );
});

test("the production build names the API origin, without its path, and upgrades requests", () => {
  const policy = contentSecurityPolicy("https://someone--race-engineer-api.modal.run/");
  assert.equal(
    policy,
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; " +
      "font-src 'self'; connect-src 'self' https://someone--race-engineer-api.modal.run; object-src 'none'; " +
      "base-uri 'self'; form-action 'self'; frame-ancestors 'none'; manifest-src 'self'; upgrade-insecure-requests",
  );
  const directives = parse(policy);
  assert.deepEqual(directives.get("upgrade-insecure-requests"), []);
  assert.deepEqual(parse(contentSecurityPolicy("https://api.example.com:8443/v1/")).get("connect-src"), [
    "'self'",
    "https://api.example.com:8443",
  ]);
});

test("nothing outside the site and the API is allowed to run, load or frame", () => {
  const directives = parse(contentSecurityPolicy("https://api.example.com"));
  for (const [name, sources] of directives) {
    for (const source of sources) {
      assert.ok(!source.includes("*"), `${name} has a wildcard`);
      assert.notEqual(source, "'unsafe-eval'", `${name} allows eval`);
      assert.notEqual(source, "blob:", `${name} allows blob:`);
      assert.ok(source !== "https:" && source !== "http:", `${name} allows a whole scheme`);
    }
  }
  assert.deepEqual(directives.get("object-src"), ["'none'"]);
  assert.deepEqual(directives.get("frame-ancestors"), ["'none'"]);
  assert.deepEqual(directives.get("default-src"), ["'self'"]);
});

test("an API URL that isn't http(s) adds no source", () => {
  assert.equal(apiOrigin("not a url"), null);
  assert.equal(apiOrigin("ftp://files.example.com"), null);
  assert.equal(apiOrigin("javascript:alert(1)"), null);
  assert.deepEqual(parse(contentSecurityPolicy("not a url")).get("connect-src"), ["'self'"]);
  assert.ok(!contentSecurityPolicy("not a url").includes("upgrade-insecure-requests"));
});

test("a production build sends the policy and its companion headers, each once", () => {
  const headers = securityHeaders({ dev: false, apiUrl: "https://api.example.com" });
  assert.deepEqual(
    headers.map((h) => h.key),
    ["Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy", "Permissions-Policy", "X-Frame-Options"],
  );
  const value = (key: string) => headers.find((h) => h.key === key)?.value;
  assert.equal(value("Content-Security-Policy"), contentSecurityPolicy("https://api.example.com"));
  assert.equal(value("X-Content-Type-Options"), "nosniff");
  assert.equal(value("Referrer-Policy"), "strict-origin-when-cross-origin");
  assert.equal(value("Permissions-Policy"), "camera=(), microphone=(), geolocation=(), browsing-topics=()");
  assert.equal(value("X-Frame-Options"), "DENY");
  assert.ok(!headers.some((h) => h.key.toLowerCase() === "strict-transport-security"));
});
