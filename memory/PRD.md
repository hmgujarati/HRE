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
