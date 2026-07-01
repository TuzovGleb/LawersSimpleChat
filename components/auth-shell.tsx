"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { ShieldCheck, Lock, Server } from "lucide-react";
import { ToasterClient } from "@/components/toaster-client";

/**
 * Shared layout for every auth screen (login, forgot-password,
 * reset-password). Desktop (lg+): two-pane grid — dark branding aside next
 * to the form column. Phones: the aside collapses to a slim brand bar so
 * the form is the first thing above the fold; the trust bullets are
 * desktop-only. Form typography (h1/.lede) lives in app/globals.css.
 */
export function AuthShell({
  children,
  footer,
}: {
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="grid min-h-dvh grid-rows-[auto_1fr] bg-[var(--bg)] lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] lg:grid-rows-1">
      <aside className="relative flex flex-col overflow-hidden bg-[var(--bg-dark)] pl-[max(1.25rem,env(safe-area-inset-left))] pr-[max(1.25rem,env(safe-area-inset-right))] pb-4 pt-[max(1rem,env(safe-area-inset-top))] text-[var(--text-on-dark)] lg:px-14 lg:pb-12 lg:pt-12">
        {/* Ambient brand glow (was .auth-aside::after) */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(800px_360px_at_80%_10%,rgba(168,118,62,0.1),transparent_60%),radial-gradient(700px_300px_at_10%_90%,rgba(122,46,46,0.18),transparent_65%)]"
        />
        <Link
          href="/"
          className="logo relative z-[1] text-[22px] lg:mb-auto lg:text-2xl"
          style={{ color: "#fff" }}
        >
          Джейхелпер
          <span className="dot" style={{ color: "var(--brand-accent-bg)" }}>
            .
          </span>
        </Link>
        <div className="relative z-[1] mt-auto hidden lg:block">
          <h2
            style={{
              color: "#fff",
              fontFamily: "var(--font-serif-family)",
              fontWeight: 500,
              fontSize: 30,
              lineHeight: 1.2,
              margin: "0 0 24px",
              maxWidth: 420,
              letterSpacing: "-.012em",
            }}
          >
            AI-помощник для российских юристов
          </h2>
          <ul
            style={{
              listStyle: "none",
              padding: 0,
              margin: 0,
              display: "flex",
              flexDirection: "column",
              gap: 14,
            }}
          >
            <li style={{ display: "grid", gridTemplateColumns: "22px 1fr", gap: 12, fontSize: 15.5, color: "rgba(250,250,247,.85)", lineHeight: 1.45 }}>
              <Server size={20} style={{ color: "var(--secondary-accent)", marginTop: 1 }} />
              <span>Данные хранятся в России. Соответствие 152-ФЗ.</span>
            </li>
            <li style={{ display: "grid", gridTemplateColumns: "22px 1fr", gap: 12, fontSize: 15.5, color: "rgba(250,250,247,.85)", lineHeight: 1.45 }}>
              <ShieldCheck size={20} style={{ color: "var(--secondary-accent)", marginTop: 1 }} />
              <span>Содержимое дел не используется для обучения моделей.</span>
            </li>
            <li style={{ display: "grid", gridTemplateColumns: "22px 1fr", gap: 12, fontSize: 15.5, color: "rgba(250,250,247,.85)", lineHeight: 1.45 }}>
              <Lock size={20} style={{ color: "var(--secondary-accent)", marginTop: 1 }} />
              <span>Конфиденциальность — технически и юридически.</span>
            </li>
          </ul>
        </div>
      </aside>

      <main className="flex flex-col bg-[var(--bg)] pl-[max(1rem,env(safe-area-inset-left))] pr-[max(1rem,env(safe-area-inset-right))] pt-5 pb-[max(28px,env(safe-area-inset-bottom))] lg:px-14 lg:pb-10 lg:pt-7">
        <div className="auth-form-wrap m-auto w-full py-2 lg:max-w-[420px] lg:py-8">
          {children}
        </div>
        {footer ? (
          <div className="mt-8 flex flex-wrap gap-[18px] border-t border-[var(--border-strong)] pt-6 text-[13px] text-[var(--text-muted)] [&_a]:text-[var(--text-muted)]">
            {footer}
          </div>
        ) : null}
      </main>

      <ToasterClient />
    </div>
  );
}
