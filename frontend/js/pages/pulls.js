import { api } from "../api.js";
import { toast, openModal, closeModal } from "../app.js";

const fmt = n => "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtDate = d => new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const round2 = n => Math.round(n * 100) / 100;

const PULL_FILTER_KEY  = "stowe.pulls.filter";
const PULL_ACCT_KEY    = "stowe.pulls.acctFilter";
const LAST_USED_ACCOUNT = "stowe.pulls.lastAccountId";
const BANNER_DISMISS_PREFIX = "stowe.pulls.legacyBannerDismissed.";

export async function render(container, params = []) {
  if (params.length > 0) {
    await renderDetail(container, parseInt(params[0]));
  } else {
    await renderList(container);
  }
}

// ── List view ─────────────────────────────────────────────────────────────────

async function renderList(container) {
  container.innerHTML = `
    <div class="section-header">
      <h2>Pulls</h2>
      <div class="spacer"></div>
      <button id="new-pull-btn" class="btn btn-primary btn-sm">+ New Pull</button>
    </div>
    <p class="text-muted" style="margin-bottom:14px;font-size:13px">
      A Pull records an HSA distribution. Enter the amount your HSA paid out, then back it with receipts (in full or partial slices).
    </p>
    <div id="legacy-banner-wrap"></div>
    <div id="pull-filter-wrap"></div>
    <div id="pulls-list"></div>
  `;

  document.getElementById("new-pull-btn").addEventListener("click", () => showNewPullModal());

  await loadList();
}

async function loadList() {
  const el = document.getElementById("pulls-list");
  el.innerHTML = `<div class="empty-state">Loading…</div>`;

  let pulls, accounts;
  try {
    [pulls, accounts] = await Promise.all([
      api.reimbursements.list(),
      api.accounts.list().catch(() => []),
    ]);
  } catch (err) {
    el.innerHTML = `<div class="empty-state">Failed to load pulls: ${esc(err.message)}</div>`;
    return;
  }

  // Build matched-pull set up front so the first render is correct under any filter.
  await refreshMatchedPullIds(accounts);

  injectStyles();
  renderLegacyBanner(pulls, accounts);
  renderFilterRow(pulls, accounts);
  renderPullsList(pulls, accounts);
}

function renderLegacyBanner(pulls, accounts) {
  const wrap = document.getElementById("legacy-banner-wrap");
  const activeAccounts = accounts.filter(a => a.is_active);
  if (activeAccounts.length !== 1) { wrap.innerHTML = ""; return; }
  const account = activeAccounts[0];
  const dismissKey = BANNER_DISMISS_PREFIX + account.id;
  if (localStorage.getItem(dismissKey)) { wrap.innerHTML = ""; return; }
  const untagged = pulls.filter(p => !p.account_id);
  if (untagged.length === 0) { wrap.innerHTML = ""; return; }

  wrap.innerHTML = `
    <div class="legacy-banner">
      <div class="legacy-banner-text">
        Tag all <strong>${untagged.length}</strong> untagged pull${untagged.length !== 1 ? "s" : ""} to <strong>${esc(account.name)}</strong>?
      </div>
      <div class="legacy-banner-actions">
        <button id="legacy-tag-btn" class="btn btn-primary btn-sm">Tag all</button>
        <button id="legacy-dismiss-btn" class="btn btn-secondary btn-sm">Dismiss</button>
      </div>
    </div>
  `;

  document.getElementById("legacy-tag-btn").addEventListener("click", async () => {
    const btn = document.getElementById("legacy-tag-btn");
    btn.disabled = true;
    btn.textContent = "Tagging…";
    let success = 0, failed = 0;
    for (const p of untagged) {
      try {
        await api.reimbursements.update(p.id, { account_id: account.id });
        success++;
      } catch {
        failed++;
      }
    }
    if (failed === 0) {
      toast(`Tagged ${success} pull${success !== 1 ? "s" : ""} to ${account.name}`);
    } else {
      toast(`Tagged ${success}, ${failed} failed`, "error");
    }
    localStorage.setItem(dismissKey, "1");
    await loadList();
  });

  document.getElementById("legacy-dismiss-btn").addEventListener("click", () => {
    localStorage.setItem(dismissKey, "1");
    wrap.innerHTML = "";
  });
}

