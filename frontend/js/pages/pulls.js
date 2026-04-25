import { api } from "../api.js";
import { toast, openModal, closeModal } from "../app.js";

const fmt = n => "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtDate = d => new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const round2 = n => Math.round(n * 100) / 100;

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
      A Pull records an HSA distribution. Enter the amount your HSA paid out, then back it with receipts (in full or partial slices).
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

  const items = pull.line_items;

  const lineRow = li => {
    const partial = li.covered_amount + 0.005 < li.expense_amount;
    const amountCell = partial
      ? `<span style="color:var(--green);font-weight:600">${fmt(li.covered_amount)}</span><span class="text-muted" style="font-size:12px"> of ${fmt(li.expense_amount)}</span>`
      : `<span style="font-weight:600">${fmt(li.covered_amount)}</span>`;
    return { partial, amountCell };
  };

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

// ── New Pull modal (amount-first allocation) ──────────────────────────────────

async function showNewPullModal() {
  let candidates;
  try {
    // Pull picker shows anything still unreimbursed (includes partials with remaining > 0).
    candidates = await api.expenses.list({ reimbursed: false });
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

    submitBtn.disabled = true;
    submitBtn.textContent = "Pulling…";

    try {
      const pull = await api.reimbursements.create(body);
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
  `;
  document.head.appendChild(s);
}
