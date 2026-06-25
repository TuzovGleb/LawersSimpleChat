"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { ShieldCheck, Lock, Server } from "lucide-react";
import { ToasterClient } from "@/components/toaster-client";

/**
 * Shared two-column layout for every auth screen (login, forgot-password,
 * reset-password). Keeps the branding aside + form column pixel-identical
 * across screens. Styles live in app/globals.css (.auth-shell family).
 */
export function AuthShell({
  children,
  footer,
}: {
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="auth-shell">
      <aside className="auth-aside">
        <Link href="/" className="logo" style={{ color: "#fff" }}>
          Джейхелпер
          <span className="dot" style={{ color: "var(--brand-accent-bg)" }}>
            .
          </span>
        </Link>
        <div className="aside-body">
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

      <main className="auth-main">
        <div className="auth-form-wrap">{children}</div>
        {footer ? <div className="auth-footer">{footer}</div> : null}
      </main>

      <ToasterClient />
    </div>
  );
}
