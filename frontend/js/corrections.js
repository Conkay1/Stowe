import { api } from "./api.js";
import { openModal, closeModal, toast } from "./app.js";

const pendingCorrections = new Map();
const esc = value => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const money = value => Number(value).toLocaleString("en-US", { style: "currency", currency: "USD" });

export const hasPendingCorrection = id => pendingCorrections.has(id);

function operationId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function showCorrectionModal(expense, onSaved) {
  let pending = pendingCorrections.get(expense.id) || null;
  let version = 0;
  let busy = false;
  openModal(`
    <h2 class="modal-title">Correct expense cost</h2>
    <p class="text-muted">${esc(expense.merchant)} · ${esc(expense.date)}</p>
    <dl class="correction-box correction-totals">
      <dt>Recorded cost</dt><dd id="correction-recorded-cost">${money(expense.amount)}</dd>
      <dt>Already covered by pulls</dt><dd id="correction-recorded-coverage">${money(expense.covered_amount)}</dd>
    </dl>
    <form id="correction-form">
      <div class="form-group">
        <label for="correction-amount">Corrected cost ($)</label>
        <input id="correction-amount" type="number" min="0" max="999999999999.99" step="0.01" value="${esc(pending?.body.amount ?? expense.amount)}" required>
      </div>
      <div class="form-group">
        <label for="correction-reason">Reason for correction</label>
        <textarea id="correction-reason" rows="3" maxlength="2000" required>${esc(pending?.body.reason || "")}</textarea>
      </div>
      <button type="submit" id="correction-preview-btn" class="btn btn-secondary">Preview correction</button>
    </form>
    <div id="correction-preview" aria-live="polite"></div>
    <p id="correction-error" class="correction-error" role="alert"></p>
    <p class="text-muted correction-help">The original receipts and reimbursement records stay attached. A dated correction history records the reason and both amounts.</p>
    <div class="correction-actions">
      <button type="button" id="correction-save-btn" class="btn btn-primary" disabled>Save correction</button>
      <button type="button" id="correction-cancel-btn" class="btn btn-secondary">Cancel</button>
    </div>
  `);
  const form = document.getElementById("correction-form");
  const amount = document.getElementById("correction-amount");
  const reason = document.getElementById("correction-reason");
  const previewButton = document.getElementById("correction-preview-btn");
  const saveButton = document.getElementById("correction-save-btn");
  const preview = document.getElementById("correction-preview");
  const error = document.getElementById("correction-error");

  function lockFields(locked) {
    amount.disabled = locked;
    reason.disabled = locked;
    previewButton.disabled = locked;
  }

  function renderPreview() {
    const p = pending.preview;
    document.getElementById("correction-recorded-cost").textContent = money(p.before_amount);
    document.getElementById("correction-recorded-coverage").textContent = money(p.covered_amount);
    preview.innerHTML = `
      <section class="correction-box ${p.reconciliation_required ? "correction-warning" : ""}">
        <h3>Review before saving</h3>
        <dl class="correction-totals">
          <dt>Corrected cost</dt><dd>${money(p.after_amount)}</dd>
          <dt>Recorded coverage</dt><dd>${money(p.covered_amount)}</dd>
          <dt>Available for another pull</dt><dd>${money(p.remaining_amount)}</dd>
        </dl>
        ${p.overcovered_amount > 0 ? `<strong class="correction-excess">${money(p.overcovered_amount)} needs reconciliation</strong>` : ""}
        <p>Saving corrects the cost. It does not return or reassign money already withdrawn.</p>
      </section>`;
    saveButton.disabled = false;
  }

  if (pending) {
    lockFields(true);
    renderPreview();
    saveButton.textContent = "Retry same correction";
    error.textContent = "A previous save was not confirmed. Retry the same correction to verify its result.";
  }

  form.addEventListener("input", () => {
    version += 1;
    pending = null;
    preview.innerHTML = "";
    error.textContent = "";
    saveButton.disabled = true;
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || pendingCorrections.has(expense.id)) return;
    const generation = ++version;
    pending = null;
    saveButton.disabled = true;
    previewButton.disabled = true;
    preview.innerHTML = "";
    error.textContent = "";
    const body = { amount: amount.value, reason: reason.value.trim() };
    try {
      const result = await api.expenses.previewCorrection(expense.id, body);
      if (!form.isConnected || generation !== version) return;
      pending = { preview: result, body: { ...body, expected_revision: result.revision, operation_id: operationId() } };
      renderPreview();
    } catch (err) {
      if (form.isConnected && generation === version) error.textContent = err.message;
    } finally {
      previewButton.disabled = false;
    }
  });

  saveButton.addEventListener("click", async () => {
    if (!pending || busy) return;
    busy = true;
    lockFields(true);
    saveButton.disabled = true;
    error.textContent = "";
    pendingCorrections.set(expense.id, pending);
    try {
      await api.expenses.correct(expense.id, pending.body);
    } catch (err) {
      busy = false;
      if (err.status && err.status < 500 && err.status !== 408) {
        pendingCorrections.delete(expense.id);
        pending = null;
        lockFields(false);
        preview.innerHTML = "";
        error.textContent = err.message;
      } else {
        error.textContent = "The save result could not be confirmed. Retry the same correction; it will not be applied twice.";
        saveButton.textContent = "Retry same correction";
        saveButton.disabled = false;
      }
      return;
    }
    pendingCorrections.delete(expense.id);
    const reopen = form.isConnected;
    if (reopen) closeModal();
    toast("Correction saved; review any remaining difference");
    try {
      await onSaved(reopen);
    } catch {
      toast("Correction saved. Refresh to view the updated records.", "error");
    }
  });
  document.getElementById("correction-cancel-btn").addEventListener("click", closeModal);
}
