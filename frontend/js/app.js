import { api } from "./api.js";

export const state = { categories: [] };

// ── Toast ──────────────────────────────────────────────────────
const toastEl = document.getElementById("toast");
let toastTimer;
export function toast(msg, type = "success") {
  toastEl.textContent = msg;
  toastEl.className = `toast ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.add("hidden"), 3000);
}

// ── Modal ──────────────────────────────────────────────────────
const overlay = document.getElementById("modal-overlay");
const modalContent = document.getElementById("modal-content");

export function openModal(html) {
  modalContent.innerHTML = html;
  overlay.classList.remove("hidden");
}
export function closeModal() {
  overlay.classList.add("hidden");
  modalContent.innerHTML = "";
}

document.getElementById("modal-close").addEventListener("click", closeModal);
overlay.addEventListener("click", e => { if (e.target === overlay) closeModal(); });

// ── Data migration notice ─────────────────────────────────────
// Shown once per launch when the Stowe data copy failed and the app is
// running on the old Stowe folder.
(async function showMigrationNotice() {
  try {
    const info = await api.system.getDataDir();
    if (!info || !info.migration_notice) return;
    if (sessionStorage.getItem("wellslip-migration-notice") === info.migration_notice) return;
    sessionStorage.setItem("wellslip-migration-notice", info.migration_notice);
    toast(info.migration_notice, "error");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.add("hidden"), 15000);
  } catch (e) {}
})();

// ── Router ─────────────────────────────────────────────────────
const PAGES = {
  vault:    () => import("./pages/vault.js"),
  add:      () => import("./pages/add.js"),
  pulls:    () => import("./pages/pulls.js"),
  ledger:   () => import("./pages/ledger.js"),
  accounts: () => import("./pages/accounts.js"),
  settings: () => import("./pages/settings.js"),
};

async function route() {
  const hash = location.hash.slice(2) || "vault";
  const segments = hash.split("?")[0].split("/").filter(Boolean);
  const page = segments[0] || "vault";
  const params = segments.slice(1);

  document.querySelectorAll("[data-page]").forEach(el =>
    el.classList.toggle("active", el.dataset.page === page)
  );

  const loader = PAGES[page] || PAGES.vault;
  const appEl = document.getElementById("app");
  appEl.innerHTML = "";
  try {
    const mod = await loader();
    await mod.render(appEl, params);
  } catch (err) {
    appEl.innerHTML = `<div class="empty-state">Failed to load page: ${err.message}</div>`;
  }
}

// ── Init ───────────────────────────────────────────────────────
(async () => {
  const rawCats = await api.categories.list().catch(() => []);
  state.categories = rawCats.map(c => c.name || c);
  window.addEventListener("hashchange", route);
  route();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/static/sw.js").catch(() => {});
  }
})();
