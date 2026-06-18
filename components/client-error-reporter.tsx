'use client';

import { useEffect } from 'react';
import { installGlobalErrorReporting } from '@/lib/client-error-logger';

/**
 * Mounts the global browser error listeners once, at the app root. Renders
 * nothing. See lib/client-error-logger.ts.
 */
export function ClientErrorReporter() {
  useEffect(() => {
    installGlobalErrorReporting();
  }, []);
  return null;
}
