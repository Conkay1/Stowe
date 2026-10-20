const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const { chromium, request } = require("playwright");

let browser, server, api, origin, artifacts;

before(async () => {
  artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "stowe-ui-synthetic-"));
  const listener = net.createServer();
  listener.listen(0, "127.0.0.1");
  await once(listener, "listening");
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  origin = `http://127.0.0.1:${port}`;
  const log = fs.openSync(path.join(artifacts, "server.log"), "w");
  server = spawn(process.env.STOWE_TEST_PYTHON || "python3", [path.join(__dirname, "ui_server.py"), artifacts, String(port)], { stdio: ["ignore", log, log] });
  fs.closeSync(log);
  api = await request.newContext({ baseURL: origin + "/api/v1" });
  const deadline = Date.now() + 10000;
  let ready = false;
  while (Date.now() < deadline) {
    assert.equal(server.exitCode, null, fs.readFileSync(path.join(artifacts, "server.log"), "utf8"));
    try {
      const response = await api.get(origin + "/api/v1/system/data-dir", { timeout: 500 });
      if (response.ok()) {
        assert.equal((await response.json()).data_dir, fs.realpathSync(artifacts));
        ready = true;
        break;
      }
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 40));
  }
  assert.ok(ready, "Isolated synthetic server did not start");
  browser = await chromium.launch({ headless: true, executablePath: process.env.STOWE_TEST_BROWSER });
});

after(async () => {
  await browser?.close();
  await api?.dispose();
  if (server && server.exitCode === null && server.signalCode === null) {
    const exited = once(server, "exit");
    server.kill("SIGTERM");
    const timeout = setTimeout(() => server.kill("SIGKILL"), 5000);
    await exited;
    clearTimeout(timeout);
  }
  console.log(`Synthetic UI evidence: ${artifacts}`);
});

async function call(method, endpoint, body) {
  const response = await api[method](origin + "/api/v1" + endpoint, body ? { data: body } : {});
  assert.ok(response.ok(), await response.text());
  return response.status() === 204 ? null : response.json();
}

async function seed(covered = 100) {
  const expense = await call("post", "/expenses", { merchant: "Synthetic Clinic", date: "2025-01-10", amount: 100 });
  const pull = await call("post", "/reimbursements", { date: "2025-02-10", total_amount: covered, line_items: [{ expense_id: expense.id, covered_amount: covered }] });
  return { expense, pull };
}

async function openExpense(id, width = 1100) {
  const context = await browser.newContext({ viewport: { width, height: 1000 }, serviceWorkers: "block" });
  const page = await context.newPage();
  await page.goto(origin + `/#/vault/${id}`);
  await page.locator("#correct-cost-btn").waitFor();
  return { page, context };
}

async function preview(page, amount = "70", reason = "Synthetic later insurer adjustment") {
  await page.locator("#correct-cost-btn").click();
  await page.getByLabel("Corrected cost ($)", { exact: true }).fill(amount);
  await page.getByLabel("Reason for correction", { exact: true }).fill(reason);
  await page.getByRole("button", { name: "Preview correction", exact: true }).click();
  await page.locator("#correction-preview h3").waitFor();
}

test("receipt preview stays in app and preserves unsaved Manage edits through every close path", async () => {
  const { expense } = await seed();
  const filename = 'Synthetic <img src=x onerror=alert(1)> "receipt".png';
  const bytes = fs.readFileSync(path.join(__dirname, "../assets/ocr-warmup.png"));
  const upload = await api.post(origin + `/api/v1/expenses/${expense.id}/receipts`, {
    multipart: { file: { name: filename, mimeType: "image/png", buffer: bytes } },
  });
  assert.equal(upload.status(), 201);
  const rid = (await upload.json()).id;
  const before = await call("get", `/expenses/${expense.id}`);
  const { page, context } = await openExpense(expense.id);
  try {
    const notes = page.locator('#manage-form [name="notes"]');
    const merchant = page.locator('#manage-form [name="merchant"]');
    await notes.fill("Unsaved synthetic edit");
    await merchant.fill("Unsaved synthetic merchant");
    for (const close of ["button", "escape", "backdrop"]) {
      await page.locator(`[data-receipt-preview="${rid}"]`).click();
      const modal = page.locator("#receipt-preview");
      await modal.waitFor();
      const img = modal.locator("img");
      await img.evaluate(el => el.decode());
      assert.equal(await img.getAttribute("src"), `/api/v1/receipts/${rid}/file?preview=true`);
      assert.equal(await modal.locator("#receipt-preview-title").innerText(), filename);
      assert.equal(await modal.locator("img").count(), 1);
      assert.equal(await page.locator("#modal-overlay").evaluate(el => el.inert), true);
      assert.equal(context.pages().length, 1);
      assert.ok((await img.boundingBox()).width > 300);
      if (close === "button") await modal.getByRole("button", { name: "Close receipt preview" }).click();
      if (close === "escape") await page.keyboard.press("Escape");
      if (close === "backdrop") await modal.click({ position: { x: 2, y: 2 } });
      await modal.waitFor({ state: "detached" });
      assert.equal(await notes.inputValue(), "Unsaved synthetic edit");
      assert.equal(await merchant.inputValue(), "Unsaved synthetic merchant");
      assert.equal(await page.locator("#modal-overlay").evaluate(el => el.inert), false);
      assert.equal(await page.locator(`[data-receipt-preview="${rid}"]`).evaluate(el => el === document.activeElement), true);
    }
    assert.deepEqual(await call("get", `/expenses/${expense.id}`), before);
    assert.deepEqual(await (await api.get(origin + `/api/v1/receipts/${rid}/file`)).body(), bytes);
  } finally { await context.close(); }
});

