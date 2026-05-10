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

## Phase 2C — Order Tracking (implemented 2026-04-30)
- New `orders` collection with full lifecycle: pending_po → po_received → proforma_issued → order_placed → raw_material_check (branch: procuring) → in_production → packaging → dispatched → lr_received → delivered
- Convert any approved/sent quote into an order — snapshots line items, contact, totals, place_of_supply
- Auto-numbered: orders `HRE/ORD/2026-27/NNNN`, proforma `HRE/PI/2026-27/NNNN`
- Server-side **Proforma Invoice PDF** generator (reuses quote_pdf.py with `doc_title="PROFORMA INVOICE"` + 50% advance terms)
- Document uploads (PO, PI, Invoice, E-way Bill, LR copy) saved under `/uploads/orders/{oid}/` with timestamped filenames
- **Free-form production updates** appended chronologically to a per-order log
- **Auto WhatsApp notifications** at every milestone (PI Issued, In Production, Packaging, Dispatched, LR Received) — settings tab now has 5 dedicated template selectors
- **Full audit trail** (timeline) of every event with timestamps + user
- Frontend: `/orders` table list with stage filter + search; `/orders/:id` detail page with contact card, contextual stage actions, production note input, document sidebar, notification log, full timeline
- "Convert to Order" button on QuotationView (visible when status=approved/sent)

## Real-time Delivery Webhooks + Email Read Tracking — implemented (2026-04-30)
- BizChat webhook receiver at `POST /api/webhooks/bizchat/status?secret=...` parses 7 payload shapes (flat, `{data}`, `{statuses[]}`, Meta envelope, BizChat native `{message:{whatsapp_message_id, status}}`, nested `whatsapp_webhook_payload`, wrapped `payload`). Auto-generated webhook secret surfaced in Settings UI with Copy + Rotate buttons. GET health-check for BizChat's "Verify Webhook" step.
- Status-rank guard on both webhook + polled updates so late-arriving `delivered` never overwrites `read`
- Webhook event log persisted to `db.webhook_events` + admin-only read endpoint for debugging
- Email "Read" via 1×1 tracking pixel: dispatch now generates `open_token` per-email, ships an HTML-styled body with `<img src="/api/webhooks/email/open?t=...">`; endpoint upgrades `sent`→`read` when client loads the pixel (supports GET+HEAD for image-prefetch clients)
- Email body upgraded from plain text to multipart/alternative (plain + branded HTML) with gold accent, grand total block, and confidentiality footer

## Delivery Status Tracking & Dispatch Log — implemented (2026-04-30)
- Each dispatch now persists a `dispatch_log` entry on the quotation doc: `{id, channel, template, to, wamid, log_uid, pdf_file, pdf_url, sent_at, status, status_updated_at?, error?}`
- WhatsApp response body's `data.wamid` / `data.log_uid` / `data.status` captured at send time
- New endpoint `POST /api/quotations/{qid}/refresh-delivery` polls BizChatAPI `contact/message-status?wamid=...` for each non-terminal WA entry and updates `status` / `status_updated_at`
- Frontend `<DeliveryStrip>` + `<DeliveryPill>` components show channel-aware status chips (Queued → Sent → Delivered → Read, with Failed variant)
- Quotations list table: new `Delivery` column showing the latest status-per-channel strip
- QuotationView: `DispatchLogPanel` with a "Refresh Status" button + a reverse-chronological timeline of every dispatch attempt

## Quote PDF Dispatch (WhatsApp + Email) — implemented (2026-04-30)
- New module `/app/backend/quote_pdf.py`: server-side PDF generator using **Jinja2 + WeasyPrint**, replicates the on-screen `QuotationView.jsx` layout with full outer frame (top/right/bottom/left borders all enclosed)
- Each dispatch writes a **timestamped** PDF (`{quote_no}_{YYYYMMDDHHMMSS}.pdf`) so WhatsApp/Meta media cache never serves a stale copy; admin preview uses stable filename
- New endpoints:
  - `POST /api/quotations/{qid}/send` — admin/manager: render PDF + dispatch via WA template (`send-media-message` style with `header_document`) + email via SMTP
  - `GET /api/quotations/{qid}/pdf` — admin: download/preview PDF
  - `GET /api/settings/whatsapp/templates` — proxies BizChatAPI `template-list`, normalises shape
