import { useState, useEffect, useRef, useMemo } from "react";
import { CaretDown, MagnifyingGlass, X } from "@phosphor-icons/react";

// All 28 Indian states + 8 Union Territories + 1 catch-all for international
// customers. The order: states alphabetical, then UTs alphabetical, then
// "Outside India" pinned at the bottom so it's always findable.
export const INDIAN_STATES = [
  "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
  "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
  "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
  "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
  "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
];
export const INDIAN_UTS = [
  "Andaman and Nicobar Islands", "Chandigarh",
  "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir",
  "Ladakh", "Lakshadweep", "Puducherry",
];
export const OUTSIDE_INDIA = "Outside India";

export const ALL_STATE_OPTIONS = [
  ...INDIAN_STATES.map((s) => ({ value: s, group: "State" })),
  ...INDIAN_UTS.map((s) => ({ value: s, group: "Union Territory" })),
  { value: OUTSIDE_INDIA, group: "International" },
];

/**
 * Searchable dropdown for Indian states + UTs + "Outside India".
 *
 *   <StateSelect value={state} onChange={setState} required testId="state-select" />
 *
 * Props mirror a controlled <input>: `value` is the selected option (string),
 * `onChange(v)` fires with the new value. `required` toggles the red asterisk
 * displayed by the parent <Field/> wrapper — the component itself doesn't
 * render a label.
 */
export default function StateSelect({
  value,
  onChange,
  required = false,
  placeholder = "Choose state…",
  testId = "state-select",
  className = "",
  disabled = false,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const wrapRef = useRef(null);
  const inputRef = useRef(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return ALL_STATE_OPTIONS;
    return ALL_STATE_OPTIONS.filter((o) => o.value.toLowerCase().includes(q));
  }, [query]);

  // Click-outside + Esc to close
  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (!wrapRef.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  // Focus the search input when opening
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 30); }, [open]);

  const pick = (v) => {
    onChange(v);
    setQuery("");
    setOpen(false);
  };

  return (
    <div ref={wrapRef} className={`relative ${className}`}>
      {/* Hidden text input mirrors the value so native browser `required` validation works on form submit */}
      <input
        type="text"
        value={value || ""}
        readOnly
        required={required}
        tabIndex={-1}
        aria-hidden="true"
        className="sr-only"
        data-testid={`${testId}-hidden`}
      />
      <button
        type="button"
        onClick={() => !disabled && setOpen((o) => !o)}
        disabled={disabled}
        data-testid={testId}
        className={`w-full border border-zinc-300 px-3 py-2 text-sm bg-white flex items-center justify-between gap-2 text-left ${disabled ? "opacity-60 cursor-not-allowed" : "hover:border-zinc-400"} focus:outline-none focus:border-[#FBAE17]`}
      >
        <span className={value ? "text-[#1A1A1A]" : "text-zinc-400"}>
          {value || placeholder}
        </span>
        <span className="flex items-center gap-1">
          {value && !disabled && (
            <span
              role="button"
              tabIndex={-1}
              onClick={(e) => { e.stopPropagation(); pick(""); }}
              data-testid={`${testId}-clear`}
              className="text-zinc-400 hover:text-red-500 p-0.5"
              aria-label="Clear selection"
            >
              <X size={12} weight="bold" />
            </span>
          )}
          <CaretDown size={12} weight="bold" className="text-zinc-500" />
        </span>
      </button>

      {open && (
        <div
          className="absolute z-30 left-0 right-0 mt-1 bg-white border border-zinc-200 shadow-lg max-h-72 flex flex-col"
          data-testid={`${testId}-popover`}
        >
          <div className="relative border-b border-zinc-100 px-2 py-1.5">
            <MagnifyingGlass size={12} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search states…"
              className="w-full pl-7 pr-2 py-1 text-xs focus:outline-none"
              data-testid={`${testId}-search`}
            />
          </div>
          <div className="overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="px-3 py-4 text-xs text-zinc-400 text-center">No matches.</div>
            ) : (
              (() => {
                let lastGroup = null;
                return filtered.map((o) => {
                  const showHeading = o.group !== lastGroup;
                  lastGroup = o.group;
                  return (
                    <div key={o.value}>
                      {showHeading && (
                        <div className="px-3 pt-2 pb-1 text-[9px] font-bold uppercase tracking-[0.18em] text-zinc-400 bg-zinc-50/80">
                          {o.group}
                        </div>
                      )}
                      <button
                        type="button"
                        onClick={() => pick(o.value)}
                        data-testid={`${testId}-option-${o.value.replace(/\s+/g, "-").toLowerCase()}`}
                        className={`w-full text-left px-3 py-1.5 text-sm hover:bg-[#FBAE17]/10 ${value === o.value ? "bg-[#FBAE17]/15 font-bold" : ""}`}
                      >
                        {o.value}
                      </button>
                    </div>
                  );
                });
              })()
            )}
          </div>
        </div>
      )}
    </div>
  );
}
