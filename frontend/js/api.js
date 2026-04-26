const BASE = "/api/v1";

async function req(method, path, body, isForm = false) {
  const opts = { method, headers: {} };
  if (body) {
    if (isForm) {
      opts.body = body;
    } else {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
  }
  const res = await fetch(BASE + path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  if (res.status === 204) return null;
  return res.json();
}

const get = (path, params) => {
  const url = params ? path + "?" + new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v !== null && v !== undefined))
  ) : path;
  return req("GET", url);
};
const post = (path, body, isForm) => req("POST", path, body, isForm);
const put  = (path, body) => req("PUT", path, body);
const del  = (path) => req("DELETE", path);

export const api = {
  categories: {
    list:   () => get("/categories"),
    create: (body) => post("/categories", body),
    remove: (id)   => del(`/categories/${id}`),
  },
  expenses: {
    list:          (params)       => get("/expenses", params),
    get:           (id)           => get(`/expenses/${id}`),
    create:        (body)         => post("/expenses", body),
    update:        (id, body)     => put(`/expenses/${id}`, body),
    remove:        (id)           => del(`/expenses/${id}`),
    uploadReceipt: (id, formData) => post(`/expenses/${id}/receipts`, formData, true),
  },
  receipts: {
    remove:  (id) => del(`/receipts/${id}`),
    fileUrl: (id) => `${BASE}/receipts/${id}/file`,
  },
  reimbursements: {
    list:   ()       => get("/reimbursements"),
    get:    (id)     => get(`/reimbursements/${id}`),
    create: (body)   => post("/reimbursements", body),
    remove: (id)     => del(`/reimbursements/${id}`),
  },
  summary:     () => get("/summary"),
  annualLedger: () => get("/annual-ledger"),
  csvUrl:    (year) => year ? `${BASE}/export/csv?year=${year}` : `${BASE}/export/csv`,
  backupUrl: ()     => `${BASE}/export/zip`,
};
