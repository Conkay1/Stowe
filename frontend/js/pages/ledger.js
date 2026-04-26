import { api } from "../api.js";

const fmt = n => "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export async function render(container) {
  container.innerHTML = `
    <div class="section-header" style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
      <h2 style="margin:0">Annual Ledger</h2>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <a href="${api.backupUrl()}" class="btn btn-secondary" download>Download Backup</a>
        <a id="export-all-csv" href="${api.csvUrl()}" class="btn btn-secondary" download>Export CSV</a>
      </div>
    </div>
    <div id="ledger-content"><div class="empty-state">Loading…</div></div>
  `;

  try {
    const [years, summary, expenses] = await Promise.all([
      api.annualLedger(), 
      api.summary(),
      api.expenses.list()
    ]);
    renderLedger(years, summary, expenses);
  } catch (err) {
    document.getElementById("ledger-content").innerHTML =
      `<div class="empty-state">Failed to load: ${err.message}</div>`;
  }
}

function renderLedger(years, summary, expenses) {
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

    <div style="display:flex; gap:16px; margin-bottom: 24px; flex-wrap: wrap;">
      <div class="card" style="flex:1; min-width:300px; padding: 16px;">
        <h3 style="margin-top:0; margin-bottom:16px; font-size: 14px; color: var(--muted); text-transform: uppercase;">Category Breakdown</h3>
        <div style="position: relative; height: 250px;">
          <canvas id="categoryChart"></canvas>
        </div>
      </div>
      <div class="card" style="flex:1; min-width:300px; padding: 16px;">
        <h3 style="margin-top:0; margin-bottom:16px; font-size: 14px; color: var(--muted); text-transform: uppercase;">Spending Trends</h3>
        <div style="position: relative; height: 250px;">
          <canvas id="trendChart"></canvas>
        </div>
      </div>
    </div>

    ${years.map(y => yearCard(y)).join("")}
  `;

  setTimeout(() => renderCharts(expenses), 0);
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

function renderCharts(expenses) {
  if (expenses.length === 0 || !window.Chart) return;

  const catTotals = {};
  expenses.forEach(e => {
    catTotals[e.category] = (catTotals[e.category] || 0) + e.amount;
  });
  
  const catLabels = Object.keys(catTotals).sort((a,b) => catTotals[b] - catTotals[a]);
  const catData = catLabels.map(l => catTotals[l]);
  const bgColors = [
    '#185FA5', '#2E86AB', '#3F88C5', '#F49D37', '#D72638', 
    '#1B998B', '#F2A65A', '#772E25', '#C44536', '#4C5C68'
  ];

  new Chart(document.getElementById('categoryChart'), {
    type: 'doughnut',
    data: {
      labels: catLabels,
      datasets: [{
        data: catData,
        backgroundColor: bgColors.slice(0, catLabels.length),
        borderWidth: 0
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'right', labels: { boxWidth: 12, font: { family: 'system-ui' } } },
        tooltip: { callbacks: { label: (ctx) => ' ' + ctx.label + ': ' + fmt(ctx.raw) } }
      }
    }
  });

  const monthTotals = {};
  expenses.forEach(e => {
    const month = e.date.substring(0, 7);
    monthTotals[month] = (monthTotals[month] || 0) + e.amount;
  });

  const allMonths = Object.keys(monthTotals).sort();
  const trendLabels = allMonths.slice(-12);
  const trendData = trendLabels.map(m => monthTotals[m]);

  new Chart(document.getElementById('trendChart'), {
    type: 'bar',
    data: {
      labels: trendLabels.map(m => {
        const [y, mo] = m.split('-');
        const date = new Date(y, parseInt(mo)-1);
        return date.toLocaleDateString('default', { month: 'short', year: '2-digit' });
      }),
      datasets: [{
        label: 'Expenses',
        data: trendData,
        backgroundColor: '#185FA5',
        borderRadius: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => ' ' + fmt(ctx.raw) } }
      },
      scales: {
        y: { beginAtZero: true, grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { callback: v => '$'+v } },
        x: { grid: { display: false } }
      }
    }
  });
}