test("PDF preview uses inline same-origin receipt ID and keeps the original download route", async () => {
  const { expense } = await seed();
  const bytes = Buffer.from("%PDF-1.4\nSynthetic PDF evidence\n%%EOF\n");
  const upload = await api.post(origin + `/api/v1/expenses/${expense.id}/receipts`, {
    multipart: { file: { name: "Synthetic.pdf", mimeType: "application/pdf", buffer: bytes } },
  });
  assert.equal(upload.status(), 201);
  const rid = (await upload.json()).id;
  const { page, context } = await openExpense(expense.id);
  try {
    await page.locator(`[data-receipt-preview="${rid}"]`).click();
    const frame = page.locator("#receipt-preview iframe");
    await frame.waitFor();
    assert.equal(await frame.getAttribute("src"), `/api/v1/receipts/${rid}/file?preview=true`);
    assert.equal(await page.locator("#receipt-preview a").getAttribute("href"), `/api/v1/receipts/${rid}/file`);
    const response = await api.get(origin + `/api/v1/receipts/${rid}/file?preview=true`);
    assert.match(response.headers()["content-disposition"], /^inline;/);
    assert.deepEqual(await response.body(), bytes);
    assert.equal(context.pages().length, 1);
    await page.getByRole("button", { name: "Close receipt preview" }).click();
    assert.equal(await page.locator("#manage-form").isVisible(), true);
  } finally { await context.close(); }
});

test("correction UI preserves evidence, exposes excess and allows metadata on a zero cost", async () => {
  const { expense, pull } = await seed();
  const { page, context } = await openExpense(expense.id);
  try {
    const receipt = await api.post(origin + `/api/v1/expenses/${expense.id}/receipts`, { multipart: { file: { name: "synthetic.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-synthetic evidence") } } });
    assert.equal(receipt.status(), 201);
    const rid = (await receipt.json()).id;
    const before = await (await api.get(origin + `/api/v1/receipts/${rid}/file`)).body();
    await preview(page, "0", "Synthetic full adjustment <script>unsafe()</script>");
    assert.match(await page.locator("#correction-preview").innerText(), /\$100\.00 needs reconciliation/);
    await page.screenshot({ path: path.join(artifacts, "correction-preview.png"), fullPage: true });
    await page.getByRole("button", { name: "Save correction", exact: true }).click();
    await page.locator(".correction-history").waitFor();
    assert.equal(await page.locator('#manage-form [name="amount"]').inputValue(), "0");
    assert.equal(await page.locator("#delete-btn").isDisabled(), true);
    await page.locator(".correction-history summary").click();
    assert.match(await page.locator(".correction-history").innerText(), /<script>unsafe\(\)<\/script>/);
    assert.equal(await page.locator(".correction-history script").count(), 0);
    await page.locator('#manage-form [name="notes"]').fill("Metadata survives a zero cost correction");
    await page.getByRole("button", { name: "Save Changes", exact: true }).click();
    await page.waitForFunction(() => document.getElementById("modal-overlay").classList.contains("hidden"));
    const after = await call("get", `/expenses/${expense.id}`);
    assert.equal(after.amount, 0);
    assert.equal(after.covered_amount, 100);
    assert.equal(after.remaining_amount, 0);
    assert.equal(after.notes, "Metadata survives a zero cost correction");
    assert.deepEqual(await (await api.get(origin + `/api/v1/receipts/${rid}/file`)).body(), before);
    await page.goto(origin + `/#/pulls/${pull.id}`);
    await page.locator("#undo-pull-btn").waitFor();
    assert.equal(await page.locator("#undo-pull-btn").isDisabled(), true);
    assert.match(await page.locator("#app").innerText(), /\$100\.00 above corrected cost across all pulls/);
  } finally { await context.close(); }
});

