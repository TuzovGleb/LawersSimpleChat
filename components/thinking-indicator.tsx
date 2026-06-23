"use client";

import { useEffect, useState } from "react";
import { Clock } from "lucide-react";

interface ThinkingIndicatorProps {
  isThinking: boolean;
  // Wall-clock start of THIS chat's in-flight turn. The elapsed time is derived
  // from it (not from when this component mounted), so switching between chats
  // shows each chat's own elapsed time instead of resetting or sharing a timer.
  startedAt?: number | null;
  thinkingTime?: number; // Время размышления в секундах (показываем после завершения)
  modelName?: string;
}

export function ThinkingIndicator({ isThinking, startedAt, thinkingTime, modelName }: ThinkingIndicatorProps) {
  const [elapsedTime, setElapsedTime] = useState(0);

  useEffect(() => {
    if (!isThinking || !startedAt) {
      setElapsedTime(0);
      return;
    }

    const tick = () => setElapsedTime(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
    tick();
    const interval = setInterval(tick, 250);

    return () => clearInterval(interval);
  }, [isThinking, startedAt]);

  // Форматируем время
  const formatTime = (seconds: number) => {
    if (seconds < 60) {
      return `${seconds}с`;
    }
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}м ${secs}с`;
  };

  // Если показываем результат размышления (после завершения)
  if (thinkingTime && !isThinking) {
    return (
      <div className="flex items-center gap-2 rounded-lg bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
        <span>
          Я подумал {formatTime(thinkingTime)}
        </span>
      </div>
    );
  }

  // Показываем процесс размышления
  if (isThinking) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <span className="font-medium">Думаю</span>
        <div className="flex items-center gap-1.5">
          <Clock className="h-3.5 w-3.5" />
          <span className="font-mono text-xs">{formatTime(elapsedTime)}</span>
        </div>
      </div>
    );
  }

  return null;
}

