'use client';

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { features, useCases, security } from "@/lib/landing-content";

export const dynamic = 'force-dynamic';

const CALENDLY_URL = 'https://calendly.com/glebtuzov/30-minute-call-with-tuzov-gleb-opencv';

export default function HomePage() {
  const router = useRouter();
  const { user, loading } = useAuth();
  const [isScrolled, setIsScrolled] = useState(false);

  useEffect(() => {
    if (!loading && user) {
      router.replace('/workspace');
    }
  }, [user, loading, router]);

  useEffect(() => {
    const onScroll = () => setIsScrolled(window.scrollY > 4);
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  if (loading || user) {
    return (
      <div className="flex min-h-dvh items-center justify-center" style={{ background: 'var(--bg)' }}>
        <div className="text-center">
          <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-solid" style={{ borderColor: 'var(--brand-accent)', borderRightColor: 'transparent' }} />
          <p className="mt-4" style={{ color: 'var(--text-secondary)' }}>
            {user ? 'Переход в рабочее пространство…' : 'Загрузка…'}
          </p>
        </div>
      </div>
    );
  }

  const handleStart = () => window.open(CALENDLY_URL, '_blank');
  const handleLogin = () => router.push('/auth');

  return (
    <div style={{ background: 'var(--bg)' }}>
      {/* Header */}
      <header className={`app-header ${isScrolled ? 'scrolled' : ''}`}>
        <div className="container-x app-header-inner">
          <a href="#top" className="logo">
            Джейхелпер<span className="dot">.</span>
          </a>
          <nav className="flex items-center gap-3">
            <a href="#features" className="hidden md:inline text-sm font-medium" style={{ color: 'var(--text-primary)', opacity: .82 }}>Возможности</a>
            <a href="#use-cases" className="hidden md:inline text-sm font-medium" style={{ color: 'var(--text-primary)', opacity: .82 }}>Сценарии</a>
            <a href="#security" className="hidden md:inline text-sm font-medium" style={{ color: 'var(--text-primary)', opacity: .82 }}>Безопасность</a>
            <button onClick={handleLogin} className="btn btn-secondary btn-sm">Войти</button>
            <button onClick={handleStart} className="btn btn-primary btn-sm">Записаться на звонок</button>
          </nav>
        </div>
      </header>

      {/* Hero */}
      <section id="top" className="container-x" style={{ padding: '72px 28px 88px' }}>
        <div className="max-w-3xl">
          <div className="sec-eyebrow">для российских юристов</div>
          <h1 style={{ fontSize: 'clamp(38px, 5.4vw, 62px)', lineHeight: 1.07, margin: '0 0 22px' }}>
            Искусственный интеллект для юридической практики
          </h1>
          <p style={{ fontSize: 19, lineHeight: 1.55, color: '#404652', maxWidth: 620, margin: '0 0 32px' }}>
            Специализированная система для российских юристов. Анализируйте документы,
            готовьте заключения, работайте с делами эффективнее.
          </p>
          <div className="flex flex-wrap gap-3 items-center">
            <button onClick={handleStart} className="btn btn-primary btn-lg">
              Записаться на звонок
            </button>
            <span style={{ fontSize: 13.5, color: 'var(--text-secondary)' }}>
              Консультация бесплатная, ~30 минут
            </span>
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" style={{ background: 'var(--bg-soft)', borderTop: '1px solid var(--border-soft)', borderBottom: '1px solid var(--border-soft)', padding: '110px 0' }}>
        <div className="container-x">
          <div className="sec-eyebrow">Возможности</div>
          <h2 style={{ fontSize: 'clamp(28px, 3.5vw, 40px)', maxWidth: 720, marginBottom: 48 }}>
            Что вы получаете в Джейхелпере
          </h2>
          <div className="grid gap-5" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
            {features.map((feature) => (
              <div key={feature.id} className="card-x" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <h3 style={{ fontSize: 20, fontWeight: 500, lineHeight: 1.25 }}>{feature.name}</h3>
                <p style={{ fontSize: 15, color: '#4B5260', margin: 0, lineHeight: 1.55 }}>{feature.description}</p>
                <ul style={{ listStyle: 'none', padding: 0, margin: '8px 0 0', display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {feature.benefits.map((benefit, i) => (
                    <li key={i} style={{ display: 'grid', gridTemplateColumns: '16px 1fr', gap: 10, fontSize: 14, color: '#2A313D', alignItems: 'start', lineHeight: 1.5 }}>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--brand-accent)', marginTop: 3 }}>
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                      <span>{benefit}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Use cases */}
      <section id="use-cases" style={{ padding: '110px 0' }}>
        <div className="container-x">
          <div className="sec-eyebrow">Сценарии</div>
          <h2 style={{ fontSize: 'clamp(28px, 3.5vw, 40px)', maxWidth: 720, marginBottom: 48 }}>
            Как это работает в реальной практике
          </h2>
          <div className="grid gap-5" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
            {useCases.map((useCase, idx) => (
              <div key={idx} className="card-x">
                <div style={{ fontFamily: 'var(--font-serif-family)', fontStyle: 'italic', color: 'var(--brand-accent)', fontSize: 14, fontWeight: 500, marginBottom: 10 }}>
                  0{idx + 1}
                </div>
                <h3 style={{ fontSize: 22, fontWeight: 500, margin: '0 0 10px', lineHeight: 1.25 }}>{useCase.title}</h3>
                <p style={{ fontSize: 15, color: '#4B5260', margin: 0, lineHeight: 1.55 }}>{useCase.description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Security */}
      <section id="security" style={{ background: 'var(--bg-soft)', borderTop: '1px solid var(--border-soft)', borderBottom: '1px solid var(--border-soft)', padding: '110px 0' }}>
        <div className="container-x">
          <div className="sec-eyebrow">Безопасность</div>
          <h2 style={{ fontSize: 'clamp(28px, 3.5vw, 40px)', maxWidth: 720, marginBottom: 48 }}>
            {security.title}
          </h2>
          <div className="grid gap-5" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))' }}>
            {security.items.map((item, idx) => (
              <div key={idx} className="card-x">
                <h3 style={{ fontSize: 18, fontWeight: 500, margin: '0 0 10px' }}>{item.title}</h3>
                <p style={{ fontSize: 14.5, color: '#4B5260', margin: 0, lineHeight: 1.55 }}>{item.description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Final CTA */}
      <section style={{ background: 'var(--bg-dark)', color: 'var(--text-on-dark)', padding: '100px 0', position: 'relative', overflow: 'hidden' }}>
        <div
          aria-hidden
          style={{
            position: 'absolute',
            inset: 0,
            background:
              'radial-gradient(800px 360px at 80% 10%, rgba(168,118,62,.10), transparent 60%), radial-gradient(700px 300px at 10% 90%, rgba(122,46,46,.18), transparent 65%)',
            pointerEvents: 'none',
          }}
        />
        <div className="container-x" style={{ position: 'relative', zIndex: 1, textAlign: 'center', maxWidth: 720, margin: '0 auto' }}>
          <h2 style={{ fontSize: 'clamp(32px, 4.4vw, 46px)', margin: '0 0 16px', color: '#fff' }}>
            Готовы начать?
          </h2>
          <p style={{ fontSize: 17, color: 'var(--on-dark-muted)', margin: '0 0 32px' }}>
            Запишитесь на короткую консультацию — обсудим ваши задачи и покажем возможности системы.
          </p>
          <button onClick={handleStart} className="btn btn-primary btn-lg">
            Записаться на звонок
          </button>
          <p style={{ marginTop: 16, fontSize: 13.5, color: 'var(--on-dark-muted)' }}>
            Бесплатно, ~30 минут
          </p>
        </div>
      </section>

      {/* Footer */}
      <footer style={{ background: 'var(--bg)', padding: '48px 0 32px', borderTop: '1px solid var(--border-strong)' }}>
        <div className="container-x">
          <div className="flex items-center justify-between gap-8 flex-wrap">
            <a href="#top" className="logo">
              Джейхелпер<span className="dot">.</span>
            </a>
            <div className="flex gap-7 flex-wrap">
              <button onClick={handleLogin} style={{ background: 'transparent', border: 0, color: 'var(--text-secondary)', fontSize: 14, padding: 0, cursor: 'pointer' }}>Войти</button>
              <a href="#features" style={{ color: 'var(--text-secondary)', fontSize: 14 }}>Возможности</a>
              <a href="#security" style={{ color: 'var(--text-secondary)', fontSize: 14 }}>Безопасность</a>
            </div>
          </div>
          <div style={{ marginTop: 24, paddingTop: 20, borderTop: '1px solid var(--border-strong)', display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', color: 'var(--text-secondary)', fontSize: 13 }}>
            <span>© {new Date().getFullYear()} Джейхелпер. Все права защищены.</span>
            <span>Работаем в России. Оплата картой РФ.</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