test("editing after preview requires another preview and stale evidence cannot save", async () => {
  const { expense } = await seed();
  const { page, context } = await openExpense(expense.id);
  try {
    await preview(page);
    await page.getByLabel("Reason for correction", { exact: true }).fill("Changed synthetic reason");
    assert.equal(await page.locator("#correction-save-btn").isDisabled(), true);
    assert.equal(await page.locator("#correction-preview").innerText(), "");
    await page.locator("#correction-preview-btn").click();
    await page.locator("#correction-preview h3").waitFor();
    await call("put", `/expenses/${expense.id}`, { notes: "Evidence changed after preview" });
    await page.locator("#correction-save-btn").click();
    await page.waitForFunction(() => document.getElementById("correction-error").textContent.includes("changed"));
    assert.equal(await page.locator("#correction-save-btn").isDisabled(), true);
    assert.equal((await call("get", `/expenses/${expense.id}`)).amount, 100);
    assert.deepEqual(await call("get", `/expenses/${expense.id}/corrections`), []);
  } finally { await context.close(); }
});

test("lost response retries the same correction once even after closing its modal", async () => {
  const { expense } = await seed();
  const { page, context } = await openExpense(expense.id);
  const attempts = [];
  try {
    await page.route(`**/api/v1/expenses/${expense.id}/corrections`, async route => {
      if (route.request().method() !== "POST") return route.continue();
      attempts.push(route.request().postDataJSON());
      const response = await route.fetch();
      if (attempts.length === 1) return route.abort("failed");
      return route.fulfill({ response });
    });
    await preview(page);
    await page.locator("#correction-save-btn").click();
    await page.getByRole("button", { name: "Retry same correction", exact: true }).waitFor();
    assert.equal(await page.locator("#correction-amount").isDisabled(), true);
    await page.locator("#modal-close").click();
    await page.goto(origin + "/#/vault");
    await page.goto(origin + `/#/vault/${expense.id}`);
    await page.getByRole("button", { name: "Verify pending correction", exact: true }).click();
    await page.getByRole("button", { name: "Retry same correction", exact: true }).click();
    await page.locator(".correction-history").waitFor();
    assert.equal(attempts.length, 2);
    assert.deepEqual(attempts[0], attempts[1]);
    assert.equal((await call("get", `/expenses/${expense.id}/corrections`)).length, 1);
  } finally { await context.close(); }
});

test("account reconciliation shows a signed one-cent difference and global expense excess", async () => {
  const { expense, pull } = await seed();
  const account = await call("post", "/accounts", { name: "Synthetic HSA", custodian: "Test" });
  await call("put", `/reimbursements/${pull.id}`, { account_id: account.id });
  await call("post", `/accounts/${account.id}/imports/commit`, { column_map: { date: "date", amount: "amount" }, rows: [{ date: "2025-02-10", amount: 99.99 }] });
  const distributions = await call("get", `/accounts/${account.id}/distributions`);
  await call("post", `/distributions/${distributions[0].id}/match`, { reimbursement_id: pull.id });
  const { page, context } = await openExpense(expense.id, 390);
  try {
    await preview(page);
    await page.screenshot({ path: path.join(artifacts, "correction-mobile.png"), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await page.locator("#correction-save-btn").click();
    await page.locator(".correction-history").waitFor();
    await page.locator("#modal-close").click();
    await page.goto(origin + `/#/accounts/${account.id}`);
    await page.locator("#reconciliation-differences").waitFor();
    const text = await page.locator("#reconciliation-differences").innerText();
    assert.match(text, /Withdrawal difference: −\$0\.01/);
    assert.match(text, /\$30\.00 coverage above its corrected cost across all pulls/);
    assert.equal(await page.locator("#matched-toggle").count(), 0);
    await page.screenshot({ path: path.join(artifacts, "reconciliation-mobile.png"), fullPage: true });
    await page.locator(`a[href="#/vault/${expense.id}"]`).click();
    await page.locator(".correction-history").waitFor();
  } finally { await context.close(); }
});
