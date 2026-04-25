import { api } from "../api.js";
import { state, toast, openModal, closeModal } from "../app.js";

const fmt = n => "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtDate = d => new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

let currentFilter = "unreimbursed";
let allExpenses = [];

export async function render(container) {
  container.innerHTML = `
    <div id="vault-hero-wrap"></div>
    <div id="completeness-wrap"></div>
    <div class="section-header">
      <h2>Expenses</h2>
      <div class="spacer"></div>
      <a href="#/add" class="btn btn-primary btn-sm">+ Add</a>
    </div>
    <div class="filter-tabs" id="filter-tabs">
      <button class="filter-tab active" data-filter="unreimbursed">Vault</button>
      <button class="filter-tab" data-filter="all">All</button>
      <button class="filter-tab" data-filter="reimbursed">Reimbursed</button>
    </div>
    <div id="expense-list"></div>
  `;

  document.getElementById("filter-tabs").addEventListener("click", e => {
    const btn = e.target.closest(".filter-tab");
    if (!btn) return;
    document.querySelectorAll(".filter-tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentFilter = btn.dataset.filter;
    renderList();
  });

  await loadAll();
}

async function loadAll() {
  const [summary, expenses] = await Promise.all([
    api.summary(),
    api.expenses.list(),
  ]);

  allExpenses = expenses;
  renderHero(summary);
  renderCompleteness(summary);
  renderList();
}

function renderHero(s) {
  document.getElementById("vault-hero-wrap").innerHTML = `
    <div class="vault-hero">
      <div class="vault-label">Vault Balance</div>
      <div class="vault-amount">${fmt(s.total_unreimbursed)}</div>
      <div class="vault-sub">${s.count_unreimbursed} expense${s.count_unreimbursed !== 1 ? "s" : ""} documented · withdraw any time</div>
      <div class="vault-hero-stats">
        <div>
          <div class="vault-stat-label">Lifetime Eligible</div>
          <div class="vault-stat-value">${fmt(s.total_unreimbursed + s.total_reimbursed)}</div>
        </div>
        <div>
          <div class="vault-stat-label">Already Reimbursed</div>
          <div class="vault-stat-value" style="color:var(--green)">${fmt(s.total_reimbursed)}</div>
        </div>
        <div>
          <div class="vault-stat-label">Expenses</div>
          <div class="vault-stat-value">${s.count_unreimbursed + s.count_reimbursed}</div>
        </div>
      </div>
    </div>
  `;
}

function renderCompleteness(s) {
  const pct = s.receipt_completeness_pct;
  const colorClass = pct >= 80 ? "green" : pct >= 50 ? "yellow" : "red";
  document.getElementById("completeness-wrap").innerHTML = `
    <div class="completeness-bar-wrap">
      <span class="completeness-label">Receipt coverage</span>
      <div class="completeness-track">
        <div class="completeness-fill ${colorClass}" style="width:${pct}%"></div>
      </div>
      <span class="completeness-pct" style="color:var(--${colorClass})">${pct}%</span>
    </div>
  `;
}

function renderList() {
  const filtered = allExpenses.filter(e => {
    if (currentFilter === "unreimbursed") return !e.reimbursed;
    if (currentFilter === "reimbursed")   return e.reimbursed;
    return true;
  });

  const el = document.getElementById("expense-list");

  if (filtered.length === 0) {
    el.innerHTML = `<div class="empty-state">${
      currentFilter === "unreimbursed"
        ? "No unreimbursed expenses yet.<br><a href=\"#/add\">Add your first expense →</a>"
        : "No expenses found."
    }</div>`;
    return;
  }

  // Table for desktop, cards for mobile — use CSS classes + responsive layout
  el.innerHTML = `
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
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody id="expense-tbody"></tbody>
        </table>
      </div>
    </div>
    <div id="expense-cards" class="mobile-only-cards"></div>
  `;

  // Add responsive CSS if not already present
  if (!document.getElementById("responsive-table-style")) {
    const s = document.createElement("style");
    s.id = "responsive-table-style";
    s.textContent = `
      @media (max-width: 767px) { .desktop-only-table { display: none; } }
      @media (min-width: 768px) { .mobile-only-cards { display: none; } }
    `;
    document.head.appendChild(s);
  }

  const tbody = document.getElementById("expense-tbody");
  const cards = document.getElementById("expense-cards");

  filtered.forEach(e => {
    const covered = e.covered_amount ?? 0;
    const remaining = e.remaining_amount ?? e.amount;
    const partial = !e.reimbursed && covered > 0.005;

    const statusBadge = e.reimbursed
      ? `<span class="badge badge-reimbursed">Reimbursed</span>`
      : `<span class="badge badge-unreimbursed">In Vault</span>`;
    const receiptBadge = `<span class="badge badge-receipt" data-manage="${e.id}">📎 ${e.receipts.length}</span>`;
    const partialNote = partial
      ? `<div class="expense-partial-note">Partially pulled — ${fmt(remaining)} remaining of ${fmt(e.amount)}</div>`
      : "";

    // Table row
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>
        <strong>${esc(e.merchant)}</strong>
        ${partialNote}
      </td>
      <td>${fmtDate(e.date)}</td>
      <td><span class="badge badge-category">${esc(e.category)}</span></td>
      <td class="col-amount" style="color:var(--red)">${fmt(e.amount)}</td>
      <td>${receiptBadge}</td>
      <td>${statusBadge}</td>
      <td><button class="btn btn-secondary btn-sm" data-manage="${e.id}">Manage</button></td>
    `;
    tbody.appendChild(tr);

    // Card
    const card = document.createElement("div");
    card.className = "expense-card";
    card.innerHTML = `
      <div class="expense-card-info">
        <div class="expense-card-merchant">${esc(e.merchant)}</div>
        <div class="expense-card-meta">${fmtDate(e.date)} · <span class="badge badge-category" style="font-size:10px">${esc(e.category)}</span></div>
        ${partialNote}
      </div>
      <div class="expense-card-right">
        <span class="expense-card-amount">${fmt(e.amount)}</span>
        <div style="display:flex;gap:6px;align-items:center">
          ${receiptBadge}
          ${statusBadge}
        </div>
        <button class="btn btn-secondary btn-sm" data-manage="${e.id}">Manage</button>
      </div>
    `;
    cards.appendChild(card);
  });

  // Inject partial-note style once.
  if (!document.getElementById("vault-partial-style")) {
    const s = document.createElement("style");
    s.id = "vault-partial-style";
    s.textContent = `
      .expense-partial-note {
        font-size: 11px; color: var(--green); margin-top: 3px; font-weight: 500;
      }
    `;
    document.head.appendChild(s);
  }

  // Event delegation for manage buttons and receipt badges
  [tbody, cards].forEach(container => {
    container.addEventListener("click", e => {
      const id = e.target.closest("[data-manage]")?.dataset.manage;
      if (id) showManageModal(parseInt(id));
    });
  });
}

async function showManageModal(id) {
  const expense = await api.expenses.get(id);
  const cats = state.categories;

  openModal(`
    <div class="modal-title">Manage Expense</div>
    <form id="manage-form">
      <div class="form-row">
        <div class="form-group">
          <label>Merchant</label>
          <input name="merchant" value="${esc(expense.merchant)}" required>
        </div>
        <div class="form-group">
          <label>Date</label>
          <input type="date" name="date" value="${esc(expense.date)}" required>
        </div>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Amount ($)</label>
          <input type="number" step="0.01" name="amount" value="${esc(String(expense.amount))}" required>
        </div>
        <div class="form-group">
          <label>Category</label>
          <select name="category">
            ${cats.map(c => `<option ${c === expense.category ? "selected" : ""}>${esc(c)}</option>`).join("")}
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>Notes</label>
        <textarea name="notes" rows="2">${esc(expense.notes || "")}</textarea>
      </div>
      ${(expense.covered_amount ?? 0) > 0.005 ? `
        <div class="form-group">
          <label>Reimbursement</label>
          <div class="pull-coverage-summary">
            <span class="badge badge-reimbursed">${expense.reimbursed ? "Fully covered" : "Partially covered"}</span>
            <span style="font-size:13px">${fmt(expense.covered_amount)} of ${fmt(expense.amount)} pulled across ${expense.pull_count} pull${expense.pull_count !== 1 ? "s" : ""}</span>
          </div>
          <p class="text-muted" style="font-size:12px;margin-top:6px">
            <a href="#/pulls">View pulls →</a> &nbsp;·&nbsp; To unmark, undo the relevant pull(s).
          </p>
        </div>
      ` : `
        <div class="form-group">
          <label style="display:flex;align-items:center;gap:8px;flex-direction:row">
            <input type="checkbox" name="reimbursed" id="reimb-check" style="width:auto" ${expense.reimbursed ? "checked" : ""}>
            <span>Mark as reimbursed (without a pull)</span>
          </label>
        </div>
        <div class="form-group" id="reimb-date-wrap" style="${expense.reimbursed ? "" : "display:none"}">
          <label>Reimbursement date</label>
          <input type="date" name="reimbursed_date" value="${expense.reimbursed_date || new Date().toISOString().slice(0,10)}">
        </div>
      `}
      <div style="display:flex;gap:8px;margin-bottom:20px">
        <button type="submit" class="btn btn-primary">Save Changes</button>
        <button type="button" id="delete-btn" class="btn btn-danger">Delete</button>
      </div>
    </form>
    <hr class="divider">
    <div class="modal-title" style="margin-bottom:8px">Receipts (${expense.receipts.length})</div>
    <div class="receipt-grid" id="receipt-grid">${renderReceiptGrid(expense.receipts)}</div>
    <div class="camera-btn-wrap">
      <label class="camera-btn">
        <span class="camera-btn-icon">📷</span>
        <span>Add Receipt — Photo or File</span>
        <input type="file" accept="image/*,application/pdf" capture="environment" id="receipt-upload" style="display:none">
      </label>
    </div>
  `);

  // Toggle reimbursement date — only present when the expense isn't part of a pull
  const reimbCheck = document.getElementById("reimb-check");
  if (reimbCheck) {
    reimbCheck.addEventListener("change", e => {
      document.getElementById("reimb-date-wrap").style.display = e.target.checked ? "" : "none";
    });
  }

  // Save form
  document.getElementById("manage-form").addEventListener("submit", async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {
      merchant: fd.get("merchant"),
      date: fd.get("date"),
      amount: parseFloat(fd.get("amount")),
      category: fd.get("category"),
      notes: fd.get("notes") || null,
    };
    // Reimbursement toggle is only editable when there's no pull-backed coverage.
    if ((expense.covered_amount ?? 0) <= 0.005) {
      body.reimbursed = fd.get("reimbursed") === "on";
      if (body.reimbursed && fd.get("reimbursed_date")) {
        body.reimbursed_date = fd.get("reimbursed_date");
      }
    }
    try {
      await api.expenses.update(id, body);
      toast("Expense updated");
      closeModal();
      await loadAll();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  // Delete
  document.getElementById("delete-btn").addEventListener("click", async () => {
    if (!confirm("Delete this expense and all its receipts?")) return;
    try {
      await api.expenses.remove(id);
      toast("Expense deleted");
      closeModal();
      await loadAll();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  // Receipt upload
  document.getElementById("receipt-upload").addEventListener("change", async e => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      await api.expenses.uploadReceipt(id, fd);
      toast("Receipt added");
      const updated = await api.expenses.get(id);
      document.getElementById("receipt-grid").innerHTML = renderReceiptGrid(updated.receipts);
      attachReceiptDeleteListeners(id);
    } catch (err) {
      toast(err.message, "error");
    }
  });

  attachReceiptDeleteListeners(id);
}

function renderReceiptGrid(receipts) {
  if (receipts.length === 0) return `<span class="text-muted" style="font-size:13px">No receipts yet</span>`;
  return receipts.map(r => {
    const url = api.receipts.fileUrl(r.id);
    const isImage = r.file_type && r.file_type.startsWith("image/");
    const thumb = isImage
      ? `<img src="${url}" alt="${r.original_filename}">`
      : `<span class="pdf-icon">📄</span>`;
    return `
      <div class="receipt-item">
        <a href="${url}" target="_blank" class="receipt-thumb">${thumb}</a>
        <button class="receipt-del" data-receipt-del="${r.id}" title="Delete receipt">×</button>
      </div>
    `;
  }).join("");
}

function attachReceiptDeleteListeners(expenseId) {
  document.getElementById("receipt-grid")?.querySelectorAll("[data-receipt-del]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const rid = parseInt(btn.dataset.receiptDel);
      if (!confirm("Remove this receipt?")) return;
      try {
        await api.receipts.remove(rid);
        const updated = await api.expenses.get(expenseId);
        document.getElementById("receipt-grid").innerHTML = renderReceiptGrid(updated.receipts);
        attachReceiptDeleteListeners(expenseId);
        await loadAll();
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });
}