- Settings `whatsapp.quote_template_name` + `quote_template_language` now stored; admin can pick from 34 approved templates loaded via "Load templates" button (auto-fills `template_name` dropdown + matching language dropdown)
- `QuotationView.jsx` now has a **Send to Customer** button (green WhatsApp accent) that triggers dispatch and toasts the channel results (`WhatsApp ✓ + Email ✓` etc.)
- `_dispatch_finalised_quote` is also called automatically when a customer finalises a public quote, so self-service quotes auto-arrive on WhatsApp + Email
- Required env: `PUBLIC_BASE_URL` (added to backend/.env) — used as the `media_url` host for `header_document`

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

## WhatsApp Chatbot v2 — Material → Family → Cable → Hole → Qty → Proforma (2026-05-10)
- **Bug fix #1**: Bot was using wrong field names (`name`/`code`/`price`/`family_id`) — schema is `family_name`/`product_code`/`final_price`/`product_family_id`. On the customer's WhatsApp, family lists rendered as "Family / Tap to select" (empty fallback) and variants as "Variant / ₹0/unit". Fixed by aligning schema reads in `_send_family_list`, `_send_variant_*` and `_bot_finalize_quote`.
- **Bug fix #2 (live)**: After fix #1, the user reported that tapping a family on real WhatsApp made the bot reply "Please tap one of the families…". Root cause: BizChat's LIVE inbound envelope nests the `interactive.list_reply.id` (and `button_reply.id`) inside `whatsapp_webhook_payload.entry[].changes[].value.messages[].interactive.*` — the top-level `message.body` only carries the visible row TITLE, not the row id. The old parser only checked `message.interactive`, missed the nested path, returned `selection_id=""`, and the state machine fell through to "expecting_family". Fixed `parse_inbound` to walk the Meta-style nested envelope first; backwards-compatible with the simple `data.message.interactive.*` shape. Verified by replaying the user's actual stuck `webhook_events` payload — bot now correctly transitions PICK_FAMILY → ASK_CABLE.
- **Flow rewrite** per user request:
  1. WELCOME → main menu buttons
  2. Returning customer skips name/email/company; new customers complete it
  3. **PICK_MATERIAL** — dynamic buttons from `materials.material_name` (Copper / Aluminium)
  4. **PICK_FAMILY** — list filtered by `material_id`, shows `short_name||family_name` + `product_type||family_name`
  5. **ASK_CABLE** — strict numeric guard: rejects non-numeric input
  6. **ASK_HOLE** — numeric or `skip`/`no`/`none`/`-`/etc.
  7. **PICK_VARIANT** — Top 5 closest matches via numeric range distance (re-uses the public smart-match algorithm; lives in `whatsapp_bot.parse_size_range` + `range_distance`)
  8. **ASK_QTY** — numeric guard + MOQ check; sends a friendly "minimum X units" message when below MOQ
  9. **AFTER_ITEM** — buttons: Add another / Review cart / Cancel. *Add another loops back to PICK_MATERIAL* (per user choice 1b)
  10. **REVIEW_CART** — full itemised summary with totals; buttons: Confirm & Send / Cancel
  11. **CONFIRM** → calls `_bot_finalize_quote` which: builds Quotation (status=sent) → mints Order (stage=pending_po → proforma_issued) → generates **Proforma Invoice PDF** (`HRE/PI/{FY}/{NNNN}`) → fires `_order_auto_notify("proforma_issued")` so customer receives the PI on WhatsApp + Email
- New states: `pick_material`, `pick_family`, `ask_cable`, `ask_hole`, `after_item`, `review_cart`. Old `browse_family`/`add_more` are retired.
- Material choices are persisted in `ctx.material_choices` so button reply id "1"/"2" can be resolved back to the chosen material id (BizChat returns the index, not the title).
- Numeric input validation: `parse_first_number(text)` extracts the first number; reject if `None` or `<= 0`. Hole-size accepts `skip`-like words.
- `_bot_finalize_quote` now produces the proper-shape `QuoteIn` line items (`product_variant_id`, `product_code`, `family_name`, `cable_size`, `hole_size`, `hsn_code`, `quantity`, `base_price`, `gst_percentage`) so `_compute_quote_totals` and the WeasyPrint PDF render correctly with full GST math.
- Tests: `tests/test_whatsapp_bot_flow.py` — 3 tests passing (size parser, handoff keyword, full e2e flow with quote+order+proforma). Locks down the schema-name regression so future schema drift fails loud.


