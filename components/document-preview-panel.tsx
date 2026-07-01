"use client";

import { useEffect, useRef, useState } from "react";
import { AlertCircle, Download, FileText, Loader2, X } from "lucide-react";

interface DocumentPreviewPanelProps {
  /** Persisted/backend chat id used to route the document request. */
  chatId: string;
  /** Drafting tool call id (artifact.id). */
  artifactId: string;
  fileName: string;
  onClose: () => void;
}

/**
 * Right-side panel that previews a drafted .docx exactly as it looks — text AND
 * formatting — by fetching the rendered file and drawing it with docx-preview.
 * Full-screen on mobile, a fixed panel on desktop. Download stays available.
 */
export function DocumentPreviewPanel({
  chatId,
  artifactId,
  fileName,
  onClose,
}: DocumentPreviewPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  const downloadUrl = `/api/chat/${encodeURIComponent(chatId)}/documents/${encodeURIComponent(
    artifactId,
  )}`;

  // Fetch the rendered .docx and draw it with docx-preview (lazy-imported so it
  // never lands in the main bundle / SSR).
  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    (async () => {
      try {
        const response = await fetch(downloadUrl);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const blob = await response.blob();
        const { renderAsync } = await import("docx-preview");
        if (cancelled || !containerRef.current) return;
        containerRef.current.innerHTML = "";
        await renderAsync(blob, containerRef.current, undefined, {
          className: "docx",
          inWrapper: true,
          ignoreWidth: false,
          ignoreHeight: false,
          breakPages: true,
        });
        if (!cancelled) setStatus("ready");
      } catch {
        if (!cancelled) setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [downloadUrl]);

  // Close on Escape.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="docpreview fixed inset-0 z-50 flex justify-end">
      {/* Signature treatment: the panel reads like a legal document laid on a
          warm desk — A4 sheet with a soft paper shadow, an oxblood spine in the
          header, the filename set in the serif display face. Plus the entrance
          choreography (scrim fades, sheet slides in) under reduced-motion guard.
          We also tame docx-preview's defaults: no auto-hyphenation (Word's off),
          no bulky gray wrapper padding. */}
      <style>{`
        .docpreview .docpreview-scrim { animation: docpreview-fade .22s ease-out both; }
        .docpreview .docpreview-panel { animation: docpreview-slide .28s cubic-bezier(.22,.61,.36,1) both; }
        @keyframes docpreview-fade { from { opacity: 0 } to { opacity: 1 } }
        @keyframes docpreview-slide { from { transform: translateX(24px); opacity: 0 } to { transform: none; opacity: 1 } }
        @media (prefers-reduced-motion: reduce) {
          .docpreview .docpreview-scrim, .docpreview .docpreview-panel { animation: none; }
        }
        .docx-host .docx-wrapper { padding: 20px 0 4px 0 !important; background: transparent !important; }
        .docx-host .docx-wrapper > section.docx {
          margin: 0 auto 18px auto !important;
          border-radius: 2px;
          box-shadow: var(--shadow-md);
        }
        .docx-host, .docx-host * { -webkit-hyphens: none !important; hyphens: none !important; }
      `}</style>
      <div
        className="docpreview-scrim absolute inset-0"
        style={{ background: "rgba(20, 24, 31, .32)", backdropFilter: "blur(2px)" }}
        onClick={onClose}
        aria-hidden
      />
      <aside
        className="docpreview-panel relative flex h-full w-full max-w-full flex-col md:w-[840px]"
        style={{
          background: "var(--bg-elevated)",
          borderLeft: "1px solid var(--border-strong)",
          boxShadow: "var(--shadow-lg)",
        }}
        role="dialog"
        aria-label={`Предпросмотр документа ${fileName}`}
      >
        <header
          className="flex items-center justify-between gap-3 px-5 py-3.5 pt-[max(0.875rem,env(safe-area-inset-top))]"
          style={{
            borderBottom: "1px solid var(--border-soft)",
            borderTop: "2px solid var(--brand-accent)",
          }}
        >
          <div className="flex min-w-0 items-center gap-3">
            <span
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px]"
              style={{ background: "var(--brand-accent-bg)", color: "var(--brand-accent)" }}
              aria-hidden
            >
              <FileText className="h-[18px] w-[18px]" />
            </span>
            <span className="flex min-w-0 flex-col">
              <span
                className="text-[10px] font-semibold uppercase leading-none tracking-[0.14em]"
                style={{ color: "var(--text-muted)" }}
              >
                Предпросмотр документа
              </span>
              <span
                className="mt-1 truncate text-[15px] leading-snug"
                style={{ color: "var(--text-primary)", fontFamily: "var(--font-serif-family)" }}
                title={`${fileName}.docx`}
              >
                {fileName}
                <span style={{ color: "var(--text-muted)" }}>.docx</span>
              </span>
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <a
              href={downloadUrl}
              className="inline-flex items-center gap-1.5 rounded-[8px] px-3 py-1.5 text-xs font-medium text-white transition-colors"
              style={{ background: "var(--brand-accent)" }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--brand-accent-hover)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "var(--brand-accent)")}
              title="Скачать .docx"
            >
              <Download className="h-3.5 w-3.5" />
              Скачать
            </a>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-8 w-8 items-center justify-center rounded-[8px] transition-colors hover:bg-[var(--bg-soft)]"
              style={{ color: "var(--text-secondary)" }}
              aria-label="Закрыть"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </header>

        <div
          className="relative flex-1 overflow-auto pb-[env(safe-area-inset-bottom)]"
          style={{ background: "var(--bg-soft)" }}
        >
          {status === "loading" && (
            <div
              className="absolute inset-0 flex items-center justify-center gap-2 text-sm"
              style={{ color: "var(--text-secondary)" }}
            >
              <Loader2 className="h-5 w-5 animate-spin" />
              Загружаю документ…
            </div>
          )}
          {status === "error" && (
            <div
              className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center"
            >
              <AlertCircle className="h-6 w-6" style={{ color: "var(--brand-accent)" }} />
              <p className="text-sm" style={{ color: "var(--text-primary)" }}>
                Не удалось открыть предпросмотр
              </p>
              <a
                href={downloadUrl}
                className="text-xs font-medium underline-offset-2 hover:underline"
                style={{ color: "var(--brand-accent)" }}
              >
                Скачать файл
              </a>
            </div>
          )}
          <div
            ref={containerRef}
            className="docx-host"
            style={{ display: status === "ready" ? "block" : "none" }}
          />
        </div>
      </aside>
    </div>
  );
}
