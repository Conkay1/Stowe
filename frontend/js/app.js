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

// ── Router ─────────────────────────────────────────────────────
const PAGES = {
  vault:  () => import("./pages/vault.js"),
  add:    () => import("./pages/add.js"),
  ledger: () => import("./pages/ledger.js"),
};

async function route() {
  const hash = location.hash.slice(2) || "vault";
  const page = hash.split("?")[0].split("/")[0];

  document.querySelectorAll("[data-page]").forEach(el =>
    el.classList.toggle("active", el.dataset.page === page)
  );

  const loader = PAGES[page] || PAGES.vault;
  const appEl = document.getElementById("app");
  appEl.innerHTML = "";
  try {
    const mod = await loader();
    await mod.render(appEl);
  } catch (err) {
    appEl.innerHTML = `<div class="empty-state">Failed to load page: ${err.message}</div>`;
  }
}

// ── Init ───────────────────────────────────────────────────────
(async () => {
  state.categories = await api.categories.list().catch(() => []);
  window.addEventListener("hashchange", route);
  route();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/static/sw.js").catch(() => {});
  }
})();
