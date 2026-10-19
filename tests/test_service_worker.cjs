const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function worker() {
  const handlers = {};
  const entries = new Map();
  const stores = new Map([["stowe-v3", new Map()], ["unrelated", new Map()]]);
  const cache = { put: async (request, response) => entries.set(request.url, response), match: async request => entries.get(request.url) };
  const context = {
    URL,
    self: { location: { origin: "http://127.0.0.1:9000" }, skipWaiting() {}, clients: { claim: async () => {} }, addEventListener: (name, handler) => { handlers[name] = handler; } },
    caches: { keys: async () => [...stores.keys()], delete: async name => stores.delete(name), open: async () => cache },
    fetch: async () => new Response("static asset"),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../frontend/sw.js"), "utf8"), context);
  return { handlers, entries, stores, context };
}

test("financial reads, receipt files and exports never use a cache fallback", () => {
  const { handlers } = worker();
  for (const pathname of ["/api/v1/expenses", "/api/v1/receipts/1/file", "/api/v1/export/zip", "/api/v1/summary"]) {
    let intercepted = false;
    handlers.fetch({ request: new Request(`http://127.0.0.1:9000${pathname}`), respondWith: () => { intercepted = true; } });
    assert.equal(intercepted, false, pathname);
  }
});

test("activation clears only old Stowe caches that could contain medical records", async () => {
  const { handlers, stores } = worker();
  let completion;
  handlers.activate({ waitUntil: promise => { completion = promise; } });
  await completion;
  assert.equal(stores.has("stowe-v3"), false);
  assert.equal(stores.has("unrelated"), true);
});

test("static assets keep their bounded offline fallback", async () => {
  const { handlers, entries, context } = worker();
  const request = new Request("http://127.0.0.1:9000/static/js/app.js");
  let completion;
  handlers.fetch({ request, respondWith: promise => { completion = promise; } });
  assert.equal(await (await completion).text(), "static asset");
  assert.equal(entries.size, 1);
  context.fetch = async () => { throw new Error("offline"); };
  handlers.fetch({ request, respondWith: promise => { completion = promise; } });
  assert.equal(await (await completion).text(), "static asset");
});
