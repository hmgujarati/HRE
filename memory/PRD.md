# HRE Exporter CRM — PRD

## Original problem statement
Build Phase 1 of CRM + WhatsApp quotation system for HRE Exporter (ISO 9001 cable lug/terminal manufacturer). Phase 1 modules only: login, branded admin dashboard, material management, category management, product family/catalogue management, product variant/pricing chart, image+dimension drawing storage, price history, bulk discount update. Quotations / WhatsApp Bot / Expo Leads → "Coming Soon" only.

## Architecture
- Backend: FastAPI (single `server.py`), MongoDB via Motor, JWT (PyJWT), bcrypt, local `uploads/` static mount
- Frontend: React 19, react-router-dom 7, Tailwind, Phosphor icons, sonner toasts, shadcn-style components overridden to sharp/rounded-none
- Auth: JWT bearer (Authorization header), token in localStorage
- IDs: UUID strings (no ObjectId leakage)
- Currency: INR ₹

## User personas
- **Admin** — full CRUD on all entities, bulk discount, delete
- **Manager** — view + edit product/pricing data, image uploads, bulk discount
- **Employee** — read-only on catalogue/pricing

## WhatsApp + SMTP Settings Module — implemented (2026-04-30)
- New collection `settings` (singleton doc `id: "integrations"`) holds WhatsApp (BizChatAPI) and SMTP (Hostinger) configuration
- Endpoints (admin/manager): `GET /api/settings/integrations`, `PUT /api/settings/integrations`, `POST /api/settings/whatsapp/test`, `POST /api/settings/smtp/test`
- Secrets are **masked on read** (`tes•••890` style) and **preserved on update** when the field is sent empty/null
- New Settings UI is **tabbed** (`WhatsApp`, `Email (SMTP)`, `Account`, `Branding`); admin can fill Vendor UID, token, OTP template name, language, default country code, from-phone-number-id; "Send Test" panels for both
- `POST /api/public/quote-requests/{rid}/send-otp` now reads DB settings → if WhatsApp is `enabled` + token + template → calls BizChatAPI `send-template-message` (passes OTP as `field_1` and `button_0` for COPY_CODE templates); else dev fallback (logs + returns `dev_otp`). Response `delivery: "whatsapp"|"dev"` for client telemetry.
- BizChatAPI integration: `httpx.AsyncClient` POST to `{base}/{vendor_uid}/contact/send-template-message?token=...` with phone normalised to `{country_code}{10-digits}`

## Public Portal Wave A — implemented (2026-04-29)
- `/catalogue` public page (hero + materials filter chips + grid) — fully mobile responsive (chip row scrolls, hero scales, build-quote CTA stacks)
- `/catalogue/:id` Family Detail with **Smart Variant Finder**:
  - Cable size + Hole size inputs; numeric/range parser handles `"4-6 mm²"`, `"1.5"`, `"5 mm"` etc.
  - Top-5 closest matches by numeric range distance (0 if user input falls inside a range)
  - Hidden by default; "Show all" toggle reveals full table on demand (mobile = card list, desktop = table)
- `/request-quote` cart + business details + mock OTP + priced review (mobile: card review, stacked subtotal); cart cards stack on mobile
- `/my-quotes` past quotes via stored token
- Backend `POST /api/public/quote-requests/start | /send-otp | /verify-otp | /finalise` — OTP currently DEV (returned in `dev_otp`); WhatsApp+SMTP wiring pending user keys

## Phase 2A — implemented (2026-04-29)
- **Contacts (CRM)** module: full CRUD + smart upsert by phone/email (last-10-digit normalisation), regex-safe search, source filter (manual/expo/quotation/whatsapp), per-contact quote history, sidebar nav link
- **Quotations** module:
  - Auto-numbered `HRE/QT/{FY}/{NNNN}` (Indian fiscal year Apr–Mar, MongoDB-counter backed)
  - Embedded line_items with per-line and aggregate computation (subtotal/discount/taxable/GST/grand_total)
  - Statuses: draft → sent → approved/rejected/revised/expired with timestamps
  - **Revise** endpoint clones into v2 draft, marks source as revised, strips prior `-R{n}` suffix (no chains)
  - Quote builder UI with ContactPicker (with quick-add) + VariantSearchPicker + sticky totals + notes + terms
  - Quote view with branded printable layout, Bill To / Ship To, signature lines, **Print → PDF** via browser
  - Pipeline & Won value cards on quotations list + quote-stats endpoint
- Sidebar reorganised: CRM section now (Dashboard, Quotations, Contacts, Pricing Chart, Product Families, Materials, Categories, Products/Variants, Price History). Coming soon: WhatsApp Bot, Order Tracking, Expo Leads.

## Phase 1 — implemented (2026-04-27)
- JWT auth (login, /me, logout) with seeded admin `admin@hrexporter.com` / `Admin@123`
- Materials CRUD (Copper + Aluminium seeded)
- Nested Categories CRUD (Sheet Metal Lug → Ring/Pin/Fork/U; Tubular Lug → Copper Lug/Inline Connectors; Aluminium top-level)
- Product Families CRUD with technical fields (material/specification/finish/insulation colour coding/standard reference) + image uploads (main, dimension drawing, catalogue reference)
- Product Variants CRUD with **dynamic JSON dimensions**, base price + discount % + manual override → computed final price
- Price History recorded on create / update / bulk discount (full audit trail)
- Bulk Discount endpoints: by material / category / product-family + preview-count endpoint
- Dashboard stats: families, variants, active count, categories, per-material counts, recent families, recent price changes
- 3 seeded product families with 9 variants (RI-7048/49/53, RII-7057/58/59, PT-1/2/9)
- Branded UI: dark sidebar #1A1A1A, brand yellow #FBAE17, Chivo + Inter + JetBrains Mono fonts, sharp geometric (rounded-none), industrial catalogue tables

## Backlog / Phase 2 (P0)
- Quotation builder (multi-line variant picker, customer details, discount per line)
- PDF generation (catalogue style with images + dimension drawing)
- WhatsApp chatbot integration (product reply with image)
- CRM leads
- Expo lead capture

## Phase 2 (P1)
- Logo upload via Settings (replace text wordmark with HRE logo file)
- Multi-currency support (USD, INR toggle)
- CSV import/export of variants
- Object storage migration (S3) — storage layer is already modular

## Phase 2 (P2)
- Brute-force lockout on /api/auth/login
- Decimal-based price math (currently float, susceptible to paise rounding)
- Referential integrity on DELETE for material/category/family
- Image upload MIME/magic-byte validation + cleanup of replaced files
- FastAPI lifespan (replace deprecated on_event)

## Test Status
- Backend: 28/28 tests passing (`pytest backend/tests/test_hre_crm_backend.py`)
- Frontend: smoke tested — login + dashboard rendering verified
