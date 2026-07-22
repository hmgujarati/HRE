// Tiny date helpers — backend stores ISO YYYY-MM-DD, UI shows DD/MM/YYYY (Indian convention).

export function toDmy(iso) {
  if (!iso) return "";
  // Accept already-formatted dd/mm/yyyy without re-parsing
  const dmySlash = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(iso);
  if (dmySlash) return iso;
  // Accept legacy dd-mm-yyyy and normalise to dd/mm/yyyy
  const dmyDash = /^(\d{2})-(\d{2})-(\d{4})$/.exec(iso);
  if (dmyDash) return `${dmyDash[1]}/${dmyDash[2]}/${dmyDash[3]}`;
  const isoMatch = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (isoMatch) return `${isoMatch[3]}/${isoMatch[2]}/${isoMatch[1]}`;
  // Fall back to native Date parsing (e.g. full ISO timestamps from the API)
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  return `${dd}/${mm}/${d.getFullYear()}`;
}

export function fromDmy(dmy) {
  if (!dmy) return "";
  // Accept both / and - as separator, but canonical is /
  const m = /^(\d{2})[/-](\d{2})[/-](\d{4})$/.exec(dmy.trim());
  if (!m) return null;            // invalid → caller surfaces an error
  const day = parseInt(m[1], 10);
  const mon = parseInt(m[2], 10);
  const yr = parseInt(m[3], 10);
  if (mon < 1 || mon > 12 || day < 1 || day > 31) return null;
  // Round-trip via Date so invalid combos like 31/02/2026 fail cleanly
  const d = new Date(yr, mon - 1, day);
  if (d.getFullYear() !== yr || d.getMonth() !== mon - 1 || d.getDate() !== day) return null;
  return `${yr}-${String(mon).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}
