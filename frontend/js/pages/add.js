import { api } from "../api.js";
import { state, toast } from "../app.js";

export async function render(container) {
  const today = new Date().toISOString().slice(0, 10);
  const cats = state.categories.length ? state.categories : await api.categories.list();

  container.innerHTML = `
    <div class="section-header">
      <h2>Log HSA Expense</h2>
    </div>
    <div class="card">
      <form id="add-form">
        <div class="form-group">
          <label>Merchant / Provider</label>
          <input id="merchant-input" name="merchant" placeholder="e.g. CVS Pharmacy" required autofocus>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Date</label>
            <input type="date" name="date" value="${today}" required>
          </div>
          <div class="form-group">
            <label>Amount ($)</label>
            <input type="number" step="0.01" min="0.01" name="amount" placeholder="0.00" required>
          </div>
        </div>
        <div class="form-group">
          <label>Category</label>
          <select name="category">
            ${cats.map(c => `<option>${c}</option>`).join("")}
          </select>
        </div>
        <div class="form-group">
          <label>Notes (optional)</label>
          <textarea name="notes" rows="2" placeholder="Prescription, copay, etc."></textarea>
        </div>
        <hr class="divider">
        <div class="form-group">
          <label>Receipt (optional)</label>
          <div class="camera-btn-wrap">
            <label class="camera-btn" id="camera-label">
              <span class="camera-btn-icon">📷</span>
              <span id="camera-label-text">Attach Receipt — Photo or File</span>
              <input type="file" accept="image/*,application/pdf" capture="environment" id="receipt-file" style="display:none">
            </label>
          </div>
          <div id="file-preview" class="mt-8" style="display:none">
            <span id="file-name" style="font-size:13px;color:var(--accent)"></span>
            <button type="button" id="clear-file" class="btn btn-secondary btn-sm" style="margin-left:8px">Remove</button>
          </div>
        </div>
        <button type="submit" id="submit-btn" class="btn btn-primary btn-full" style="margin-top:8px;padding:12px">
          Save Expense
        </button>
      </form>
      <div id="success-msg" class="hidden" style="text-align:center;padding:32px">
        <div style="font-size:40px;margin-bottom:12px">✅</div>
        <div style="font-size:18px;font-weight:600;margin-bottom:8px">Expense Logged!</div>
        <div class="text-muted" style="margin-bottom:20px">Saved to your vault.</div>
        <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
          <button id="add-another" class="btn btn-secondary">Add Another</button>
          <a href="#/vault" class="btn btn-primary">View Vault</a>
        </div>
      </div>
    </div>
  `;

  // File selection display
  const fileInput = document.getElementById("receipt-file");
  const filePreview = document.getElementById("file-preview");
  const fileName = document.getElementById("file-name");
  const labelText = document.getElementById("camera-label-text");
  const clearFileBtn = document.getElementById("clear-file");

  fileInput.addEventListener("change", () => {
    const f = fileInput.files[0];
    if (f) {
      fileName.textContent = f.name;
      filePreview.style.display = "";
      labelText.textContent = "Change Receipt";
    } else {
      filePreview.style.display = "none";
      labelText.textContent = "Attach Receipt — Photo or File";
    }
  });

  clearFileBtn.addEventListener("click", () => {
    fileInput.value = "";
    filePreview.style.display = "none";
    labelText.textContent = "Attach Receipt — Photo or File";
  });

  // Form submit
  document.getElementById("add-form").addEventListener("submit", async e => {
    e.preventDefault();
    const btn = document.getElementById("submit-btn");
    btn.disabled = true;
    btn.textContent = "Saving…";

    const fd = new FormData(e.target);
    const body = {
      merchant: fd.get("merchant"),
      date: fd.get("date"),
      amount: parseFloat(fd.get("amount")),
      category: fd.get("category"),
      notes: fd.get("notes") || null,
    };

    try {
      const expense = await api.expenses.create(body);

      const file = fileInput.files[0];
      if (file) {
        const receiptFd = new FormData();
        receiptFd.append("file", file);
        await api.expenses.uploadReceipt(expense.id, receiptFd);
      }

      document.getElementById("add-form").classList.add("hidden");
      document.getElementById("success-msg").classList.remove("hidden");
      toast("Expense logged!");
    } catch (err) {
      toast(err.message, "error");
      btn.disabled = false;
      btn.textContent = "Save Expense";
    }
  });

  // Add another — reset form
  document.getElementById("add-another")?.addEventListener("click", () => {
    document.getElementById("success-msg").classList.add("hidden");
    const form = document.getElementById("add-form");
    form.classList.remove("hidden");
    form.reset();
    form.querySelector("input[name='date']").value = new Date().toISOString().slice(0, 10);
    filePreview.style.display = "none";
    labelText.textContent = "Attach Receipt — Photo or File";
    document.getElementById("submit-btn").disabled = false;
    document.getElementById("submit-btn").textContent = "Save Expense";
    document.getElementById("merchant-input").focus();
  });
}
