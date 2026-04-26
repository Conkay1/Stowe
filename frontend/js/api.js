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
    list:   ()         => get("/reimbursements"),
    get:    (id)       => get(`/reimbursements/${id}`),
    create: (body)     => post("/reimbursements", body),
    update: (id, body) => put(`/reimbursements/${id}`, body),
    remove: (id)       => del(`/reimbursements/${id}`),
  },
  accounts: {
    list:            ()           => get("/accounts"),
    get:             (id)         => get(`/accounts/${id}`),
    create:          (body)       => post("/accounts", body),
    update:          (id, body)   => put(`/accounts/${id}`, body),
    deactivate:      (id)         => put(`/accounts/${id}/deactivate`),
    remove:          (id)         => del(`/accounts/${id}`),
    parseCsv:        (id, fd)     => post(`/accounts/${id}/imports/parse`, fd, true),
    commitImport:    (id, body)   => post(`/accounts/${id}/imports/commit`, body),
    distributions:   (id, status) => get(`/accounts/${id}/distributions`, status ? { status } : null),
    reconciliation:  (id)         => get(`/accounts/${id}/reconciliation`),
    autoReconcile:   (id)         => post(`/accounts/${id}/reconcile/auto`),
    snapshots: {
      list:   (id)            => get(`/accounts/${id}/snapshots`),
      create: (id, body)      => post(`/accounts/${id}/snapshots`, body),
      update: (snapId, body)  => put(`/snapshots/${snapId}`, body),
      remove: (snapId)        => del(`/snapshots/${snapId}`),
    },
  },
  distributions: {
    match:   (id, body) => post(`/distributions/${id}/match`, body),
    unmatch: (id)       => del(`/distributions/${id}/match`),
    remove:  (id)       => del(`/distributions/${id}`),
  },
  summary:     () => get("/summary"),
  annualLedger: () => get("/annual-ledger"),
  csvUrl:    (year) => year ? `${BASE}/export/csv?year=${year}` : `${BASE}/export/csv`,
  backupUrl: ()     => `${BASE}/export/zip`,
};