- New module `/app/backend/whatsapp_bot.py` with state machine + outbound send helpers (text / button / list)
- Endpoint `POST /api/webhooks/bizchat/inbound` receives BizChat customer-message webhook payloads
- Permissive parser handles standard Meta shapes (text, button_reply, list_reply) — every raw payload logged to `webhook_events` collection for debugging
- 8-state conversation: WELCOME → ASK_NAME → ASK_EMAIL → ASK_COMPANY → BROWSE_FAMILY → PICK_VARIANT → ASK_QTY → ADD_MORE → CONFIRM → FINALIZED
- Persists to `chatbot_sessions` collection with full transcript + 30-min idle TTL
- New customers auto-saved to `contacts` collection with `source: whatsapp_bot`
- Returning customers auto-detected (tolerant phone match — handles 91-prefix variations)
- Quote finalization calls `_bot_finalize_quote` which builds line items with `unit_price` from variants table, generates PDF via WeasyPrint, dispatches via existing `_dispatch_finalised_quote` (so customer gets PDF on WA + Email + sees it in `/my-quotes`)
- Handoff keywords (sales/human/agent/complaint/refund/urgent) at any state → bot sends admin's phone (`whatsapp.admin_notify_phone`) and ends session
- "About HRE" reply → link to https://hrexporter.com/about-hr-exporter/
- All outbound sends wrapped in `_safe_send` so transient BizChat errors don't break state machine
- BizChat list rows require `description` field to be non-empty — defaulted to "Tap to select"
- Live-tested with simulated webhooks: button_reply correctly routed, list_reply correctly routed, contact lookup tolerant, sessions persisted, transcript recorded

## ETA Nudge + Email Retry Queue (2026-05-10)
- **ETA Nudge** on Orders list: amber banner counts in-flight orders missing an Expected Completion Date; new "ETA" column shows the date in green, or a clickable amber "Set ETA" link for in-flight rows that don't have one. Whole row tints amber for missing-ETA orders. In-flight = stage > pending_po and < delivered.
- **Email Retry Queue** with exponential backoff (30s → 2m → 10m, max 3 attempts):
  - Background asyncio worker started on app startup; ticks every 30s
  - When `_order_auto_notify` or `_notify_production_update` SMTP send fails inline, the email is captured into `email_retry_queue` collection with `next_retry_at` set 30s out
  - Worker re-attempts the send; on success flips the original notification's `email_status: sent` and clears `email_error`. On exhaustion, marks `email_retry_exhausted: true` and persists the final error.
  - Each notification entry now carries a unique `id` (uuid) so the worker can target the exact row via `notifications.id`. New helper `_persist_order_notification` centralizes the push + retry-enqueue logic across all 9 notification call sites.
  - Live-tested: forced a stub retry → worker picked it up within 30s, sent successfully, flipped `email_status: sent`, `email_retry_attempt: 2`.

## Order Notification Read-Receipts (2026-05-10)
- Each order email notification now embeds a 1×1 tracking pixel; `_order_auto_notify` and `_notify_production_update` mint an `email_open_token` per send and persist it on the notification entry alongside `email_status="sent"`.
- `/api/webhooks/email/open` extended to also lookup `orders.notifications` by `email_open_token`. On hit, flips `email_status: sent → read` with `email_status_updated_at` timestamp.
- `/api/webhooks/bizchat/status` extended to also lookup `orders.notifications` by `wamid`. Status hierarchy: pending → accepted → sent → delivered → read (failed terminal). Only upgrades, never downgrades.
- OrderView "Customer pings" panel rebuilt: per-channel pills now show full status (sent / delivered / read / failed) with timestamps; failed entries display the error inline. Re-fire badge for re-sent entries. **Live-tested**: pixel hit on a fresh re-fire flipped `email_status` from `sent` → `read` and the UI rendered `EMAIL · READ @ 6:12:22 AM`.

## Re-fire Notifications + Auto Language Sync (2026-05-10)
- New endpoint `POST /api/settings/whatsapp/sync-template-languages` queries BizChat's template list and auto-overwrites every stale `*_template_language` field with the actual approved language. Triggered automatically when admin clicks "Load Templates" in Settings UI. Live-tested: 4 stage template languages (`order_pi/packaging/dispatched/lr_template_language`) auto-flipped from `en` → `en_US`, fixing the live "Template language not found" failures.
- New endpoint `POST /api/orders/{oid}/refire-notification` re-fires the most recent stage or production-update notification (WhatsApp + Email) for an order without advancing the stage. Stamps `refire_of` on the new entry for audit. Useful when a previous send failed or customer asks for a re-send. Live-tested on HRE/ORD/2026-27/0003 after language sync — both channels delivered (`whatsapp: True, email: True`).
- OrderView "Auto Notifications" panel rebuilt: now shows BOTH WA + Email status pills per row (sent/failed/—), kind label (stage vs floor update), production note quote, error details inline, and a "Re-fire last" button at the top of the panel.

