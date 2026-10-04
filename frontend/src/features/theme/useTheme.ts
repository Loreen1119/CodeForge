import { useEffect, useState } from 'react';

/**
 * 主题切换：两态 light / dark，手动切换。
 * - light：浅色（默认）
 * - dark：VSCode 风格黑底
 *
 * 持久化到 localStorage，在 `<html>` 上设 `data-theme`。
 * 初次访问默认 light。
 */
export type ThemeMode = 'light' | 'dark';

const STORAGE_KEY = 'codeforge-theme';

const LABELS: Record<ThemeMode, string> = {
  light: '浅色',
  dark: '深色',
};

const ICONS: Record<ThemeMode, string> = {
  light: '☀',
  dark: '☾',
};

function readStored(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === 'light' || v === 'dark') return v;
    // 兼容旧数据：auto 视作 light
    if (v === 'auto') return 'light';
  } catch {
    // localStorage 不可用（隐私模式等），用默认
  }
  return 'light';
}

export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>(readStored);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', mode);
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // 持久化失败不阻塞，本次会话仍生效
    }
  }, [mode]);

  /** 切换：light ↔ dark */
  function toggle() {
    setMode((prev) => (prev === 'light' ? 'dark' : 'light'));
  }

  return { mode, toggle, label: LABELS[mode], icon: ICONS[mode] };
}
