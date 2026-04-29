// Number → Indian-format words (e.g. 152456.50 →
// "ONE LAKH FIFTY TWO THOUSAND FOUR HUNDRED FIFTY SIX AND FIFTY PAISE ONLY")

const ONES = [
  "", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE",
  "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN",
  "SEVENTEEN", "EIGHTEEN", "NINETEEN",
];
const TENS = [
  "", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY",
];

function twoDigits(n) {
  if (n < 20) return ONES[n];
  const t = Math.floor(n / 10);
  const o = n % 10;
  return TENS[t] + (o ? " " + ONES[o] : "");
}

function threeDigits(n) {
  const h = Math.floor(n / 100);
  const r = n % 100;
  let out = "";
  if (h) out += ONES[h] + " HUNDRED";
  if (r) out += (out ? " " : "") + twoDigits(r);
  return out;
}

export function numberToWordsINR(value) {
  const num = Math.abs(Math.round(Number(value || 0) * 100)) / 100;
  const rupees = Math.floor(num);
  const paise = Math.round((num - rupees) * 100);

  if (rupees === 0 && paise === 0) return "ZERO ONLY";

  const crore = Math.floor(rupees / 10000000);
  const lakh = Math.floor((rupees % 10000000) / 100000);
  const thousand = Math.floor((rupees % 100000) / 1000);
  const remainder = rupees % 1000;

  const parts = [];
  if (crore) parts.push(twoDigits(crore) + " CRORE");
  if (lakh) parts.push(twoDigits(lakh) + " LAKH");
  if (thousand) parts.push(twoDigits(thousand) + " THOUSAND");
  if (remainder) parts.push(threeDigits(remainder));

  let words = parts.join(" ").trim();
  if (paise > 0) {
    words += ` AND ${twoDigits(paise)} PAISE`;
  }
  return (words || "ZERO") + " ONLY";
}