## Expected Completion Date + Doc Guards on Stage Advance (2026-05-10)
- New field `expected_completion_date` on orders (YYYY-MM-DD). New endpoint `PUT /api/orders/{oid}/expected-completion`. Frontend: editable card on OrderView with Set/Change/Clear actions.
- Notifications enriched: when ETA is set, both WhatsApp `{{4}}` (Updated:) and Email body now show `· Expected completion: 25-Jun-2026`. Email also has a black/yellow ETA badge below the timestamp. No Meta template re-approval needed — fits inside the existing `{{4}}` slot.
- New `STAGE_REQUIRED_DOCS` map enforces required docs on `/orders/{oid}/advance` (proforma_issued → PI; dispatched → Tax Invoice + E-way Bill; lr_received → LR Copy). Returns 400 with friendly message: "Cannot move to {stage} — missing required document(s): X, Y."
- `/orders/{oid}/upload-dispatch` also enforces both Tax Invoice + E-way Bill before allowing the dispatched transition. Prevents the silent advance the user reported.
- Public `_public_order_summary` now also exposes `expected_completion_date` so it can be shown on `/my-quotes` tracking strip.

## Bug fixes — stage notifications + production updates (2026-05-10)
- **Email not sending fix**: `_order_auto_notify` was calling `_send_email_sync` which doesn't exist; the actual function is `_send_smtp_email`. Renamed all 3 references. Confirmed with live test: production update on `HRE/ORD/2026-27/0003` now returns `email: True` (sent to hmgujarati@gmail.com).
- **Duplicate "in" fix**: Approved Meta templates often hardcode "is now in {{3}}" in the body. Passing `STAGE_TO_LABEL["in_production"]="In Production"` produced "is now in In Production". Added `STAGE_TEMPLATE_LABEL` map that strips redundant connectors (in_production → "Production"). All other stages use the same label.
- **Production-update notifications**: `POST /api/orders/{oid}/production-update` now fires email (always, when SMTP enabled) + WhatsApp (when `order_production_update_template` is configured). Email uses a branded HTML body with the note as a blockquote. New template fields in Settings: `order_production_update_template` + `_language`.

## Per-stage Template Languages + Email Stage-Notify + Tax Invoice Auto-Gen + Stronger Confirm (2026-05-10)
- Each Phase 2C auto-notify template now has its own language field (`order_pi_template_language`, `order_production_template_language`, `order_packaging_template_language`, `order_dispatched_template_language`, `order_lr_template_language`). Same for `po_received_admin_template_language`. Settings UI auto-fills the language when admin selects a template name. Fixes "Template for the selected language not found" Meta error when individual templates are approved in different languages (e.g. quote in `en_US` but production in `en`).
- `_order_auto_notify` rewritten: now sends BOTH WhatsApp + Email in parallel for every notify stage. Email includes branded HTML body + actual files attached (Tax Invoice, E-way Bill, LR Copy, PI). On `dispatched` stage, WhatsApp sends the template with Tax Invoice as document header, then a follow-up `send-media-message` carries the E-way Bill so customer receives BOTH attachments.
- New endpoint `POST /api/orders/{oid}/invoice/generate` auto-creates a Tax Invoice PDF using `quote_pdf.py` with `doc_title="TAX INVOICE"`. New invoice numbering counter `HRE/INV/{FY}/{NNNN}`. Frontend exposes "Auto-generate Tax Invoice PDF" button at the `packaging` stage.
- Strengthened stage-advance confirmation prompt: "Are you sure you want to move this order forward to '{stage}'? This will trigger automatic WhatsApp + Email notifications and cannot be undone."

