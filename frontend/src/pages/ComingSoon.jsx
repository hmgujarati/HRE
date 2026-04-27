import PageHeader from "@/components/PageHeader";
import { Wrench } from "@phosphor-icons/react";

export default function ComingSoon({ title }) {
  return (
    <div className="animate-fade-in">
      <PageHeader eyebrow="Phase 2" title={title || "Coming Soon"} subtitle="This module is reserved for the next development phase." testId="soon-header" />
      <div className="p-12 flex flex-col items-center text-center">
        <div className="w-16 h-16 bg-[#FBAE17] flex items-center justify-center mb-4"><Wrench size={28} weight="bold" /></div>
        <div className="font-heading font-black text-2xl">Under construction</div>
        <p className="text-zinc-500 text-sm mt-2 max-w-md">Quotation generation, WhatsApp chatbot and Expo lead capture will arrive in Phase 2.</p>
      </div>
    </div>
  );
}
