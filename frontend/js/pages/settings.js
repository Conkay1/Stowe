import { api } from "../api.js";
import { state, toast } from "../app.js";

export async function render(container) {
  await renderView(container);
}

async function renderView(container) {
  try {
    const cats = await api.categories.list();
    // Update global state with the name strings
    state.categories = cats.map(c => c.name);

    // Split into defaults and custom
    const defaults = cats.filter(c => c.is_default);
    const custom = cats.filter(c => !c.is_default);

    container.innerHTML = `
      <div class="section-header">
        <h2>Settings</h2>
      </div>
      
      <div class="card">
        <h3>Categories</h3>
        <p class="text-muted" style="margin-bottom: 16px; font-size: 13px;">
          Preset categories are always available. Add your own custom categories below.
        </p>
        
        <div style="margin-bottom: 20px;">
          <div style="font-size: 12px; text-transform: uppercase; color: var(--muted); margin-bottom: 8px; font-weight: 600;">Preset</div>
          <ul style="padding: 0; list-style: none; margin: 0;">
            ${defaults.map(c => `
              <li style="display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--border);">
                <span>${c.name}</span>
                <span class="text-muted" style="font-size: 11px;">Default</span>
              </li>
            `).join('')}
          </ul>
        </div>

        <div style="margin-bottom: 20px;">
          <div style="font-size: 12px; text-transform: uppercase; color: var(--muted); margin-bottom: 8px; font-weight: 600;">Custom</div>
          <ul style="padding: 0; list-style: none; margin: 0;" id="custom-cat-list">
            ${custom.length === 0 ? `
              <li class="text-muted" style="padding: 10px 0; font-size: 13px; font-style: italic;">No custom categories yet.</li>
            ` : custom.map(c => `
              <li style="display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--border);">
                <span>${c.name}</span>
                <button class="btn btn-sm btn-secondary delete-cat" data-id="${c.id}" style="padding: 4px 8px; font-size: 12px; color: var(--danger);">
                  Delete
                </button>
              </li>
            `).join('')}
          </ul>
        </div>

        <form id="add-cat-form" style="display: flex; gap: 8px;">
          <input type="text" name="name" placeholder="New category name" required style="flex: 1;">
          <button type="submit" class="btn btn-primary" id="add-cat-btn">Add</button>
        </form>
      </div>
    `;

    document.getElementById("add-cat-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = document.getElementById("add-cat-btn");
      btn.disabled = true;
      const name = new FormData(e.target).get("name").trim();
      
      try {
        await api.categories.create({ name });
        toast("Category added");
        await renderView(container);
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });

    document.querySelectorAll(".delete-cat").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        if (!confirm("Delete this category? Any existing expenses will be moved to 'Other'.")) return;
        const id = e.currentTarget.dataset.id;
        try {
          e.currentTarget.disabled = true;
          await api.categories.remove(id);
          toast("Category deleted");
          await renderView(container);
        } catch (err) {
          toast(err.message, "error");
          e.currentTarget.disabled = false;
        }
      });
    });

  } catch (err) {
    container.innerHTML = `<div class="empty-state">Failed to load settings: ${err.message}</div>`;
  }
}