## Dual-channel OTP (WhatsApp + Email) — implemented + hardened (2026-05-06)
- New shared helpers `_send_otp_whatsapp` / `_send_otp_email` / `_otp_delivery_label`; both fire in parallel for the **same OTP code**
- Email OTP uses Hostinger SMTP (multipart text + branded HTML, 60-min validity badge, gold accent)
- Wired into both flows: `/public/quote-requests/{rid}/send-otp` (request-quote) and `/public/my-quotes/login/start` (login lookups email by phone_norm)
- Response: `delivery: "whatsapp+email" | "whatsapp" | "email" | "dev"` + masked `email_hint`
- Frontend MyQuotes shows "We've sent a code to your WhatsApp (…) and email (ha•••••@…)"
- **Hardening (2026-05-06):** `_send_whatsapp_template` now requires `data.wamid` or `data.log_uid` in BizChat's response body. BizChatAPI returns HTTP 200 even for invalid vendor_uid/token combos, but without a wamid — previously this falsely reported `delivery=whatsapp`. Now correctly falls back to other channel or dev.
- Tested: 11/11 Phase 2E + 58/58 full regression — all passing

## Customer-side PO Submission — implemented (2026-05-06)
- New public endpoint `POST /api/public/quote/{qid}/submit-po` (multipart: token, instructions, optional file)
- Either PDF/image attachment OR free-text instructions required (or both); 25MB cap; PDF/PNG/JPG/JPEG/WEBP only
- Auto-creates an order in `pending_po` if none exists; otherwise attaches PO to existing order; never auto-advances stage (admin must Confirm)
- Stores `documents.po` with `submitted_by_customer=true`, `customer_instructions`, `uploaded_at`
- Appends a `customer_po` timeline event for full audit
- Notifies admin via Email + WhatsApp (graceful no-op if integrations not configured)
- New settings fields: `whatsapp.admin_notify_phone`, `whatsapp.po_received_admin_template`, `smtp.admin_notify_email` — exposed in Settings → WhatsApp + Email tabs
- `/quotations/{qid}/send` now flips status to `sent` even when no channel is configured (PDF generation is enough) — unblocks customer PO submission in dev/disabled environments
- Frontend: new `SubmitPoModal.jsx` with file picker + textarea; "Submit PO" button on each MyQuotes row when quote is sent/approved AND order is in pending_po (or no order yet); shows "PO Sent" pill + "Re-submit / Add Note" CTA when already submitted
- Backend tests: 18/18 new Phase-2D tests + 63/63 regression — all passing (`/app/backend/tests/test_phase2d_customer_po_submit.py`)

## Customer-side Order Tracking in My Quotes — implemented (2026-05-06)
- `/public/my-quotes` API now enriches each quote with an `order` summary block (order_number, stage, stage_label, stage_index, milestones[], proforma_url, lr/invoice URLs)
- `_public_order_summary` collapses internal stages into 6 customer-friendly milestones: Order Confirmed → Proforma Invoice Issued → In Production → Packaging → Dispatched → Delivered
- Milestones are marked done/active based on STAGE_ORDER index of the order's current stage, with timestamps pulled from the `timeline.stage` events
- New component `PublicTrackingStrip.jsx` renders a horizontal progress bar (desktop) / vertical list (mobile) with Phosphor icons + dates
- MyQuotes.jsx upgraded from flat table to expandable rows; rows with an associated order auto-expand and show the tracking strip inline
- No new authentication needed — re-uses the existing OTP-based public session token

## Phase 2B + 2C — regression tested (2026-05-06)
- Backend testing agent confirmed 29/29 new tests pass + 34/34 Phase 2A regression tests pass
- End-to-end verified: BizChat status webhook (sent→read), email-open pixel (sent→read), WeasyPrint PDF (valid %PDF), order conversion + 11-stage advance + proforma generation
- Quote /send gracefully returns 200 with {pdf:true, whatsapp:false, email:false} when WA/SMTP empty (no 500)
- No critical bugs; 10 minor UX/hygiene items logged in /app/test_reports/iteration_3.json

## Backlog (post 2C, P1)
- Customer-facing public order tracking page `/track/{order#}` (P0)
- Hot Leads dashboard widget — quotes with READ status not yet approved/rejected
- Auto WhatsApp customer notification on stage change (template per stage)
- Phase 2D: WhatsApp inbound chatbot for self-serve quotes
- Refactor server.py (3315 lines) → routers per module
- Stage transition guard on /orders/{oid}/advance (no jumping forward/backward)
- Switch INR math to Decimal (paise drift)

## Test Status
- Backend: 29/29 Phase 2B/2C + 34/34 Phase 2A regression passing (iteration_3.json, 2026-05-06)
- Frontend: login renders post Emergent-branding removal (smoke tested)
