import { api } from "../api.js";
import { toast, openModal, closeModal } from "../app.js";

const fmt = n => "$" + Math.abs(Number(n) || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtDate = d => d ? new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "—";
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const today = () => new Date().toISOString().slice(0, 10);

const CANONICAL_FIELDS = ["date", "amount", "description", "custodian_ref"];
const CANONICAL_LABELS = {
  date: "Date",
  amount: "Amount",
  description: "Description",
  custodian_ref: "Transaction ID",
  "": "(ignore)",
};

export async function render(container, params = []) {
  injectStyles();
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
      <h2>HSA Accounts</h2>
      <div class="spacer"></div>
      <button id="new-account-btn" class="btn btn-primary btn-sm">+ New Account</button>
    </div>
    <p class="text-muted" style="margin-bottom:18px;font-size:13px">
      Track each HSA you own. Import custodian CSVs to reconcile against your Pulls and record balances over time.
    </p>
    <div id="accounts-list"></div>
  `;

  document.getElementById("new-account-btn").addEventListener("click", () => showAccountModal(null));
  await loadAccounts();
}

async function loadAccounts() {
  const el = document.getElementById("accounts-list");
  el.innerHTML = `<div class="empty-state">Loading…</div>`;

  let accounts;
  try {
    accounts = await api.accounts.list();
  } catch (err) {
    el.innerHTML = `<div class="empty-state">Failed to load accounts: ${esc(err.message)}</div>`;
    return;
  }

  if (accounts.length === 0) {
    el.innerHTML = `
      <div class="empty-state">
        No accounts yet.<br>
        Add your HSA to start importing custodian CSVs and reconciling Pulls.
      </div>
    `;
    return;
  }

  el.innerHTML = accounts.map(a => {
    const totalBalance = (a.cash_balance || 0) + (a.invested_balance || 0);
    const inactiveTag = a.is_active ? "" : `<span class="badge badge-muted">Deactivated</span>`;
    const maskTag = a.account_mask ? `<span class="text-muted">· ${esc(a.account_mask)}</span>` : "";
    const unmatchedTag = a.unmatched_distribution_count > 0
      ? `<span class="badge badge-amber">${a.unmatched_distribution_count} unmatched</span>`
      : "";
    return `
      <a class="account-card" href="#/accounts/${a.id}">
        <div class="account-card-left">
          <div class="account-card-name">
            ${esc(a.name)} ${inactiveTag}
          </div>
          <div class="account-card-sub">
            ${esc(a.custodian)} ${maskTag}
            · ${a.pull_count} pull${a.pull_count !== 1 ? "s" : ""}
            · ${a.distribution_count} distribution${a.distribution_count !== 1 ? "s" : ""}
            ${unmatchedTag}
          </div>
        </div>
        <div class="account-card-right">
          <div class="account-card-balance">${fmt(totalBalance)}</div>
          <div class="account-card-balance-sub text-muted">
            ${a.last_snapshot_date ? `as of ${fmtDate(a.last_snapshot_date)}` : "no balance recorded"}
          </div>
        </div>
      </a>
    `;
  }).join("");
}

// ── Account create/edit modal ─────────────────────────────────────────────────

function showAccountModal(account) {
  const isEdit = !!account;
  openModal(`
    <div class="modal-title">${isEdit ? "Edit Account" : "New HSA Account"}</div>
    <form id="account-form">
      <div class="form-group">
        <label>Account name</label>
        <input name="name" value="${esc(account?.name || "")}" placeholder="e.g. Fidelity HSA" required autofocus>
      </div>
      <div class="form-group">
        <label>Custodian</label>
        <input name="custodian" value="${esc(account?.custodian || "")}" placeholder="e.g. Fidelity" required>
      </div>
      <div class="form-group">
        <label>Account mask (optional)</label>
        <input name="account_mask" value="${esc(account?.account_mask || "")}" maxlength="12"
               placeholder="Last 4 or a nickname — never the full number">
      </div>
      <div class="form-group">
        <label>Notes (optional)</label>
        <textarea name="notes" rows="2">${esc(account?.notes || "")}</textarea>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
        <button type="button" id="account-cancel" class="btn btn-secondary">Cancel</button>
        <button type="submit" class="btn btn-primary">${isEdit ? "Save" : "Create Account"}</button>
      </div>
    </form>
  `);

  document.getElementById("account-cancel").addEventListener("click", closeModal);

  document.getElementById("account-form").addEventListener("submit", async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {
      name: fd.get("name").trim(),
      custodian: fd.get("custodian").trim(),
      account_mask: (fd.get("account_mask") || "").trim() || null,
      notes: (fd.get("notes") || "").trim() || null,
    };
    try {
      if (isEdit) {
        await api.accounts.update(account.id, body);
        toast("Account updated");
      } else {
        await api.accounts.create(body);
        toast("Account created");
      }
      closeModal();
      // Reload whichever view we're on.
      if (location.hash === "#/accounts") {
        await loadAccounts();
      } else {
        await renderDetail(document.getElementById("app"), account?.id || (await api.accounts.list()).slice(-1)[0]?.id);
      }
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

// ── Detail view ───────────────────────────────────────────────────────────────

async function renderDetail(container, accountId) {
  container.innerHTML = `<div class="empty-state">Loading…</div>`;

  let account, recon, snapshots;
  try {
    [account, recon, snapshots] = await Promise.all([
      api.accounts.get(accountId),
      api.accounts.reconciliation(accountId),
      api.accounts.snapshots.list(accountId),
    ]);
  } catch (err) {
    container.innerHTML = `
      <div class="section-header"><h2>Account not found</h2></div>
      <p class="text-muted">${esc(err.message)}</p>
      <p><a href="#/accounts">← Back to all accounts</a></p>
    `;
    return;
  }

  const totalBalance = (account.cash_balance || 0) + (account.invested_balance || 0);
  const inactiveTag = account.is_active ? "" : `<span class="badge badge-muted">Deactivated</span>`;

  container.innerHTML = `
    <div style="margin-bottom:12px">
      <a href="#/accounts" class="text-muted" style="font-size:13px">← All accounts</a>
    </div>

    <div class="vault-hero">
      <div class="vault-label">${esc(account.custodian)} ${account.account_mask ? `· ${esc(account.account_mask)}` : ""} ${inactiveTag}</div>
      <div class="vault-amount">${esc(account.name)}</div>
      <div class="vault-sub">
        Current balance: <strong>${fmt(totalBalance)}</strong>
        ${account.last_snapshot_date ? ` · as of ${fmtDate(account.last_snapshot_date)}` : ` · no balance recorded`}
      </div>
      <div class="vault-hero-stats" style="margin-top:14px">
        <div>
          <div class="vault-stat-label">Cash</div>
          <div class="vault-stat-value">${fmt(account.cash_balance)}</div>
        </div>
        <div>
          <div class="vault-stat-label">Invested</div>
          <div class="vault-stat-value">${fmt(account.invested_balance)}</div>
        </div>
        <div>
          <div class="vault-stat-label">Pulls tagged</div>
          <div class="vault-stat-value">${account.pull_count}</div>
        </div>
        <div>
          <div class="vault-stat-label">Distributions</div>
          <div class="vault-stat-value">${account.distribution_count}</div>
        </div>
      </div>
    </div>

    <div class="filter-tabs" id="account-tabs">
      <button class="filter-tab active" data-tab="reconcile">Reconcile</button>
      <button class="filter-tab" data-tab="snapshots">Balances</button>
      <button class="filter-tab" data-tab="manage">Manage</button>
    </div>

    <div id="tab-content"></div>
  `;

  document.getElementById("account-tabs").addEventListener("click", e => {
    const btn = e.target.closest(".filter-tab");
    if (!btn) return;
    document.querySelectorAll("#account-tabs .filter-tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    if (tab === "reconcile") renderReconcileTab(account, recon);
    else if (tab === "snapshots") renderSnapshotsTab(account, snapshots);
    else renderManageTab(account);
  });

  renderReconcileTab(account, recon);
}

// ── Reconcile tab ─────────────────────────────────────────────────────────────

function renderReconcileTab(account, recon) {
  const el = document.getElementById("tab-content");
  const matchedCount = recon.matched.length;
  const unmatchedDistribCount = recon.unmatched_distributions.length;
  const unmatchedPullCount = recon.unmatched_pulls.length;

  el.innerHTML = `
    <div class="section-header" style="margin-top:12px">
      <h2>Reconciliation</h2>
      <div class="spacer"></div>
      <button id="import-csv-btn" class="btn btn-secondary btn-sm">Import CSV…</button>
      <button id="auto-reconcile-btn" class="btn btn-primary btn-sm" ${unmatchedDistribCount === 0 ? "disabled" : ""}>
        Auto-match
      </button>
    </div>

    ${recon.matched.length === 0 && recon.unmatched_distributions.length === 0 && recon.unmatched_pulls.length === 0 ? `
      <div class="empty-state">
        No data yet. Import a custodian CSV to compare distributions against your Pulls.
      </div>
    ` : ""}

    ${unmatchedDistribCount > 0 ? `
      <div class="card" style="padding:0;overflow:hidden;margin-bottom:14px">
        <div class="recon-section-header amber">
          Unmatched distributions <span class="recon-count">${unmatchedDistribCount}</span>
          <span class="recon-section-sub">— custodian says money left, no matching Pull</span>
        </div>
        <div class="recon-list">
          ${recon.unmatched_distributions.map(d => `
            <div class="recon-row" data-distribution="${d.id}">
              <div class="recon-row-left">
                <div class="recon-row-main">${fmt(d.amount)} · ${fmtDate(d.date)}</div>
                <div class="recon-row-sub">${esc(d.description || "(no description)")} ${d.custodian_ref ? `· ref ${esc(d.custodian_ref)}` : ""}</div>
              </div>
              <div class="recon-row-actions">
                <button class="btn btn-secondary btn-sm" data-find-match="${d.id}">Find match</button>
                <button class="btn btn-danger btn-sm" data-delete-distrib="${d.id}">Delete</button>
              </div>
            </div>
          `).join("")}
        </div>
      </div>
    ` : ""}

    ${unmatchedPullCount > 0 ? `
      <div class="card" style="padding:0;overflow:hidden;margin-bottom:14px">
        <div class="recon-section-header amber">
          Unmatched pulls <span class="recon-count">${unmatchedPullCount}</span>
          <span class="recon-section-sub">— Pull recorded, but no matching distribution from the custodian</span>
        </div>
        <div class="recon-list">
          ${recon.unmatched_pulls.map(p => `
            <div class="recon-row">
              <div class="recon-row-left">
                <div class="recon-row-main">${fmt(p.total_amount)} · ${fmtDate(p.date)}</div>
                <div class="recon-row-sub">${p.reference ? `Ref ${esc(p.reference)}` : "(no reference)"} · <a href="#/pulls/${p.id}">View pull →</a></div>
              </div>
            </div>
          `).join("")}
        </div>
      </div>
    ` : ""}

    ${matchedCount > 0 ? `
      <div class="card" style="padding:0;overflow:hidden">
        <div class="recon-section-header green" id="matched-toggle" style="cursor:pointer">
          Matched <span class="recon-count">${matchedCount}</span>
          <span class="recon-section-sub" id="matched-toggle-label">— click to expand</span>
        </div>
        <div class="recon-list" id="matched-list" hidden>
          ${recon.matched.map(m => `
            <div class="recon-row">
              <div class="recon-row-left">
                <div class="recon-row-main">${fmt(m.distribution.amount)} · ${fmtDate(m.distribution.date)}</div>
                <div class="recon-row-sub">
                  ${esc(m.distribution.description || "(no description)")}
                  · linked to pull <a href="#/pulls/${m.pull_id}">${fmtDate(m.pull_date)}</a>
                  · <span class="text-muted">(${esc(m.distribution.match_method || "manual")})</span>
                </div>
              </div>
              <div class="recon-row-actions">
                <button class="btn btn-secondary btn-sm" data-unmatch="${m.distribution.id}">Unlink</button>
              </div>
            </div>
          `).join("")}
        </div>
      </div>
    ` : ""}
  `;

  document.getElementById("import-csv-btn").addEventListener("click", () => showImportModal(account));
  document.getElementById("auto-reconcile-btn").addEventListener("click", async () => {
    try {
      const r = await api.accounts.autoReconcile(account.id);
      toast(r.matched_count
        ? `Auto-matched ${r.matched_count} distribution${r.matched_count !== 1 ? "s" : ""}`
        : "No new matches found");
      await refreshDetail(account.id);
    } catch (err) {
      toast(err.message, "error");
    }
  });

  const matchedToggle = document.getElementById("matched-toggle");
  if (matchedToggle) {
    matchedToggle.addEventListener("click", () => {
      const list = document.getElementById("matched-list");
      const label = document.getElementById("matched-toggle-label");
      list.hidden = !list.hidden;
      label.textContent = list.hidden ? "— click to expand" : "— click to collapse";
    });
  }

  el.querySelectorAll("[data-find-match]").forEach(btn => {
    btn.addEventListener("click", () => {
      const distId = parseInt(btn.dataset.findMatch);
      const dist = recon.unmatched_distributions.find(d => d.id === distId);
      if (dist) showFindMatchModal(account, dist);
    });
  });

  el.querySelectorAll("[data-unmatch]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.unmatch);
      if (!confirm("Unlink this distribution from its Pull?")) return;
      try {
        await api.distributions.unmatch(id);
        toast("Unlinked");
        await refreshDetail(account.id);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });

  el.querySelectorAll("[data-delete-distrib]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.deleteDistrib);
      if (!confirm("Delete this distribution? This removes the imported row only — your Pulls are unaffected.")) return;
      try {
        await api.distributions.remove(id);
        toast("Distribution deleted");
        await refreshDetail(account.id);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });
}

async function refreshDetail(accountId) {
  await renderDetail(document.getElementById("app"), accountId);
}

// ── Find-match modal (manual pairing) ─────────────────────────────────────────

async function showFindMatchModal(account, distribution) {
  // Pull all unmatched pulls within ±30 days of the distribution date.
  let allPulls;
  try {
    allPulls = await api.reimbursements.list();
  } catch (err) {
    toast(err.message, "error");
    return;
  }

  // Already-matched pulls from the reconciliation snapshot — exclude these.
  let recon;
  try {
    recon = await api.accounts.reconciliation(account.id);
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  const matchedPullIds = new Set(recon.matched.map(m => m.pull_id));

  const distDate = new Date(distribution.date + "T00:00:00").getTime();
  const candidates = allPulls
    .filter(p => !matchedPullIds.has(p.id))
    .filter(p => {
      const days = Math.abs((new Date(p.date + "T00:00:00").getTime() - distDate) / 86400000);
      return days <= 30;
    })
    .sort((a, b) => Math.abs(a.total_amount - distribution.amount) - Math.abs(b.total_amount - distribution.amount));

  openModal(`
    <div class="modal-title">Find a Pull to match</div>
    <p class="text-muted" style="font-size:13px;margin-bottom:12px">
      Distribution: <strong>${fmt(distribution.amount)}</strong> on ${fmtDate(distribution.date)}
      ${distribution.description ? `· ${esc(distribution.description)}` : ""}
    </p>
    ${candidates.length === 0 ? `
      <div class="empty-state">No unmatched pulls within ±30 days.</div>
    ` : `
      <div class="recon-list" style="max-height:360px;overflow-y:auto;border:1px solid var(--border);border-radius:var(--radius)">
        ${candidates.map(p => {
          const diff = p.total_amount - distribution.amount;
          const days = Math.round((new Date(p.date + "T00:00:00").getTime() - distDate) / 86400000);
          const exact = Math.abs(diff) < 0.01;
          return `
            <div class="recon-row">
              <div class="recon-row-left">
                <div class="recon-row-main">
                  ${fmt(p.total_amount)} · ${fmtDate(p.date)}
                  ${exact ? `<span class="badge badge-green" style="margin-left:6px">exact</span>` : ""}
                </div>
                <div class="recon-row-sub">
                  ${p.reference ? `Ref ${esc(p.reference)} · ` : ""}
                  ${days === 0 ? "same day" : `${days > 0 ? "+" : ""}${days}d from distribution`}
                  ${!exact ? ` · ${diff > 0 ? "Pull is " : "Pull is short by "}${fmt(Math.abs(diff))}` : ""}
                </div>
              </div>
              <div class="recon-row-actions">
                <button class="btn btn-primary btn-sm" data-link-pull="${p.id}">Link</button>
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

  document.querySelectorAll("[data-link-pull]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const pullId = parseInt(btn.dataset.linkPull);
      try {
        await api.distributions.match(distribution.id, { reimbursement_id: pullId });
        toast("Linked");
        closeModal();
        await refreshDetail(account.id);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });
}

// ── Snapshots tab ─────────────────────────────────────────────────────────────

function renderSnapshotsTab(account, snapshots) {
  const el = document.getElementById("tab-content");
  el.innerHTML = `
    <div class="section-header" style="margin-top:12px">
      <h2>Balance snapshots</h2>
      <div class="spacer"></div>
      <button id="add-snapshot-btn" class="btn btn-primary btn-sm">+ Snapshot</button>
    </div>
    <p class="text-muted" style="margin-bottom:12px;font-size:13px">
      Record point-in-time balances. The most recent snapshot is shown as the current balance.
    </p>
    ${snapshots.length === 0 ? `
      <div class="empty-state">No snapshots yet. Add one to start tracking balance over time.</div>
    ` : `
      <div class="card" style="padding:0;overflow:hidden">
        <table class="snapshot-table">
          <thead>
            <tr>
              <th>Date</th>
              <th class="col-amount">Cash</th>
              <th class="col-amount">Invested</th>
              <th class="col-amount">Total</th>
              <th>Source</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${snapshots.map(s => `
              <tr>
                <td>${fmtDate(s.as_of_date)}</td>
                <td class="col-amount">${fmt(s.cash_balance)}</td>
                <td class="col-amount">${fmt(s.invested_balance)}</td>
                <td class="col-amount"><strong>${fmt(s.cash_balance + s.invested_balance)}</strong></td>
                <td><span class="badge badge-muted">${esc(s.source || "manual")}</span></td>
                <td>
                  <button class="btn btn-danger btn-sm" data-delete-snap="${s.id}">Delete</button>
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `}
  `;

  document.getElementById("add-snapshot-btn").addEventListener("click", () => showSnapshotModal(account));

  el.querySelectorAll("[data-delete-snap]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.deleteSnap);
      if (!confirm("Delete this snapshot?")) return;
      try {
        await api.accounts.snapshots.remove(id);
        toast("Snapshot deleted");
        await refreshDetail(account.id);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });
}

function showSnapshotModal(account) {
  openModal(`
    <div class="modal-title">New balance snapshot</div>
    <form id="snapshot-form">
      <div class="form-group">
        <label>As of date</label>
        <input type="date" name="as_of_date" value="${today()}" max="${today()}" required>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Cash balance ($)</label>
          <input type="number" step="0.01" min="0" name="cash_balance" placeholder="0.00" value="0">
        </div>
        <div class="form-group">
          <label>Invested balance ($)</label>
          <input type="number" step="0.01" min="0" name="invested_balance" placeholder="0.00" value="0">
        </div>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
        <button type="button" id="snap-cancel" class="btn btn-secondary">Cancel</button>
        <button type="submit" class="btn btn-primary">Save snapshot</button>
      </div>
    </form>
  `);
  document.getElementById("snap-cancel").addEventListener("click", closeModal);
  document.getElementById("snapshot-form").addEventListener("submit", async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {
      as_of_date: fd.get("as_of_date"),
      cash_balance: parseFloat(fd.get("cash_balance")) || 0,
      invested_balance: parseFloat(fd.get("invested_balance")) || 0,
      source: "manual",
    };
    try {
      await api.accounts.snapshots.create(account.id, body);
      toast("Snapshot saved");
      closeModal();
      await refreshDetail(account.id);
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

// ── Manage tab (edit / deactivate / delete) ───────────────────────────────────

function renderManageTab(account) {
  const el = document.getElementById("tab-content");
  el.innerHTML = `
    <div class="section-header" style="margin-top:12px">
      <h2>Manage account</h2>
    </div>
    <div class="card">
      <div class="form-group">
        <label>Account name</label>
        <div>${esc(account.name)}</div>
      </div>
      <div class="form-group">
        <label>Custodian</label>
        <div>${esc(account.custodian)}</div>
      </div>
      ${account.account_mask ? `
        <div class="form-group">
          <label>Account mask</label>
          <div>${esc(account.account_mask)}</div>
        </div>
      ` : ""}
      ${account.notes ? `
        <div class="form-group">
          <label>Notes</label>
          <div style="white-space:pre-wrap">${esc(account.notes)}</div>
        </div>
      ` : ""}
      <div class="form-group">
        <label>Status</label>
        <div>${account.is_active ? "Active" : `<span class="badge badge-muted">Deactivated</span>`}</div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
        <button id="edit-account-btn" class="btn btn-secondary">Edit</button>
        ${account.is_active
          ? `<button id="deactivate-account-btn" class="btn btn-secondary">Deactivate</button>`
          : `<button id="activate-account-btn" class="btn btn-secondary">Reactivate</button>`}
        <button id="delete-account-btn" class="btn btn-danger">Delete</button>
      </div>
      <p class="text-muted" style="font-size:12px;margin-top:10px">
        Deactivate keeps history intact and just hides the account in pickers. Delete is permanent and only allowed when no Pulls or distributions reference this account.
      </p>
    </div>
  `;

  document.getElementById("edit-account-btn").addEventListener("click", () => showAccountModal(account));

  document.getElementById("deactivate-account-btn")?.addEventListener("click", async () => {
    if (!confirm(`Deactivate ${account.name}? It will be hidden from pickers but data is preserved.`)) return;
    try {
      await api.accounts.deactivate(account.id);
      toast("Account deactivated");
      await refreshDetail(account.id);
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.getElementById("activate-account-btn")?.addEventListener("click", async () => {
    try {
      await api.accounts.update(account.id, { is_active: true });
      toast("Account reactivated");
      await refreshDetail(account.id);
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.getElementById("delete-account-btn").addEventListener("click", async () => {
    if (!confirm(`Permanently delete ${account.name}? This cannot be undone.`)) return;
    try {
      await api.accounts.remove(account.id);
      toast("Account deleted");
      location.hash = "#/accounts";
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

// ── CSV import modal (parse + preview + commit) ───────────────────────────────

function showImportModal(account) {
  openModal(`
    <div class="modal-title">Import CSV — ${esc(account.name)}</div>
    <p class="text-muted" style="font-size:13px;margin-bottom:14px">
      Upload your custodian's transaction export. The file is parsed in memory and never saved to disk.
    </p>
    <div class="form-group">
      <label class="camera-btn" id="csv-pick-label">
        <span class="camera-btn-icon">📄</span>
        <span id="csv-pick-text">Choose CSV file…</span>
        <input type="file" id="csv-file" accept=".csv,text/csv" style="display:none">
      </label>
    </div>
    <div id="csv-import-body"></div>
  `);

  const fileInput = document.getElementById("csv-file");
  const pickText = document.getElementById("csv-pick-text");

  fileInput.addEventListener("change", async () => {
    const f = fileInput.files[0];
    if (!f) return;
    pickText.textContent = `Parsing ${f.name}…`;

    const fd = new FormData();
    fd.append("file", f);

    let result;
    try {
      result = await api.accounts.parseCsv(account.id, fd);
    } catch (err) {
      pickText.textContent = "Choose CSV file…";
      toast(err.message, "error");
      return;
    }

    pickText.textContent = `Selected: ${f.name}`;
    renderImportPreview(account, result);
  });
}

function renderImportPreview(account, parseResult) {
  const body = document.getElementById("csv-import-body");
  const { headers, data_rows, suggested_map, encoding, delimiter } = parseResult;
  const totalRows = data_rows.length;

  body.innerHTML = `
    <div class="csv-banner">
      ${totalRows} row${totalRows !== 1 ? "s" : ""} detected · encoding ${esc(encoding)} · delimiter "${delimiter === "\t" ? "\\t" : esc(delimiter)}"
    </div>

    <div class="form-row" style="margin-top:12px">
      <div class="form-group">
        <label>Date format</label>
        <select id="csv-date-format">
          <option value="auto" selected>Auto-detect</option>
          <option value="YYYY-MM-DD">YYYY-MM-DD</option>
          <option value="MM/DD/YYYY">MM/DD/YYYY</option>
          <option value="DD/MM/YYYY">DD/MM/YYYY</option>
        </select>
      </div>
      <div class="form-group">
        <label>Sign convention</label>
        <select id="csv-negate">
          <option value="no" selected>Use values as-is</option>
          <option value="yes">Negate (treat negatives as distributions)</option>
        </select>
      </div>
    </div>

    <div class="form-group">
      <label>Description filter (optional)</label>
      <input id="csv-desc-filter" placeholder="e.g. distribution, withdrawal — only import rows containing this">
    </div>

    <div class="form-group">
      <label>Column mapping</label>
      <div class="csv-map-grid">
        ${headers.map(h => `
          <div class="csv-map-row">
            <span class="csv-map-header">${esc(h)}</span>
            <select class="csv-map-field" data-header="${esc(h)}">
              <option value="">(ignore)</option>
              ${CANONICAL_FIELDS.map(f => `
                <option value="${f}" ${suggested_map[h] === f ? "selected" : ""}>${CANONICAL_LABELS[f]}</option>
              `).join("")}
            </select>
          </div>
        `).join("")}
      </div>
    </div>

    <div class="form-group">
      <label>Preview</label>
      <div id="csv-preview-list"></div>
    </div>

    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:14px">
      <span id="csv-status" class="text-muted" style="font-size:13px"></span>
      <div style="display:flex;gap:8px">
        <button id="csv-cancel" class="btn btn-secondary">Cancel</button>
        <button id="csv-commit" class="btn btn-primary" disabled>Import</button>
      </div>
    </div>
  `;

  document.getElementById("csv-cancel").addEventListener("click", closeModal);

  // Live re-preview when any control changes.
  const reprev = () => updatePreview(account, parseResult, headers, data_rows);
  document.querySelectorAll(".csv-map-field").forEach(sel => sel.addEventListener("change", reprev));
  document.getElementById("csv-date-format").addEventListener("change", reprev);
  document.getElementById("csv-negate").addEventListener("change", reprev);
  document.getElementById("csv-desc-filter").addEventListener("input", reprev);

  reprev();

  document.getElementById("csv-commit").addEventListener("click", async () => {
    const { columnMap, parsedRows } = collectMappingAndRows(headers, data_rows, parseResult);
    if (parsedRows.length === 0) {
      toast("Nothing to import — check column mapping", "error");
      return;
    }
    const map = {};
    for (const [k, v] of Object.entries(columnMap)) if (v) map[k] = v;
    const body = { column_map: map, rows: parsedRows };
    const btn = document.getElementById("csv-commit");
    btn.disabled = true;
    btn.textContent = "Importing…";
    try {
      const r = await api.accounts.commitImport(account.id, body);
      const parts = [`Imported ${r.inserted}`];
      if (r.skipped_dupes) parts.push(`skipped ${r.skipped_dupes} duplicate${r.skipped_dupes !== 1 ? "s" : ""}`);
      if (r.skipped_invalid) parts.push(`skipped ${r.skipped_invalid} invalid`);
      if (r.auto_matched) parts.push(`auto-matched ${r.auto_matched}`);
      toast(parts.join(" · "));
      closeModal();
      await refreshDetail(account.id);
    } catch (err) {
      btn.disabled = false;
      btn.textContent = "Import";
      toast(err.message, "error");
    }
  });
}

function collectMappingAndRows(headers, dataRows, _parseResult) {
  const columnMap = {};
  document.querySelectorAll(".csv-map-field").forEach(sel => {
    columnMap[sel.dataset.header] = sel.value;
  });
  const idxFor = (canonical) => headers.findIndex(h => columnMap[h] === canonical);
  const dateIdx = idxFor("date");
  const amountIdx = idxFor("amount");
  const descIdx = idxFor("description");
  const refIdx = idxFor("custodian_ref");

  const dateFmt = document.getElementById("csv-date-format")?.value || "auto";
  const negate = document.getElementById("csv-negate")?.value === "yes";
  const descFilter = (document.getElementById("csv-desc-filter")?.value || "").trim().toLowerCase();

  const parsedRows = [];
  const errors = [];

  if (dateIdx < 0 || amountIdx < 0) {
    return { columnMap, parsedRows, errors: ["Map both Date and Amount columns to preview."] };
  }

  for (const row of dataRows) {
    const rawDate = (row[dateIdx] || "").trim();
    const rawAmount = (row[amountIdx] || "").trim();
    const rawDesc = descIdx >= 0 ? (row[descIdx] || "").trim() : "";
    const rawRef = refIdx >= 0 ? (row[refIdx] || "").trim() : "";

    if (!rawDate && !rawAmount) continue; // empty row

    const date = parseDate(rawDate, dateFmt);
    let amount = parseAmount(rawAmount);
    if (date === null || amount === null) {
      errors.push(`row "${rawDate}, ${rawAmount}" — could not parse`);
      continue;
    }
    if (negate) amount = -amount;
    if (amount < 0) amount = -amount; // store as positive (magnitude)

    if (descFilter && !rawDesc.toLowerCase().includes(descFilter)) continue;
    if (Math.abs(amount) < 0.005) continue;

    parsedRows.push({
      date,
      amount: Math.round(amount * 100) / 100,
      description: rawDesc || null,
      custodian_ref: rawRef || null,
    });
  }

  return { columnMap, parsedRows, errors };
}

function updatePreview(account, parseResult, headers, dataRows) {
  const { parsedRows, errors } = collectMappingAndRows(headers, dataRows, parseResult);
  const status = document.getElementById("csv-status");
  const commit = document.getElementById("csv-commit");
  const list = document.getElementById("csv-preview-list");

  if (errors.length && parsedRows.length === 0) {
    list.innerHTML = `<div class="text-muted" style="font-size:13px">${errors.map(esc).join("<br>")}</div>`;
    status.textContent = "";
    commit.disabled = true;
    commit.textContent = "Import";
    return;
  }

  list.innerHTML = parsedRows.length === 0
    ? `<div class="text-muted" style="font-size:13px">No rows match — check the column mapping and filter.</div>`
    : parsedRows.slice(0, 10).map(r => `
        <div class="csv-preview-row">
          <span class="csv-preview-date">${esc(r.date)}</span>
          <span class="csv-preview-amount">${fmt(r.amount)}</span>
          <span class="csv-preview-desc">${esc(r.description || "(no description)")}</span>
          ${r.custodian_ref ? `<span class="csv-preview-ref">${esc(r.custodian_ref)}</span>` : ""}
        </div>
      `).join("");

  const skipped = errors.length;
  status.textContent = parsedRows.length > 0
    ? `${parsedRows.length} row${parsedRows.length !== 1 ? "s" : ""} ready${skipped ? ` · ${skipped} unparseable` : ""}`
    : "";
  commit.disabled = parsedRows.length === 0;
  commit.textContent = parsedRows.length > 0 ? `Import ${parsedRows.length} row${parsedRows.length !== 1 ? "s" : ""}` : "Import";
}

// Returns ISO yyyy-mm-dd or null.
function parseDate(raw, fmt) {
  if (!raw) return null;
  const cleaned = raw.replace(/\s+/g, " ").trim();

  const isoMatch = cleaned.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (isoMatch) return `${isoMatch[1]}-${isoMatch[2]}-${isoMatch[3]}`;

  // Slash- or dash-separated.
  const partsMatch = cleaned.match(/^(\d{1,4})[\/\-](\d{1,2})[\/\-](\d{1,4})/);
  if (!partsMatch) return null;
  let [_, a, b, c] = partsMatch;

  let yyyy, mm, dd;
  if (a.length === 4) {
    yyyy = a; mm = b; dd = c;
  } else if (c.length === 4) {
    yyyy = c;
    if (fmt === "DD/MM/YYYY") { dd = a; mm = b; }
    else if (fmt === "MM/DD/YYYY") { mm = a; dd = b; }
    else {
      // auto — assume MM/DD/YYYY (US default) unless first chunk > 12.
      if (parseInt(a) > 12) { dd = a; mm = b; } else { mm = a; dd = b; }
    }
  } else {
    return null;
  }

  mm = mm.padStart(2, "0");
  dd = dd.padStart(2, "0");
  if (parseInt(mm) < 1 || parseInt(mm) > 12) return null;
  if (parseInt(dd) < 1 || parseInt(dd) > 31) return null;
  return `${yyyy}-${mm}-${dd}`;
}

// Returns a number or null.
function parseAmount(raw) {
  if (raw == null) return null;
  let s = String(raw).trim();
  if (!s) return null;
  let negative = false;
  if (s.startsWith("(") && s.endsWith(")")) { negative = true; s = s.slice(1, -1); }
  s = s.replace(/[$\s]/g, "");
  if (s.startsWith("-")) { negative = true; s = s.slice(1); }
  // Strip thousands separators. Treat last "." or "," as the decimal point.
  if (s.includes(",") && s.includes(".")) {
    if (s.lastIndexOf(",") > s.lastIndexOf(".")) {
      s = s.replace(/\./g, "").replace(",", ".");
    } else {
      s = s.replace(/,/g, "");
    }
  } else if (s.includes(",") && !s.includes(".")) {
    // Could be "1,234" (thousands) or "12,34" (european decimal). Heuristic: if exactly one comma
    // followed by 1-2 digits at the end, treat as decimal; otherwise thousands.
    if (/^\d+,\d{1,2}$/.test(s)) s = s.replace(",", ".");
    else s = s.replace(/,/g, "");
  }
  const n = parseFloat(s);
  if (!Number.isFinite(n)) return null;
  return negative ? -n : n;
}

// ── Styles ────────────────────────────────────────────────────────────────────

function injectStyles() {
  if (document.getElementById("accounts-page-style")) return;
  const s = document.createElement("style");
  s.id = "accounts-page-style";
  s.textContent = `
    .account-card {
      display: flex; align-items: center; gap: 16px;
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 16px 20px; margin-bottom: 10px;
      text-decoration: none; color: var(--text);
      transition: border-color .15s, transform .12s;
    }
    .account-card:hover { border-color: var(--brand-light); transform: translateY(-1px); }
    .account-card-left { flex: 1; min-width: 0; }
    .account-card-name { font-size: 16px; font-weight: 600; display: flex; gap: 8px; align-items: center; }
    .account-card-sub { font-size: 12px; color: var(--muted); margin-top: 3px; }
    .account-card-right { text-align: right; flex-shrink: 0; }
    .account-card-balance { font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; }
    .account-card-balance-sub { font-size: 11px; margin-top: 2px; }

    .badge-amber  { background: var(--yellow); color: #000; margin-left: 6px; }
    .badge-green  { background: var(--green); color: #fff; }
    .badge-muted  { background: var(--surface2); color: var(--muted); border: 1px solid var(--border); }

    .recon-section-header {
      padding: 10px 14px; font-size: 13px; font-weight: 600;
      background: var(--surface2); border-bottom: 1px solid var(--border);
    }
    .recon-section-header.amber { color: var(--yellow); }
    .recon-section-header.green { color: var(--green); }
    .recon-count {
      display: inline-block; min-width: 22px; padding: 1px 8px;
      border-radius: 999px; background: var(--border); color: var(--text);
      font-size: 11px; margin-left: 6px;
    }
    .recon-section-sub { font-weight: 400; color: var(--muted); margin-left: 6px; }
    .recon-list { }
    .recon-row {
      display: flex; align-items: center; gap: 12px;
      padding: 10px 14px; border-bottom: 1px solid var(--border);
    }
    .recon-row:last-child { border-bottom: none; }
    .recon-row-left { flex: 1; min-width: 0; }
    .recon-row-main { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .recon-row-sub  { font-size: 12px; color: var(--muted); margin-top: 2px; }
    .recon-row-actions { display: flex; gap: 6px; flex-shrink: 0; }

    .snapshot-table { width: 100%; }
    .snapshot-table th, .snapshot-table td { padding: 9px 12px; }

    .csv-banner {
      padding: 8px 12px; border-radius: var(--radius);
      background: var(--surface2); font-size: 12px; color: var(--muted);
    }
    .csv-map-grid { display: flex; flex-direction: column; gap: 6px; max-height: 220px; overflow-y: auto; padding: 8px; border: 1px solid var(--border); border-radius: var(--radius); }
    .csv-map-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; align-items: center; }
    .csv-map-header { font-size: 12px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .csv-map-field { font-size: 13px; }

    #csv-preview-list { max-height: 220px; overflow-y: auto; border: 1px solid var(--border); border-radius: var(--radius); }
    .csv-preview-row {
      display: grid; grid-template-columns: 90px 80px 1fr 120px;
      gap: 8px; padding: 6px 10px; font-size: 12px;
      border-bottom: 1px solid var(--border); font-variant-numeric: tabular-nums;
    }
    .csv-preview-row:last-child { border-bottom: none; }
    .csv-preview-amount { font-weight: 600; }
    .csv-preview-desc { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .csv-preview-ref { color: var(--muted); font-family: monospace; }

    @media (max-width: 600px) {
      .csv-preview-row { grid-template-columns: 80px 70px 1fr; }
      .csv-preview-ref { display: none; }
      .recon-row { flex-wrap: wrap; }
    }
  `;
  document.head.appendChild(s);
}
