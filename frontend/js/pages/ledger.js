import { api } from "../api.js";

const fmt = n => "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export async function render(container) {
  container.innerHTML = `
    <div class="section-header" style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
      <h2 style="margin:0">Annual Ledger</h2>
      <a id="export-all-csv" href="${api.csvUrl()}" class="btn btn-secondary" download>Export CSV</a>
    </div>
    <div id="ledger-content"><div class="empty-state">Loading…</div></div>
  `;

  try {
    const [years, summary] = await Promise.all([api.annualLedger(), api.summary()]);
    renderLedger(years, summary);
  } catch (err) {
    document.getElementById("ledger-content").innerHTML =
      `<div class="empty-state">Failed to load: ${err.message}</div>`;
  }
}

function renderLedger(years, summary) {
  const el = document.getElementById("ledger-content");

  if (years.length === 0) {
    el.innerHTML = `<div class="empty-state">No expenses yet.<br><a href="#/add">Add your first expense →</a></div>`;
    return;
  }

  const totalAll = summary.total_unreimbursed + summary.total_reimbursed;

  el.innerHTML = `
    <div class="card mb-16" style="margin-bottom:16px">
      <div class="vault-label">All-Time Totals</div>
      <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:12px">
        <div>
          <div class="ledger-stat-label">Total Eligible</div>
          <div class="ledger-stat-val">${fmt(totalAll)}</div>
        </div>
        <div>
          <div class="ledger-stat-label">Vault Balance</div>
          <div class="ledger-stat-val yellow">${fmt(summary.total_unreimbursed)}</div>
        </div>
        <div>
          <div class="ledger-stat-label">Reimbursed</div>
          <div class="ledger-stat-val green">${fmt(summary.total_reimbursed)}</div>
        </div>
        <div>
          <div class="ledger-stat-label">Total Expenses</div>
          <div class="ledger-stat-val">${summary.count_unreimbursed + summary.count_reimbursed}</div>
        </div>
      </div>
    </div>
    ${years.map(y => yearCard(y)).join("")}
  `;
}

function yearCard(y) {
  const pct = y.receipt_completeness_pct;
  const barColor = pct >= 80 ? "var(--green)" : pct >= 50 ? "var(--yellow)" : "var(--red)";
  const vaultClass = y.total_unreimbursed > 0 ? "yellow" : "green";

  return `
    <div class="ledger-year-card">
      <div class="ledger-year-header">
        <span class="ledger-year">${y.year}</span>
        <span class="ledger-count">${y.count} expense${y.count !== 1 ? "s" : ""}</span>
        <span class="ledger-vault-amount ${vaultClass}" title="Unreimbursed balance">
          ${fmt(y.total_unreimbursed)} in vault
        </span>
        <a href="${api.csvUrl(y.year)}" class="btn btn-secondary btn-sm" download title="Download ${y.year} as CSV">CSV</a>
      </div>
      <div class="ledger-stats">
        <div>
          <div class="ledger-stat-label">Total Eligible</div>
          <div class="ledger-stat-val">${fmt(y.total_amount)}</div>
        </div>
        <div>
          <div class="ledger-stat-label">Reimbursed</div>
          <div class="ledger-stat-val green">${fmt(y.total_reimbursed)}</div>
        </div>
        <div>
          <div class="ledger-stat-label">Unreimbursed</div>
          <div class="ledger-stat-val yellow">${fmt(y.total_unreimbursed)}</div>
        </div>
        <div>
          <div class="ledger-stat-label">Receipt Coverage</div>
          <div class="ledger-stat-val" style="color:${barColor}">${pct}%</div>
        </div>
      </div>
      <div class="ledger-bar-wrap">
        <div class="ledger-bar" style="width:${pct}%;background:${barColor}"></div>
      </div>
      <div style="font-size:11px;color:var(--muted);margin-top:4px">
        Receipt coverage of vault expenses
      </div>
    </div>
  `;
}
