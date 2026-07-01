'use client';

import { useEffect } from 'react';
import { reportClientError } from '@/lib/client-error-logger';

// Root error boundary for the App Router. Catches render errors that escape all
// nested boundaries (including the root layout) and reports them before showing
// a minimal fallback. global-error.tsx must render its own <html>/<body>.
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    reportClientError(error, { event: 'global-error', digest: error.digest });
  }, [error]);

  return (
    <html lang="ru">
      <body
        style={{
          display: 'flex',
          minHeight: '100dvh',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '1rem',
          fontFamily: 'system-ui, sans-serif',
          padding: '2rem',
          textAlign: 'center',
        }}
      >
        <h2 style={{ fontSize: '1.25rem', fontWeight: 600 }}>Что-то пошло не так</h2>
        <p style={{ color: '#666', maxWidth: '32rem' }}>
          Произошла непредвиденная ошибка. Мы уже её зафиксировали. Попробуйте
          обновить страницу.
        </p>
        <button
          type="button"
          onClick={() => reset()}
          style={{
            padding: '0.5rem 1.25rem',
            borderRadius: '0.5rem',
            border: '1px solid #ccc',
            cursor: 'pointer',
            background: '#111',
            color: '#fff',
          }}
        >
          Попробовать снова
        </button>
      </body>
    </html>
  );
}