function renderFilterRow(pulls, accounts) {
  const wrap = document.getElementById("pull-filter-wrap");
  // Hide filter row entirely if no accounts exist — keeps the legacy UX byte-identical.
  if (accounts.length === 0) {
    wrap.innerHTML = "";
    return;
  }

  const status = localStorage.getItem(PULL_FILTER_KEY) || "all";
  const acctFilter = localStorage.getItem(PULL_ACCT_KEY) || "all";

  wrap.innerHTML = `
    <div class="pull-filter-row">
      <div class="filter-tabs" style="margin-bottom:0">
        <button class="filter-tab ${status === "all" ? "active" : ""}" data-status="all">All</button>
        <button class="filter-tab ${status === "matched" ? "active" : ""}" data-status="matched">Matched</button>
        <button class="filter-tab ${status === "unmatched" ? "active" : ""}" data-status="unmatched">Unmatched</button>
        <button class="filter-tab ${status === "no-account" ? "active" : ""}" data-status="no-account">No account</button>
      </div>
      <select id="pull-account-filter" class="pull-account-filter">
        <option value="all" ${acctFilter === "all" ? "selected" : ""}>All accounts</option>
        ${accounts.map(a => `<option value="${a.id}" ${acctFilter == a.id ? "selected" : ""}>${esc(a.name)}</option>`).join("")}
      </select>
    </div>
  `;

  wrap.querySelectorAll("[data-status]").forEach(btn => {
    btn.addEventListener("click", () => {
      localStorage.setItem(PULL_FILTER_KEY, btn.dataset.status);
      renderFilterRow(pulls, accounts);
      renderPullsList(pulls, accounts);
    });
  });
  document.getElementById("pull-account-filter").addEventListener("change", e => {
    localStorage.setItem(PULL_ACCT_KEY, e.target.value);
    renderPullsList(pulls, accounts);
  });
}

function renderPullsList(pulls, accounts) {
  const el = document.getElementById("pulls-list");
  const status = localStorage.getItem(PULL_FILTER_KEY) || "all";
  const acctFilter = localStorage.getItem(PULL_ACCT_KEY) || "all";

  // Build a quick lookup of which pulls have a matched distribution. We use the simple heuristic
  // that the list endpoint returns no per-pull match flag, so we treat presence of `account_id`
  // alone as not-enough-info — for v1, "matched/unmatched" requires checking distributions per
  // account, which would be N+1. Instead, for the list we do a cheap bulk fetch of distributions
  // for active accounts and build a Set of matched pull ids. (This is async-loaded; if it fails,
  // we degrade to showing only the No-account / account badges.)
  const filtered = pulls.filter(p => {
    if (acctFilter !== "all" && String(p.account_id || "") !== String(acctFilter)) return false;
    if (status === "no-account") return !p.account_id;
    if (status === "matched") return !!p.account_id && matchedPullIds.has(p.id);
    if (status === "unmatched") return !!p.account_id && !matchedPullIds.has(p.id);
    return true;
  });

  if (pulls.length === 0) {
    el.innerHTML = `
      <div class="empty-state">
        No pulls yet.<br>
        Reimburse yourself by pulling expenses out of the vault.
      </div>
    `;
    return;
  }

  if (filtered.length === 0) {
    el.innerHTML = `<div class="empty-state">No pulls match this filter.</div>`;
    return;
  }

  el.innerHTML = filtered.map(p => {
    let badgeHtml = "";
    // Only show badges when the user has at least one account configured —
    // keeps the v0.4 list view byte-identical when no accounts exist.
    if (accounts.length > 0) {
      if (!p.account_id) {
        badgeHtml = `<span class="pull-badge gray">No account</span>`;
      } else if (matchedPullIds.has(p.id)) {
        badgeHtml = `<span class="pull-badge green">Matched · ${esc(p.account_name || "")}</span>`;
      } else {
        badgeHtml = `<span class="pull-badge amber">Unmatched · ${esc(p.account_name || "")}</span>`;
      }
    }
    return `
      <a class="pull-card" href="#/pulls/${p.id}">
        <div class="pull-card-left">
          <div class="pull-card-date">${fmtDate(p.date)}</div>
          <div class="pull-card-meta">
            ${p.expense_count} expense${p.expense_count !== 1 ? "s" : ""}${p.reference ? ` · ${esc(p.reference)}` : ""}
          </div>
          ${badgeHtml ? `<div class="pull-card-tags">${badgeHtml}</div>` : ""}
        </div>
        <div class="pull-card-amount">${fmt(p.total_amount)}</div>
      </a>
    `;
  }).join("");
}

let matchedPullIds = new Set();
let matchedFetchInFlight = null;

async function refreshMatchedPullIds(accounts) {
  if (matchedFetchInFlight) return matchedFetchInFlight;
  if (accounts.length === 0) { matchedPullIds = new Set(); return; }
  matchedFetchInFlight = (async () => {
    const next = new Set();
    for (const a of accounts) {
      try {
        const dists = await api.accounts.distributions(a.id, "matched");
        for (const d of dists) if (d.reimbursement_id) next.add(d.reimbursement_id);
      } catch {
        // Account might be gone or backend errored — skip.
      }
    }
    matchedPullIds = next;
  })();
  try {
    await matchedFetchInFlight;
  } finally {
    matchedFetchInFlight = null;
  }
}

