# Qist: Installment Management System
Flask + Supabase (PostgreSQL + Storage), glassmorphism UI, deployable on Vercel.
Roles: **Admin (shopkeeper)**, **Verification Officer**, **Customer**.

## 1. Supabase
1. Create a project at supabase.com.
2. SQL Editor -> paste all of `schema.sql` -> Run. (Creates tables, locks them with RLS, creates the private `documents` bucket, adds default plans 3/6/12 months and sample products.)
3. Project Settings -> API: copy the **Project URL** and the **service_role** key.

## 2. Run locally
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # then fill in the 3 values
python seed.py             # creates admin@qist.pk / Admin@12345 and officer@qist.pk / Officer@12345
python app.py              # http://127.0.0.1:5000
```
Change both seeded passwords after the first login (create new accounts under Accounts, disable the old ones).

## 3. Deploy on Vercel
1. Push this folder to a GitHub repository (`.env` is git-ignored).
2. vercel.com -> Add New Project -> import the repo. Framework: Flask is detected from `app.py`.
3. Settings -> Environment Variables: add `SECRET_KEY` (long random text), `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.
4. Deploy. Static files are served from `public/`.

Or with the CLI: `npm i -g vercel && vercel --prod`.

## How the report's rules are implemented
| Rule from the report | Where |
|---|---|
| Verification before any deal | Customer -> Officer (verify) -> Admin (final approve) |
| 20% minimum down payment | `MIN_DOWN_PCT` in `app.py`, enforced server-side and in the calculator |
| 2% late fine on overdue installments | `FINE_PCT`, computed live, frozen when paid |
| Block applying at 3+ overdue installments | `MAX_OVERDUE` |
| Out-of-stock blocks applications | Product stock check on apply and on approval |
| Unique transaction ID + digital receipt | `payments.txn_id`, `/receipt/<id>` (print or save as PDF) |
| Audit log, no accidental deletion | `audit_log` table; foreign keys use `ON DELETE RESTRICT`; accounts are disabled, not deleted |
| Search by name / phone / CNIC | Admin dashboard; Officer CNIC search |
| Recovery, defaulter and profit reports | `/admin/reports` |
| Role separation, hashed passwords, CSRF | `login_required(role)`, Werkzeug hashes, CSRF token on every POST |

## Honest scope notes (mention these in your viva)
- **Online payments** use a confirm-by-admin flow: the customer pays to the shop's JazzCash/Easypaisa/bank account, enters the transaction ID, and the admin confirms it. A live gateway API needs a merchant account; the `payments` table is ready for it.
- **SMS/email reminders** are not wired up (they need a provider such as Twilio/SendGrid). The overdue list in Reports gives the shop the numbers to call.
- Uploads are limited to 2 MB per file because Vercel caps request size at about 4.5 MB.

## Installable app + offline mode (PWA)
- Open the live site in Chrome or Edge, click **Install app** in the top bar (or the install icon in the address bar). Qist then opens in its own window with its own logo, like desktop software.
- **Admin and Officer** can keep working offline. Pages they opened while online (dashboard, Counter, applications, queue) stay available.
- While offline: **Receive cash** (Counter and application page) and the officer's **Approve / Needs info / Reject** are saved on the device and sent automatically when the internet returns. The top bar shows `Offline · N waiting`; click it to see, cancel or sync items.
- Safety: every saved action has a unique ID so it is never applied twice; if the situation changed meanwhile (for example the installment was already paid) the server refuses it and the item is marked Failed with the reason.
- Not available offline: approving a plan (needs live stock check), confirming online payments, uploading documents, opening CNIC/bill images, and creating accounts.
- iPhone: Safari -> Share -> Add to Home Screen.
- After deploying a new version, bump `VERSION` in `public/sw.js` so devices refresh their saved files.
