import { api } from "../api.js";
import { toast, openModal, closeModal } from "../app.js";

const fmt = n => "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtDate = d => new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

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
    <p class="text-muted" style="margin-bottom:18px;font-size:13px">
      A Pull is a record of an HSA distribution covering one or more receipts. Click into one to see what it covered.
    </p>
    <div id="pulls-list"></div>
  `;

  document.getElementById("new-pull-btn").addEventListener("click", () => showNewPullModal());

  await loadList();
}

async function loadList() {
  const el = document.getElementById("pulls-list");
  el.innerHTML = `<div class="empty-state">Loading…</div>`;

  let pulls;
  try {
    pulls = await api.reimbursements.list();
  } catch (err) {
    el.innerHTML = `<div class="empty-state">Failed to load pulls: ${esc(err.message)}</div>`;
    return;
  }

  if (pulls.length === 0) {
    el.innerHTML = `
      <div class="empty-state">
        No pulls yet.<br>
        Reimburse yourself by pulling expenses out of the vault.
      </div>
    `;
    return;
  }

  el.innerHTML = pulls.map(p => `
    <a class="pull-card" href="#/pulls/${p.id}">
      <div class="pull-card-left">
        <div class="pull-card-date">${fmtDate(p.date)}</div>
        <div class="pull-card-meta">
          ${p.expense_count} expense${p.expense_count !== 1 ? "s" : ""}${p.reference ? ` · ${esc(p.reference)}` : ""}
        </div>
      </div>
      <div class="pull-card-amount">${fmt(p.total_amount)}</div>
    </a>
  `).join("");

  injectStyles();
}

// ── Detail view ───────────────────────────────────────────────────────────────

async function renderDetail(container, pullId) {
  container.innerHTML = `<div class="empty-state">Loading…</div>`;

  let pull;
  try {
    pull = await api.reimbursements.get(pullId);
  } catch (err) {
    container.innerHTML = `
      <div class="section-header"><h2>Pull not found</h2></div>
      <p class="text-muted">${esc(err.message)}</p>
      <p><a href="#/pulls">← Back to all pulls</a></p>
    `;
    return;
  }

  container.innerHTML = `
    <div style="margin-bottom:12px">
      <a href="#/pulls" class="text-muted" style="font-size:13px">← All pulls</a>
    </div>

    <div class="vault-hero">
      <div class="vault-label">Pull · ${fmtDate(pull.date)}</div>
      <div class="vault-amount" style="color:var(--green)">${fmt(pull.total_amount)}</div>
      <div class="vault-sub">
        ${pull.expense_count} expense${pull.expense_count !== 1 ? "s" : ""}
        ${pull.reference ? ` · Ref ${esc(pull.reference)}` : ""}
      </div>
      ${pull.notes ? `<div class="vault-sub" style="margin-top:8px;font-style:italic">${esc(pull.notes)}</div>` : ""}
    </div>

    <div class="section-header">
      <h2>Expenses covered</h2>
    </div>

    <div class="card" style="padding:0;overflow:hidden">
      <div class="table-wrap desktop-only-table">
        <table>
          <thead>
            <tr>
              <th>Merchant</th>
              <th>Date</th>
              <th>Category</th>
              <th class="col-amount">Amount</th>
              <th>Receipts</th>
            </tr>
          </thead>
          <tbody>
            ${pull.expenses.map(e => `
              <tr>
                <td><strong>${esc(e.merchant)}</strong></td>
                <td>${fmtDate(e.date)}</td>
                <td><span class="badge badge-category">${esc(e.category)}</span></td>
                <td class="col-amount">${fmt(e.amount)}</td>
                <td><span class="badge badge-receipt">📎 ${e.receipts.length}</span></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>

    <div class="mobile-only-cards">
      ${pull.expenses.map(e => `
        <div class="expense-card">
          <div class="expense-card-info">
            <div class="expense-card-merchant">${esc(e.merchant)}</div>
            <div class="expense-card-meta">${fmtDate(e.date)} · <span class="badge badge-category" style="font-size:10px">${esc(e.category)}</span></div>
          </div>
          <div class="expense-card-right">
            <span class="expense-card-amount" style="color:var(--text)">${fmt(e.amount)}</span>
            <span class="badge badge-receipt">📎 ${e.receipts.length}</span>
          </div>
        </div>
      `).join("")}
    </div>

    <div style="margin-top:24px;display:flex;justify-content:flex-end">
      <button id="undo-pull-btn" class="btn btn-danger">Undo this pull</button>
    </div>
  `;

  injectStyles();

  document.getElementById("undo-pull-btn").addEventListener("click", async () => {
    const ok = confirm(
      "Undo this pull?\n\n"
      + `${pull.expense_count} expense${pull.expense_count !== 1 ? "s" : ""} `
      + `(${fmt(pull.total_amount)}) will be moved back to the vault as unreimbursed.`
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

// ── New Pull modal ────────────────────────────────────────────────────────────

async function showNewPullModal() {
  let candidates;
  try {
    candidates = await api.expenses.list({ reimbursed: false });
  } catch (err) {
    toast(err.message, "error");
    return;
  }

  if (candidates.length === 0) {
    openModal(`
      <div class="modal-title">New Pull</div>
      <p class="text-muted">There are no unreimbursed expenses in the vault. Add expenses first, then come back here to record a reimbursement.</p>
      <div style="margin-top:16px">
        <a href="#/add" class="btn btn-primary" id="add-from-modal">+ Add Expense</a>
      </div>
    `);
    document.getElementById("add-from-modal").addEventListener("click", closeModal);
    return;
  }

  const today = new Date().toISOString().slice(0, 10);

  openModal(`
    <div class="modal-title">New Pull</div>
    <p class="text-muted" style="font-size:13px;margin-bottom:14px">
      Pick which expenses this HSA distribution covered, then record the date and reference.
    </p>

    <div class="form-group">
      <label>Expenses (${candidates.length} unreimbursed)</label>
      <div id="pull-expense-list" class="pull-picker">
        ${candidates.map(e => `
          <label class="pull-picker-row">
            <input type="checkbox" class="pull-pick" value="${e.id}" data-amount="${e.amount}">
            <div class="pull-picker-meta">
              <div class="pull-picker-merchant">${esc(e.merchant)}</div>
              <div class="pull-picker-sub">${fmtDate(e.date)} · ${esc(e.category)}${e.receipts.length === 0 ? ` · <span style="color:var(--yellow)">no receipt</span>` : ""}</div>
            </div>
            <div class="pull-picker-amount">${fmt(e.amount)}</div>
          </label>
        `).join("")}
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

    <div class="form-group">
      <label>Notes (optional)</label>
      <textarea id="pull-notes" rows="2" placeholder="Anything to remember about this distribution"></textarea>
    </div>

    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:20px;padding-top:14px;border-top:1px solid var(--border)">
      <div>
        <div class="text-muted" style="font-size:11px;text-transform:uppercase;letter-spacing:.05em">Pull total</div>
        <div id="pull-running-total" style="font-size:22px;font-weight:700;color:var(--green)">$0.00</div>
      </div>
      <div style="display:flex;gap:8px">
        <button id="pull-cancel" class="btn btn-secondary">Cancel</button>
        <button id="pull-submit" class="btn btn-primary" disabled>Pull $0.00</button>
      </div>
    </div>
  `);

  injectStyles();

  const totalEl = document.getElementById("pull-running-total");
  const submitBtn = document.getElementById("pull-submit");

  function updateTotal() {
    const checked = [...document.querySelectorAll(".pull-pick:checked")];
    const total = checked.reduce((sum, cb) => sum + parseFloat(cb.dataset.amount), 0);
    totalEl.textContent = fmt(total);
    submitBtn.disabled = checked.length === 0;
    submitBtn.textContent = checked.length === 0 ? "Pull $0.00" : `Pull ${fmt(total)}`;
  }

  document.querySelectorAll(".pull-pick").forEach(cb =>
    cb.addEventListener("change", updateTotal)
  );

  document.getElementById("pull-cancel").addEventListener("click", closeModal);

  submitBtn.addEventListener("click", async () => {
    const ids = [...document.querySelectorAll(".pull-pick:checked")].map(cb => parseInt(cb.value));
    if (ids.length === 0) return;

    const body = {
      date: document.getElementById("pull-date").value,
      reference: document.getElementById("pull-reference").value.trim() || null,
      notes: document.getElementById("pull-notes").value.trim() || null,
      expense_ids: ids,
    };

    submitBtn.disabled = true;
    submitBtn.textContent = "Pulling…";

    try {
      const pull = await api.reimbursements.create(body);
      toast(`Pulled ${fmt(pull.total_amount)} across ${pull.expense_count} expense${pull.expense_count !== 1 ? "s" : ""}`);
      closeModal();
      // Force a re-render on the list page
      if (location.hash === "#/pulls") {
        await loadList();
      } else {
        location.hash = "#/pulls";
      }
    } catch (err) {
      toast(err.message, "error");
      submitBtn.disabled = false;
      updateTotal();
    }
  });
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
      max-height: 300px; overflow-y: auto;
      border: 1px solid var(--border); border-radius: var(--radius);
      background: var(--surface2);
    }
    .pull-picker-row {
      display: flex; align-items: center; gap: 12px;
      padding: 10px 12px; cursor: pointer;
      border-bottom: 1px solid var(--border);
      margin: 0;
    }
    .pull-picker-row:last-child { border-bottom: none; }
    .pull-picker-row:hover { background: var(--surface); }
    .pull-picker-row input[type="checkbox"] { width: auto; margin: 0; flex-shrink: 0; }
    .pull-picker-meta { flex: 1; min-width: 0; }
    .pull-picker-merchant { font-size: 13px; font-weight: 600; }
    .pull-picker-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }
    .pull-picker-amount { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; flex-shrink: 0; }
  `;
  document.head.appendChild(s);
}