// ── Detail view ───────────────────────────────────────────────────────────────

async function renderDetail(container, pullId) {
  container.innerHTML = `<div class="empty-state">Loading…</div>`;

  let pull, accounts;
  try {
    [pull, accounts] = await Promise.all([
      api.reimbursements.get(pullId),
      api.accounts.list().catch(() => []),
    ]);
  } catch (err) {
    container.innerHTML = `
      <div class="section-header"><h2>Pull not found</h2></div>
      <p class="text-muted">${esc(err.message)}</p>
      <p><a href="#/pulls">← Back to all pulls</a></p>
    `;
    return;
  }

  const items = pull.line_items;

  const lineRow = li => {
    const partial = li.covered_amount + 0.005 < li.expense_amount;
    const amountCell = partial
      ? `<span style="color:var(--green);font-weight:600">${fmt(li.covered_amount)}</span><span class="text-muted" style="font-size:12px"> of ${fmt(li.expense_amount)}</span>`
      : `<span style="font-weight:600">${fmt(li.covered_amount)}</span>`;
    return { partial, amountCell };
  };

  // Account/distribution panel — only shown when the user has at least one account.
  const accountPanel = accounts.length === 0 ? "" : renderPullAccountPanel(pull, accounts);

  container.innerHTML = `
    <div style="margin-bottom:12px">
      <a href="#/pulls" class="text-muted" style="font-size:13px">← All pulls</a>
    </div>

    <div class="vault-hero">
      <div class="vault-label">Pull · ${fmtDate(pull.date)}</div>
      <div class="vault-amount" style="color:var(--green)">${fmt(pull.total_amount)}</div>
      <div class="vault-sub">
        ${pull.expense_count} receipt${pull.expense_count !== 1 ? "s" : ""} backing this pull
        ${pull.reference ? ` · Ref ${esc(pull.reference)}` : ""}
      </div>
      ${pull.notes ? `<div class="vault-sub" style="margin-top:8px;font-style:italic">${esc(pull.notes)}</div>` : ""}
    </div>

    ${accountPanel}

    <div class="section-header">
      <h2>Receipts backing this pull</h2>
    </div>

    <div class="card" style="padding:0;overflow:hidden">
      <div class="table-wrap desktop-only-table">
        <table>
          <thead>
            <tr>
              <th>Merchant</th>
              <th>Date</th>
              <th>Category</th>
              <th class="col-amount">Covered</th>
              <th>Receipts</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(li => {
              const { amountCell } = lineRow(li);
              return `
                <tr>
                  <td><strong>${esc(li.merchant)}</strong></td>
                  <td>${fmtDate(li.date)}</td>
                  <td><span class="badge badge-category">${esc(li.category)}</span></td>
                  <td class="col-amount">${amountCell}</td>
                  <td><span class="badge badge-receipt">📎 ${li.receipt_count}</span></td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      </div>
    </div>

    <div class="mobile-only-cards">
      ${items.map(li => {
        const { amountCell } = lineRow(li);
        return `
          <div class="expense-card">
            <div class="expense-card-info">
              <div class="expense-card-merchant">${esc(li.merchant)}</div>
              <div class="expense-card-meta">${fmtDate(li.date)} · <span class="badge badge-category" style="font-size:10px">${esc(li.category)}</span></div>
            </div>
            <div class="expense-card-right">
              <span class="expense-card-amount">${amountCell}</span>
              <span class="badge badge-receipt">📎 ${li.receipt_count}</span>
            </div>
          </div>
        `;
      }).join("")}
    </div>

    <div style="margin-top:24px;display:flex;justify-content:flex-end">
      <button id="undo-pull-btn" class="btn btn-danger">Undo this pull</button>
    </div>
  `;

  injectStyles();

  if (accounts.length > 0) {
    wirePullAccountPanel(pull, accounts);
  }

  document.getElementById("undo-pull-btn").addEventListener("click", async () => {
    const ok = confirm(
      "Undo this pull?\n\n"
      + `${pull.expense_count} receipt slice${pull.expense_count !== 1 ? "s" : ""} `
      + `(${fmt(pull.total_amount)} total) will be returned to the vault as unreimbursed.`
    );
    if (!ok) return;

    try {
      await api.reimbursements.remove(pull.id);
      toast(`Pull undone — ${fmt(pull.total_amount)} returned to vault`);
      location.hash = "#/pulls";
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

// ── Pull detail: account/distribution panel ──────────────────────────────────

function renderPullAccountPanel(pull, accounts) {
  const activeAccounts = accounts.filter(a => a.is_active || a.id === pull.account_id);
  const tagged = !!pull.account_id;
  const matched = !!pull.distribution;

  const acctOptions = `
    <option value="">— No account —</option>
    ${activeAccounts.map(a => `<option value="${a.id}" ${pull.account_id === a.id ? "selected" : ""}>${esc(a.name)}</option>`).join("")}
  `;

  let bodyHtml;
  if (!tagged) {
    bodyHtml = `<div class="text-muted" style="font-size:13px">Tag this pull to one of your accounts to enable reconciliation.</div>`;
  } else if (matched) {
    const d = pull.distribution;
    bodyHtml = `
      <div class="recon-matched">
        <div><strong>${fmt(d.amount)}</strong> · ${fmtDate(d.date)} <span class="badge badge-green" style="margin-left:6px">Matched</span></div>
        <div class="text-muted" style="font-size:12px;margin-top:2px">
          ${esc(d.description || "(no description)")}
          ${d.custodian_ref ? ` · ref ${esc(d.custodian_ref)}` : ""}
        </div>
        <div style="margin-top:8px"><button id="pull-unmatch-btn" class="btn btn-secondary btn-sm">Unlink distribution</button></div>
      </div>
    `;
  } else {
    bodyHtml = `
      <div class="text-muted" style="font-size:13px;margin-bottom:8px">
        Not yet matched to a custodian distribution.
      </div>
      <button id="pull-find-match-btn" class="btn btn-primary btn-sm">Find match…</button>
    `;
  }

  return `
    <div class="card pull-account-panel">
      <div class="form-row" style="align-items:end">
        <div class="form-group" style="margin-bottom:0">
          <label>HSA account</label>
          <select id="pull-account-select">${acctOptions}</select>
        </div>
        <div class="form-group" style="margin-bottom:0">
          <label>&nbsp;</label>
          <button id="pull-account-save" class="btn btn-secondary btn-sm" disabled>Save</button>
        </div>
      </div>
      <hr class="divider">
      <div class="pull-distribution-body">${bodyHtml}</div>
    </div>
  `;
}

function wirePullAccountPanel(pull, accounts) {
  const select = document.getElementById("pull-account-select");
  const saveBtn = document.getElementById("pull-account-save");
  if (!select || !saveBtn) return;

  const original = String(pull.account_id ?? "");
  select.addEventListener("change", () => {
    saveBtn.disabled = String(select.value) === original;
  });
  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    try {
      const next = select.value === "" ? null : parseInt(select.value);
      await api.reimbursements.update(pull.id, { account_id: next });
      if (next) localStorage.setItem(LAST_USED_ACCOUNT, String(next));
      toast("Account updated");
      await renderDetail(document.getElementById("app"), pull.id);
    } catch (err) {
      saveBtn.textContent = "Save";
      saveBtn.disabled = false;
      toast(err.message, "error");
    }
  });

  document.getElementById("pull-unmatch-btn")?.addEventListener("click", async () => {
    if (!pull.distribution) return;
    if (!confirm("Unlink this distribution from the pull?")) return;
    try {
      await api.distributions.unmatch(pull.distribution.id);
      toast("Unlinked");
      await renderDetail(document.getElementById("app"), pull.id);
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.getElementById("pull-find-match-btn")?.addEventListener("click", async () => {
    await showFindMatchForPull(pull);
  });
}

async function showFindMatchForPull(pull) {
  if (!pull.account_id) {
    toast("Tag this pull to an account first", "error");
    return;
  }
  let dists;
  try {
    dists = await api.accounts.distributions(pull.account_id, "unmatched");
  } catch (err) {
    toast(err.message, "error");
    return;
  }

  const pullDate = new Date(pull.date + "T00:00:00").getTime();
  const candidates = dists
    .filter(d => Math.abs((new Date(d.date + "T00:00:00").getTime() - pullDate) / 86400000) <= 30)
    .sort((a, b) => Math.abs(a.amount - pull.total_amount) - Math.abs(b.amount - pull.total_amount));

  openModal(`
    <div class="modal-title">Find a distribution to link</div>
    <p class="text-muted" style="font-size:13px;margin-bottom:12px">
      Pull: <strong>${fmt(pull.total_amount)}</strong> on ${fmtDate(pull.date)}
    </p>
    ${candidates.length === 0 ? `
      <div class="empty-state">No unmatched distributions for this account within ±30 days.</div>
    ` : `
      <div style="max-height:360px;overflow-y:auto;border:1px solid var(--border);border-radius:var(--radius)">
        ${candidates.map(d => {
          const diff = d.amount - pull.total_amount;
          const days = Math.round((new Date(d.date + "T00:00:00").getTime() - pullDate) / 86400000);
          const exact = Math.abs(diff) < 0.01;
          return `
            <div class="recon-row">
              <div class="recon-row-left">
                <div class="recon-row-main">
                  ${fmt(d.amount)} · ${fmtDate(d.date)}
                  ${exact ? `<span class="badge badge-green" style="margin-left:6px">exact</span>` : ""}
                </div>
                <div class="recon-row-sub">
                  ${esc(d.description || "(no description)")}
                  · ${days === 0 ? "same day" : `${days > 0 ? "+" : ""}${days}d`}
                  ${!exact ? ` · off by ${fmt(Math.abs(diff))}` : ""}
                </div>
              </div>
              <div class="recon-row-actions">
                <button class="btn btn-primary btn-sm" data-link-distrib="${d.id}">Link</button>
              </div>
            </div>
          `;
        }).join("")}
      </div>
    `}
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
      <button id="find-match-cancel" class="btn btn-secondary">Cancel</button>
    </div>
  `);

  document.getElementById("find-match-cancel").addEventListener("click", closeModal);
  document.querySelectorAll("[data-link-distrib]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const distId = parseInt(btn.dataset.linkDistrib);
      try {
        await api.distributions.match(distId, { reimbursement_id: pull.id });
        toast("Linked");
        closeModal();
        await renderDetail(document.getElementById("app"), pull.id);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });
}

// ── New Pull modal (amount-first allocation) ──────────────────────────────────

async function showNewPullModal() {
  let candidates, accounts;
  try {
    // Pull picker shows anything still unreimbursed (includes partials with remaining > 0).
    [candidates, accounts] = await Promise.all([
      api.expenses.list({ reimbursed: false }),
      api.accounts.list().catch(() => []),
    ]);
  } catch (err) {
    toast(err.message, "error");
    return;
  }

  // Filter out anything with no remaining (defensive — server should already exclude).
  candidates = candidates.filter(e => (e.remaining_amount ?? e.amount) > 0.005);

  if (candidates.length === 0) {
    openModal(`
      <div class="modal-title">New Pull</div>
      <p class="text-muted">There are no expenses with remaining balance to back a pull. Add expenses first, then come back.</p>
      <div style="margin-top:16px">
        <a href="#/add" class="btn btn-primary" id="add-from-modal">+ Add Expense</a>
      </div>
    `);
    document.getElementById("add-from-modal").addEventListener("click", closeModal);
    return;
  }

  const today = new Date().toISOString().slice(0, 10);

  // Local state
  const allocations = new Map();   // expense_id -> covered_amount (number)
  const remainingById = new Map(); // expense_id -> remaining_amount (cap)
  candidates.forEach(e => remainingById.set(e.id, round2(e.remaining_amount ?? e.amount)));
  let totalAmount = 0;

  openModal(`
    <div class="modal-title">New Pull</div>
    <p class="text-muted" style="font-size:13px;margin-bottom:14px">
      Enter the HSA distribution amount, then allocate it across receipts. Allocations must sum exactly to the distribution amount.
    </p>

    <div class="form-group">
      <label for="pull-amount">Distribution amount</label>
      <input type="number" id="pull-amount" inputmode="decimal" step="0.01" min="0.01" placeholder="0.00" autofocus
             style="font-size:20px;font-weight:600;font-variant-numeric:tabular-nums">
    </div>

    <div id="pull-progress" class="pull-progress muted">
      <span id="pull-progress-text">Enter an amount to begin allocating</span>
      <div class="pull-progress-track"><div class="pull-progress-fill" id="pull-progress-fill"></div></div>
    </div>

    <div class="form-group">
      <label>Receipts (${candidates.length} with remaining balance)</label>
      <div id="pull-expense-list" class="pull-picker">
        ${candidates.map(e => {
          const remaining = remainingById.get(e.id);
          const partial = (e.covered_amount ?? 0) > 0.005;
          const subParts = [
            fmtDate(e.date),
            esc(e.category),
          ];
          if (e.receipts.length === 0) subParts.push(`<span style="color:var(--yellow)">no receipt</span>`);
          if (partial) subParts.push(`<span style="color:var(--green)">${fmt(e.covered_amount)} already pulled</span>`);
          return `
            <div class="pull-picker-row" data-row="${e.id}">
              <label class="pull-picker-row-main">
                <input type="checkbox" class="pull-pick" data-id="${e.id}" data-remaining="${remaining}">
                <div class="pull-picker-meta">
                  <div class="pull-picker-merchant">${esc(e.merchant)}</div>
                  <div class="pull-picker-sub">${subParts.join(" · ")}</div>
                </div>
                <div class="pull-picker-amount">${fmt(remaining)}${partial ? `<div class="pull-picker-amount-sub text-muted">of ${fmt(e.amount)}</div>` : ""}</div>
              </label>
              <div class="pull-picker-alloc" data-alloc="${e.id}" hidden>
                <span class="pull-alloc-prefix">$</span>
                <input type="number" class="pull-alloc-input" data-id="${e.id}" inputmode="decimal" step="0.01" min="0.01" max="${remaining}">
                <span class="pull-alloc-suffix text-muted">of ${fmt(remaining)} remaining</span>
              </div>
            </div>
          `;
        }).join("")}
      </div>
    </div>

    <div class="form-row">
      <div class="form-group">
        <label>Pull date</label>
        <input type="date" id="pull-date" value="${today}" max="${today}" required>
      </div>
      <div class="form-group">
        <label>Reference (optional)</label>
        <input type="text" id="pull-reference" placeholder="HSA portal #, check #">
      </div>
    </div>

    ${(() => {
      const activeAccts = accounts.filter(a => a.is_active);
      if (activeAccts.length === 0) return "";
      const lastUsed = localStorage.getItem(LAST_USED_ACCOUNT);
      const defaultId = activeAccts.some(a => String(a.id) === lastUsed) ? lastUsed : "";
      return `
        <div class="form-group">
          <label>HSA account (optional)</label>
          <select id="pull-account">
            <option value="">— No account —</option>
            ${activeAccts.map(a => `<option value="${a.id}" ${String(a.id) === defaultId ? "selected" : ""}>${esc(a.name)}</option>`).join("")}
          </select>
        </div>
      `;
    })()}

    <div class="form-group">
      <label>Notes (optional)</label>
      <textarea id="pull-notes" rows="2" placeholder="Anything to remember about this distribution"></textarea>
    </div>

    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:20px;padding-top:14px;border-top:1px solid var(--border)">
      <div>
        <div class="text-muted" style="font-size:11px;text-transform:uppercase;letter-spacing:.05em">Allocated</div>
        <div id="pull-running-total" style="font-size:22px;font-weight:700;font-variant-numeric:tabular-nums">$0.00 / $0.00</div>
      </div>
      <div style="display:flex;gap:8px">
        <button id="pull-cancel" class="btn btn-secondary">Cancel</button>
        <button id="pull-submit" class="btn btn-primary" disabled>Pull $0.00</button>
      </div>
    </div>
  `);

  injectStyles();

  const amountInput = document.getElementById("pull-amount");
  const totalEl = document.getElementById("pull-running-total");
  const submitBtn = document.getElementById("pull-submit");
  const progressBox = document.getElementById("pull-progress");
  const progressText = document.getElementById("pull-progress-text");
  const progressFill = document.getElementById("pull-progress-fill");

  function getAllocated() {
    let s = 0;
    allocations.forEach(v => { s += v; });
    return round2(s);
  }

  function updateUI() {
    const allocated = getAllocated();
    totalEl.textContent = `${fmt(allocated)} / ${fmt(totalAmount)}`;

    const diff = round2(allocated - totalAmount);
    let state, text;
    if (totalAmount <= 0) {
      state = "muted";
      text = "Enter an amount to begin allocating";
    } else if (Math.abs(diff) < 0.005) {
      state = "match";
      text = `Allocated ${fmt(allocated)} — ready to pull`;
    } else if (diff > 0) {
      state = "over";
      text = `Over by ${fmt(diff)} — reduce an allocation`;
    } else {
      state = "under";
      text = `Need ${fmt(-diff)} more in allocations`;
    }
    progressText.textContent = text;
    progressBox.dataset.state = state;
    const pct = totalAmount > 0 ? Math.min(100, (allocated / totalAmount) * 100) : 0;
    progressFill.style.width = pct + "%";

    const ready = totalAmount > 0 && allocations.size > 0 && Math.abs(diff) < 0.005;
    submitBtn.disabled = !ready;
    submitBtn.textContent = ready ? `Pull ${fmt(allocated)}` : (totalAmount > 0 ? `Pull ${fmt(totalAmount)}` : "Pull $0.00");
  }

  function setAllocation(id, value, { writeBack = true } = {}) {
    const remaining = remainingById.get(id);
    let v = round2(value);
    if (!Number.isFinite(v) || v <= 0) {
      allocations.delete(id);
    } else {
      if (v > remaining) v = remaining;
      allocations.set(id, v);
    }
    if (writeBack) {
      const input = document.querySelector(`.pull-alloc-input[data-id="${id}"]`);
      if (input) {
        input.value = allocations.has(id) ? allocations.get(id).toFixed(2) : "";
      }
    }
  }

  function autoFill(id) {
    // Default this row to min(remaining_to_allocate, remaining on this expense).
    const remaining = remainingById.get(id);
    const stillNeeded = round2(totalAmount - getAllocated());
    if (totalAmount <= 0) {
      // No target amount yet — default to expense's remaining.
      setAllocation(id, remaining);
    } else if (stillNeeded <= 0) {
      // Already at or over target — start at 0.01 so user notices.
      setAllocation(id, Math.min(0.01, remaining));
    } else {
      setAllocation(id, Math.min(stillNeeded, remaining));
    }
  }

  amountInput.addEventListener("input", () => {
    const raw = parseFloat(amountInput.value);
    totalAmount = Number.isFinite(raw) && raw > 0 ? round2(raw) : 0;
    updateUI();
  });

  // Checkbox selection
  document.querySelectorAll(".pull-pick").forEach(cb => {
    cb.addEventListener("change", e => {
      const id = parseInt(e.target.dataset.id);
      const allocBox = document.querySelector(`[data-alloc="${id}"]`);
      const row = document.querySelector(`[data-row="${id}"]`);
      if (e.target.checked) {
        allocBox.hidden = false;
        row.classList.add("selected");
        autoFill(id);
      } else {
        allocBox.hidden = true;
        row.classList.remove("selected");
        allocations.delete(id);
      }
      updateUI();
    });
  });

  // Per-row amount input
  document.querySelectorAll(".pull-alloc-input").forEach(inp => {
    inp.addEventListener("input", e => {
      const id = parseInt(e.target.dataset.id);
      const v = parseFloat(e.target.value);
      setAllocation(id, v, { writeBack: false });
      updateUI();
    });
    // Re-clamp on blur so the displayed value reflects what was actually set.
    inp.addEventListener("blur", e => {
      const id = parseInt(e.target.dataset.id);
      const v = parseFloat(e.target.value);
      setAllocation(id, v);
      updateUI();
    });
  });

  document.getElementById("pull-cancel").addEventListener("click", closeModal);

  submitBtn.addEventListener("click", async () => {
    if (submitBtn.disabled) return;

    const lineItems = [...allocations.entries()].map(([id, amt]) => ({
      expense_id: id,
      covered_amount: round2(amt),
    }));

    const body = {
      date: document.getElementById("pull-date").value,
      reference: document.getElementById("pull-reference").value.trim() || null,
      notes: document.getElementById("pull-notes").value.trim() || null,
      total_amount: round2(totalAmount),
      line_items: lineItems,
    };

    const acctSel = document.getElementById("pull-account");
    const chosenAcct = acctSel && acctSel.value ? parseInt(acctSel.value) : null;

    submitBtn.disabled = true;
    submitBtn.textContent = "Pulling…";

    try {
      const pull = await api.reimbursements.create(body);
      // Tag the account separately. ReimbursementCreate doesn't accept account_id today, so the
      // PUT endpoint added in v0.5 carries the assignment. Best-effort — failure is non-fatal.
      if (chosenAcct) {
        try {
          await api.reimbursements.update(pull.id, { account_id: chosenAcct });
          localStorage.setItem(LAST_USED_ACCOUNT, String(chosenAcct));
        } catch (err) {
          toast(`Pull saved, but tagging the account failed: ${err.message}`, "error");
        }
      }
      toast(`Pulled ${fmt(pull.total_amount)} across ${pull.expense_count} receipt${pull.expense_count !== 1 ? "s" : ""}`);
      closeModal();
      if (location.hash === "#/pulls") {
        await loadList();
      } else {
        location.hash = "#/pulls";
      }
    } catch (err) {
      toast(err.message, "error");
      updateUI();
    }
  });

  updateUI();
}

// ── Styles (scoped to pulls page, injected once) ──────────────────────────────

function injectStyles() {
  if (document.getElementById("pulls-page-style")) return;
  const s = document.createElement("style");
  s.id = "pulls-page-style";
  s.textContent = `
    @media (max-width: 767px) { .desktop-only-table { display: none; } }
    @media (min-width: 768px) { .mobile-only-cards { display: none; } }

    .pull-card {
      display: flex; align-items: center; gap: 16px;
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 16px 20px; margin-bottom: 10px;
      text-decoration: none; color: var(--text);
      transition: border-color .15s, transform .12s;
    }
    .pull-card:hover { border-color: var(--brand-light); transform: translateY(-1px); }
    .pull-card-left { flex: 1; min-width: 0; }
    .pull-card-date { font-size: 16px; font-weight: 600; }
    .pull-card-meta { font-size: 12px; color: var(--muted); margin-top: 3px; }
    .pull-card-amount { font-size: 20px; font-weight: 700; color: var(--green); font-variant-numeric: tabular-nums; }

    .pull-picker {
      max-height: 320px; overflow-y: auto;
      border: 1px solid var(--border); border-radius: var(--radius);
      background: var(--surface2);
    }
    .pull-picker-row {
      border-bottom: 1px solid var(--border);
      transition: background .12s;
    }
    .pull-picker-row:last-child { border-bottom: none; }
    .pull-picker-row.selected { background: var(--surface); }
    .pull-picker-row-main {
      display: flex; align-items: center; gap: 12px;
      padding: 10px 12px; cursor: pointer; margin: 0;
    }
    .pull-picker-row-main:hover { background: var(--surface); }
    .pull-picker-row-main input[type="checkbox"] { width: auto; margin: 0; flex-shrink: 0; }
    .pull-picker-meta { flex: 1; min-width: 0; }
    .pull-picker-merchant { font-size: 13px; font-weight: 600; }
    .pull-picker-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }
    .pull-picker-amount { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; flex-shrink: 0; text-align: right; }
    .pull-picker-amount-sub { font-size: 10px; font-weight: 400; margin-top: 1px; }

    .pull-picker-alloc {
      display: flex; align-items: center; gap: 8px;
      padding: 8px 14px 12px 38px;
      background: var(--surface);
    }
    .pull-picker-alloc[hidden] { display: none; }
    .pull-alloc-prefix { color: var(--muted); font-weight: 600; }
    .pull-alloc-input {
      width: 100px; padding: 6px 8px;
      font-size: 14px; font-variant-numeric: tabular-nums;
    }
    .pull-alloc-suffix { font-size: 12px; }

    .pull-progress {
      margin: -4px 0 14px;
      padding: 10px 12px;
      border-radius: var(--radius);
      border: 1px solid var(--border);
      background: var(--surface2);
      font-size: 13px;
    }
    .pull-progress-track {
      height: 4px; background: var(--border);
      border-radius: 99px; margin-top: 6px; overflow: hidden;
    }
    .pull-progress-fill {
      height: 100%; width: 0%; background: var(--muted);
      transition: width .18s, background .18s;
    }
    .pull-progress[data-state="match"] { border-color: var(--green); color: var(--green); }
    .pull-progress[data-state="match"] .pull-progress-fill { background: var(--green); }
    .pull-progress[data-state="under"] .pull-progress-fill { background: var(--brand-light, var(--green)); }
    .pull-progress[data-state="over"] { border-color: var(--red); color: var(--red); }
    .pull-progress[data-state="over"] .pull-progress-fill { background: var(--red); width: 100% !important; }
    .pull-progress[data-state="muted"] { color: var(--muted); }

    /* v0.5 — account / reconciliation surfaces */
    .pull-card-tags { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 6px; }
    .pull-badge {
      display: inline-block; padding: 2px 9px; border-radius: 999px;
      font-size: 11px; font-weight: 600; line-height: 1.4;
    }
    .pull-badge.green { background: var(--green); color: #fff; }
    .pull-badge.amber { background: var(--yellow); color: #000; }
    .pull-badge.gray  { background: var(--surface2); color: var(--muted); border: 1px solid var(--border); }

    .pull-filter-row {
      display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
      margin-bottom: 16px;
    }
    .pull-account-filter {
      width: auto; min-width: 160px;
      padding: 6px 10px; font-size: 12px;
    }

    .legacy-banner {
      display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
      padding: 12px 14px; margin-bottom: 14px;
      background: var(--surface2); border: 1px solid var(--border);
      border-left: 3px solid var(--accent);
      border-radius: var(--radius);
    }
    .legacy-banner-text { flex: 1; min-width: 200px; font-size: 13px; }
    .legacy-banner-actions { display: flex; gap: 6px; }

    .pull-account-panel { margin-bottom: 24px; padding: 14px 16px; }
    .pull-distribution-body { font-size: 14px; }
    .recon-row {
      display: flex; align-items: center; gap: 12px;
      padding: 10px 14px; border-bottom: 1px solid var(--border);
    }
    .recon-row:last-child { border-bottom: none; }
    .recon-row-left { flex: 1; min-width: 0; }
    .recon-row-main { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .recon-row-sub  { font-size: 12px; color: var(--muted); margin-top: 2px; }
    .recon-row-actions { flex-shrink: 0; }
    .badge-green { background: var(--green); color: #fff; }
  `;
  document.head.appendChild(s);
}
