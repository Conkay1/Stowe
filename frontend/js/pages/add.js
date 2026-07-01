import { api } from "../api.js";
import { state, toast } from "../app.js";
import {
  RECEIPT_ACCEPT,
  fileKey,
  formatFileSize,
  installReceiptDropTarget,
  mergeReceiptFiles,
  uploadReceiptFiles,
} from "../receiptFiles.js";

export async function render(container) {
  const today = new Date().toLocaleDateString("en-CA");
  let cats = state.categories;
  if (!cats.length) {
    const rawCats = await api.categories.list();
    cats = rawCats.map(c => c.name || c);
    state.categories = cats;
  }

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
          <label>Receipts (optional)</label>
          <div class="camera-btn-wrap">
            <label class="camera-btn" id="camera-label">
              <span class="camera-btn-icon">📷</span>
              <span id="camera-label-text">Attach Receipts — Photos or Files</span>
              <input type="file" accept="${RECEIPT_ACCEPT}" capture="environment" multiple id="receipt-file" style="display:none">
            </label>
          </div>
          <div id="file-preview" class="selected-file-list mt-8" style="display:none"></div>
          <button type="button" id="clear-file" class="btn btn-secondary btn-sm" style="display:none;margin-top:8px">Remove</button>
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

  const fileInput = document.getElementById("receipt-file");
  const filePreview = document.getElementById("file-preview");
  const dropTarget = document.getElementById("camera-label");
  const labelText = document.getElementById("camera-label-text");
  const clearFileBtn = document.getElementById("clear-file");
  let selectedFiles = [];
  let lastAnalyzedKey = "";

  function receiptLabel() {
    if (selectedFiles.length === 0) return "Attach Receipts — Photos or Files";
    if (selectedFiles.length === 1) return "Add or Change Receipt";
    return `Add More Receipts (${selectedFiles.length} selected)`;
  }

  function renderSelectedFiles() {
    labelText.textContent = receiptLabel();
    clearFileBtn.style.display = selectedFiles.length ? "" : "none";
    clearFileBtn.textContent = selectedFiles.length > 1 ? "Remove All" : "Remove";

    if (!selectedFiles.length) {
      filePreview.style.display = "none";
      filePreview.innerHTML = "";
      return;
    }

    filePreview.style.display = "";
    filePreview.innerHTML = "";
    selectedFiles.forEach((file, index) => {
      const row = document.createElement("div");
      row.className = "selected-file-row";

      const name = document.createElement("span");
      name.className = "selected-file-name";
      name.textContent = file.name;

      const meta = document.createElement("span");
      meta.className = "selected-file-meta";
      meta.textContent = formatFileSize(file.size);

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "selected-file-remove";
      remove.dataset.fileRemove = String(index);
      remove.title = "Remove file";
      remove.textContent = "×";

      row.append(name, meta, remove);
      filePreview.appendChild(row);
    });
  }

  function maybeAnalyzeFirstReceipt() {
    const first = selectedFiles[0];
    if (!first) return;
    const key = fileKey(first);
    if (key === lastAnalyzedKey) return;
    lastAnalyzedKey = key;
    autoFillFromReceipt(first);
  }

  function addReceiptFiles(files) {
    selectedFiles = mergeReceiptFiles(selectedFiles, files);
    renderSelectedFiles();
    maybeAnalyzeFirstReceipt();
  }

  installReceiptDropTarget({
    input: fileInput,
    dropTarget,
    onFiles: addReceiptFiles,
    onReject: files => toast(`${files.length} unsupported file${files.length !== 1 ? "s" : ""} skipped`, "error"),
  });

  filePreview.addEventListener("click", e => {
    const btn = e.target.closest("[data-file-remove]");
    if (!btn) return;
    selectedFiles.splice(parseInt(btn.dataset.fileRemove), 1);
    renderSelectedFiles();
    maybeAnalyzeFirstReceipt();
  });

  // Auto-analyze the attached receipt and pre-fill blank fields. Best-effort:
  // failures are silent, and we never overwrite anything the user already typed.
  async function autoFillFromReceipt(file) {
    const form = document.getElementById("add-form");
    const merchantEl = form.querySelector("input[name='merchant']");
    const dateEl = form.querySelector("input[name='date']");
    const amountEl = form.querySelector("input[name='amount']");
    const categoryEl = form.querySelector("select[name='category']");

    const setFilled = (el, value) => {
      el.value = value;
      el.classList.add("field-autofilled");
      el.addEventListener("input", () => el.classList.remove("field-autofilled"), { once: true });
    };

    labelText.textContent = "Analyzing first receipt…";
    try {
      const fd = new FormData();
      fd.append("file", file);
      const a = await api.receipts.analyze(fd);

      let filledAny = false;
      // Merchant / amount start blank; only fill if still untouched.
      if (a.merchant && !merchantEl.value.trim()) { setFilled(merchantEl, a.merchant); filledAny = true; }
      if (a.amount != null && !amountEl.value.trim()) { setFilled(amountEl, a.amount); filledAny = true; }
      // Date defaults to today; overwrite only if the user hasn't changed that default.
      if (a.date && dateEl.value === today) { setFilled(dateEl, a.date); filledAny = true; }
      // Category select defaults to the first option; suggest only if untouched.
      if (a.category && categoryEl.value === cats[0] && a.category !== cats[0]
          && cats.includes(a.category)) {
        setFilled(categoryEl, a.category); filledAny = true;
      }

      labelText.textContent = receiptLabel();
      if (filledAny) toast("Receipt scanned — review the highlighted fields");
    } catch (err) {
      labelText.textContent = receiptLabel();  // analysis is optional; stay silent
    }
  }

  clearFileBtn.addEventListener("click", () => {
    fileInput.value = "";
    selectedFiles = [];
    lastAnalyzedKey = "";
    renderSelectedFiles();
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

      if (selectedFiles.length) {
        btn.textContent = selectedFiles.length === 1 ? "Uploading receipt…" : `Uploading 0/${selectedFiles.length} receipts…`;
        await uploadReceiptFiles(
          expense.id,
          selectedFiles,
          api.expenses.uploadReceipt,
          (done, total) => { btn.textContent = `Uploading ${done}/${total} receipts…`; },
        );
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
    fileInput.value = "";
    selectedFiles = [];
    lastAnalyzedKey = "";
    renderSelectedFiles();
    document.getElementById("submit-btn").disabled = false;
    document.getElementById("submit-btn").textContent = "Save Expense";
    document.getElementById("merchant-input").focus();
  });
}
