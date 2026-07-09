import { NextRequest, NextResponse } from 'next/server';
import { requirePermission } from '@/lib/admin';
import { logger, requestIdFrom } from '@/lib/server-logger';

export const runtime = 'edge';

// Форма jsonb из admin_get_settings / admin_update_settings.
interface RawSettings {
  signup_enabled?: boolean;
  signup_trial_days?: number;
}

// Маппинг snake_case (SQL) → camelCase (клиент) в одном месте.
function toClientSettings(data: unknown) {
  const raw = (data ?? {}) as RawSettings;
  return {
    signupEnabled: raw.signup_enabled === true,
    signupTrialDays: typeof raw.signup_trial_days === 'number' ? raw.signup_trial_days : 0,
  };
}

export async function GET(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();

  const { supabase, response: adminResponse } = await requirePermission('admin.settings.view');
  if (adminResponse) return adminResponse;

  try {
    const { data, error } = await supabase!.rpc('admin_get_settings');

    if (error) {
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'admin_settings_get_supabase_error',
        err: error,
      });
      if (typeof error.message === 'string' && error.message.includes('forbidden')) {
        return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
      }
      return NextResponse.json({ error: 'Не удалось загрузить настройки.' }, { status: 500 });
    }

    logger.info('Admin settings fetched', {
      request_id: requestId,
      event: 'admin_settings_fetched',
      duration_ms: Date.now() - startedAt,
    });
    return NextResponse.json({ settings: toClientSettings(data) });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'admin_settings_get_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const requestId = requestIdFrom(req);
  const startedAt = Date.now();

  const { supabase, response: adminResponse } = await requirePermission('admin.settings.manage');
  if (adminResponse) return adminResponse;

  try {
    const body = await req.json();

    // Оба поля опциональны (NULL в RPC = «не менять»), но если поле передано —
    // тип должен быть корректным.
    const hasSignupEnabled = body?.signupEnabled !== undefined && body?.signupEnabled !== null;
    const hasTrialDays = body?.signupTrialDays !== undefined && body?.signupTrialDays !== null;

    if (hasSignupEnabled && typeof body.signupEnabled !== 'boolean') {
      return NextResponse.json(
        { error: 'Некорректное значение переключателя регистрации.' },
        { status: 400 },
      );
    }
    if (hasTrialDays && (typeof body.signupTrialDays !== 'number' || !Number.isInteger(body.signupTrialDays))) {
      return NextResponse.json(
        { error: 'Число дней триала должно быть целым числом.' },
        { status: 400 },
      );
    }
    if (hasTrialDays && body.signupTrialDays < 0) {
      return NextResponse.json(
        { error: 'Число дней триала не может быть отрицательным' },
        { status: 400 },
      );
    }

    const { data, error } = await supabase!.rpc('admin_update_settings', {
      p_signup_enabled: hasSignupEnabled ? body.signupEnabled : null,
      p_signup_trial_days: hasTrialDays ? body.signupTrialDays : null,
    });

    if (error) {
      if (typeof error.message === 'string' && error.message.includes('invalid trial days')) {
        return NextResponse.json(
          { error: 'Число дней триала не может быть отрицательным' },
          { status: 400 },
        );
      }
      logger.error('Supabase error', {
        request_id: requestId,
        event: 'admin_settings_post_supabase_error',
        err: error,
      });
      if (typeof error.message === 'string' && error.message.includes('forbidden')) {
        return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
      }
      return NextResponse.json({ error: 'Не удалось сохранить настройки.' }, { status: 500 });
    }

    logger.info('Admin settings updated', {
      request_id: requestId,
      event: 'admin_settings_updated',
      duration_ms: Date.now() - startedAt,
      signup_enabled_changed: hasSignupEnabled,
      trial_days_changed: hasTrialDays,
    });
    return NextResponse.json({ settings: toClientSettings(data) });
  } catch (error) {
    logger.error('Unexpected error', {
      request_id: requestId,
      event: 'admin_settings_post_unexpected_error',
      err: error,
    });
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
