import { useRef, useState } from "react";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { X, UploadSimple, FileText, PaperPlaneTilt, CheckCircle } from "@phosphor-icons/react";

export default function SubmitPoModal({ open, onClose, quote, token, onSubmitted }) {
  const [file, setFile] = useState(null);
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);

  if (!open) return null;

  const close = () => {
    if (busy) return;
    setFile(null);
    setInstructions("");
    onClose();
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!file && !instructions.trim()) {
      toast.error("Attach a PO PDF or type your instructions before submitting.");
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("token", token);
      fd.append("instructions", instructions.trim());
      if (file) fd.append("file", file);
      const { data } = await api.post(`/public/quote/${quote.id}/submit-po`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      toast.success(`PO received! Our team has been notified${data.admin_notified?.email ? " by email" : ""}${data.admin_notified?.whatsapp ? " and WhatsApp" : ""}.`);
      onSubmitted?.(data);
      close();
    } catch (err) {
      toast.error(formatApiError(err?.response?.data?.detail) || "Could not submit PO. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onClick={close} data-testid="submit-po-modal">
      <div className="bg-white border border-zinc-200 w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200">
          <div>
            <div className="text-[10px] uppercase tracking-[0.22em] font-bold text-[#FBAE17]">Submit Purchase Order</div>
            <h3 className="font-heading font-black text-lg leading-tight">Quote {quote?.quote_number}</h3>
          </div>
          <button onClick={close} disabled={busy} className="text-zinc-400 hover:text-black" data-testid="submit-po-close">
            <X size={20} weight="bold" />
          </button>
        </div>

        <form onSubmit={submit} className="p-5 space-y-4">
          <p className="text-xs text-zinc-600 leading-relaxed">
            Attach your formal PO as a PDF, or simply type your confirmation/instructions below — our team will treat your message as a PO and reach out for any clarifications.
          </p>

          {/* File picker */}
          <div>
            <label className="text-[10px] uppercase tracking-wider font-bold text-zinc-500 mb-1.5 block">PO PDF (optional)</label>
            <div className="border border-dashed border-zinc-300 hover:border-[#FBAE17] transition-colors">
              <input
                ref={inputRef}
                type="file"
                accept="application/pdf,image/*"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
                className="hidden"
                data-testid="submit-po-file-input"
              />
              {!file ? (
                <button type="button" onClick={() => inputRef.current?.click()} className="w-full flex flex-col items-center justify-center gap-2 py-6 text-xs text-zinc-500 hover:text-[#FBAE17]" data-testid="submit-po-file-pick">
                  <UploadSimple size={24} weight="bold" />
                  <span>Click to upload PDF or image</span>
                </button>
              ) : (
                <div className="flex items-center gap-3 p-3" data-testid="submit-po-file-chip">
                  <FileText size={18} weight="bold" className="text-[#FBAE17] shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-bold truncate">{file.name}</div>
                    <div className="text-[10px] font-mono text-zinc-500">{(file.size / 1024).toFixed(1)} KB</div>
                  </div>
                  <button type="button" onClick={() => setFile(null)} className="text-zinc-400 hover:text-red-500 text-xs uppercase tracking-wider font-bold">Remove</button>
                </div>
              )}
            </div>
          </div>

          {/* Instructions */}
          <div>
            <label className="text-[10px] uppercase tracking-wider font-bold text-zinc-500 mb-1.5 block">
              Instructions / Message {!file && <span className="text-red-500 normal-case font-normal">(required if no PDF)</span>}
            </label>
            <textarea
              rows={4}
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder={file ? "Optional — any special instructions, delivery dates, contact info…" : "Please proceed with the order. Required by 30-Apr-26. Contact: Rajesh +91 99xxx xxxxx"}
              className="w-full border border-zinc-300 px-3 py-2.5 text-sm focus:outline-none focus:border-[#FBAE17] resize-none"
              data-testid="submit-po-instructions"
            />
          </div>

          {/* Actions */}
          <div className="flex items-center justify-end gap-2 pt-2 border-t border-zinc-200 -mx-5 px-5">
            <button type="button" onClick={close} disabled={busy} className="text-xs uppercase tracking-wider font-bold text-zinc-500 hover:text-black px-3 py-2.5">
              Cancel
            </button>
            <button
              type="submit"
              disabled={busy || (!file && !instructions.trim())}
              className="bg-[#FBAE17] hover:bg-[#E59D12] text-black font-bold uppercase tracking-wider text-xs px-5 py-3 flex items-center gap-2 disabled:opacity-50"
              data-testid="submit-po-confirm"
            >
              {busy ? (
                <>Submitting…</>
              ) : (
                <>
                  <PaperPlaneTilt size={14} weight="bold" /> Submit PO
                </>
              )}
            </button>
          </div>
          <div className="flex items-start gap-2 text-[10px] text-zinc-500">
            <CheckCircle size={12} weight="bold" className="text-emerald-600 mt-[2px] shrink-0" />
            <span>Your submission is sent to our admin instantly. They'll review and confirm before manufacturing begins. You'll see the live status in the tracking strip above each quote.</span>
          </div>
        </form>
      </div>
    </div>
  );
}
