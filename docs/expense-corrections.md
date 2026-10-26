# Audited expense corrections

A later insurance adjustment can change an expense's documented cost after an HSA withdrawal. Changing that cost must not rewrite the withdrawal or silently make previously covered money available again.

The correction API changes only the expense cost and its derived reimbursement state. It preserves pull allocations, custodian distributions, receipt records, and receipt bytes. Every correction stores its reason and before/after financial snapshots in the local database. A correction does not resolve an excess withdrawal, establish tax eligibility, or reallocate coverage to another expense.

## Preview and apply

In the Vault, open **Manage → Correct cost…**, enter the supported cost and reason, and choose **Preview correction**. Review the cost, existing coverage, remaining amount, and any excess before choosing **Save correction**. Editing either input invalidates the preview. A stale preview must be refreshed; an uncertain save offers **Retry same correction** with the original operation ID. Correction history appears in Manage Expense.

The account reconciliation view shows linked records with differences in **Needs reconciliation**, separate from **Amounts agree**. Expense and pull views also show excess coverage. These warnings remain until the underlying evidence is reconciled.

1. Preserve a current database and receipt backup and verify the supporting evidence.
2. Send `POST /api/v1/expenses/{expense_id}/corrections/preview` with `amount` and a nonempty `reason`. The amount must be nonnegative and have at most two decimal places; zero supports a fully adjusted cost.
3. Review the returned before/after cost, recorded coverage, remaining amount, and excess coverage. A one-cent difference remains visible.
4. Send the same amount and reason to `POST /api/v1/expenses/{expense_id}/corrections`, adding the preview's `revision` as `expected_revision` and a new UUID as `operation_id`.
5. Read back the expense, its receipts, linked pulls and distributions, and `GET /api/v1/expenses/{expense_id}/corrections`.

If the expense or its reimbursement evidence changed after preview, applying returns HTTP 409 without writing. Preview again before retrying. A network failure after submission can be retried with the exact same operation ID and payload: an already committed correction returns the original audit instead of applying twice. Reusing an operation ID for different content is rejected.

Expense cost and audit storage share one SQLite transaction. A storage failure rolls both back. Financial writes acquire the SQLite write lock before checking amounts, so concurrent pulls cannot validate against an old cost. Reversing an incorrect correction requires another reasoned correction; the earlier audit remains.

## Reconciliation and limits

- Ordinary amount edits are rejected for expenses with reimbursement history or prior corrections. Metadata-only edits remain supported.
- Manually marked reimbursements without verified pull coverage cannot be corrected through this endpoint: the actual amount withdrawn is unknown. Reconcile that evidence first.
- Expenses with correction history cannot be deleted, and pulls backing corrected expenses cannot be undone. These guards preserve the audit and prevent covered money from returning to the vault through deletion.
- Expense responses expose `overcovered_amount` and `reconciliation_required`; the summary exposes `total_overcovered`.
- Linked account records expose withdrawal-minus-pull `amount_difference`, pull-minus-allocation `allocation_difference`, and `overcovered_expense_ids`. Excess is measured across all pulls for each affected expense; it is not attributed to a particular withdrawal when several pulls cover the same expense.
- The existing `matched` collection still means the records are linked. A linked item with `reconciliation_required: true` is not reconciled.

The audit table is additive and created on startup. Existing expense, reimbursement, distribution, and receipt records are not rewritten by schema initialization. The audit is included in a database backup; the ordinary ledger CSV is not an audit export.

Financial API reads bypass browser caching. The service worker caches only local static assets and removes prior Stowe caches that could contain medical responses. If the API is unavailable, the app reports the failure instead of presenting cached balances as current.

## Regression checks

Run Python tests with the development requirements installed. Browser checks are development-only; Node and Playwright are not required by the packaged app.

```sh
python -m pytest -q
npm ci
npx playwright install chromium
npm run test:service-worker
npm run test:ui
```

Set `STOWE_TEST_PYTHON` to the Python interpreter with the development requirements if it is not `python3`. `STOWE_TEST_BROWSER` can select an existing compatible Chromium executable. Browser tests start a loopback server on a temporary port with a fresh synthetic database and receipts directory. They retain synthetic screenshots and logs in the printed temporary directory and stop their server and browser when finished.
