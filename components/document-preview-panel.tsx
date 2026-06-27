"use client";

import { useEffect, useRef, useState } from "react";
import { AlertCircle, Download, Loader2, X } from "lucide-react";

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
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} aria-hidden />
      {/* Tame docx-preview's defaults: kill auto-hyphenation (Word doesn't do it),
          drop the bulky gray wrapper padding, let the A4 sheet sit flush. */}
      <style>{`
        .docx-host .docx-wrapper { padding: 12px 0 0 0 !important; background: transparent !important; }
        .docx-host .docx-wrapper > section.docx { margin: 0 auto 14px auto !important; }
        .docx-host, .docx-host * { -webkit-hyphens: none !important; hyphens: none !important; }
      `}</style>
      <aside
        className="relative flex h-full w-full max-w-full flex-col bg-white shadow-2xl md:w-[840px]"
        style={{ borderLeft: "1px solid var(--border-strong)" }}
        role="dialog"
        aria-label={`Предпросмотр документа ${fileName}`}
      >
        <header
          className="flex items-center justify-between gap-2 px-4 py-3"
          style={{ borderBottom: "1px solid var(--border-soft)" }}
        >
          <span
            className="truncate text-sm font-medium"
            style={{ color: "var(--text-primary)" }}
            title={`${fileName}.docx`}
          >
            {fileName}.docx
          </span>
          <div className="flex items-center gap-1.5">
            <a
              href={downloadUrl}
              className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs transition-colors hover:bg-[var(--bg-soft)]"
              style={{ border: "1px solid var(--border-strong)", color: "var(--text-secondary)" }}
              title="Скачать .docx"
            >
              <Download className="h-3.5 w-3.5" />
              Скачать
            </a>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors hover:bg-[var(--bg-soft)]"
              style={{ color: "var(--text-secondary)" }}
              aria-label="Закрыть"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </header>

        <div className="relative flex-1 overflow-auto" style={{ background: "#f3f3f1" }}>
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
              className="absolute inset-0 flex items-center justify-center gap-2 text-sm"
              style={{ color: "#DC2626" }}
            >
              <AlertCircle className="h-5 w-5" />
              Не удалось загрузить документ
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
