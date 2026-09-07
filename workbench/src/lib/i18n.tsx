/** i18n 轻量实现（不引 next-intl / react-intl）。
 *
 * 公共 API：
 * - `useT` —— 返回 `(key, params?) => string` 翻译函数（hook）
 * - `useLocale` —— 返回 `{locale, setLocale, locales}`
 * - `tFor(locale, key, params?)` —— 非 hook 版本（client 组件外用，初始取系统 locale）
 * - `<I18nProvider>` —— 把当前 locale 注入 React context；使用方在 workbench 根节点包一层
 *
 * 回退链：当前 locale → zh → key 自身（拼参数）。
 *
 * locale 解析：
 * - localStorage 优先（手动切换持久化）
 * - 否则 navigator.language 取前两位
 * - 不在 'zh' / 'en' 之列 → 默认 'zh'
 */
"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import zhMessages from "./messages/zh.json";
import enMessages from "./messages/en.json";

export type Locale = "zh" | "en";
export const SUPPORTED_LOCALES: Locale[] = ["zh", "en"];

type Messages = Record<string, string>;
const MESSAGES: Record<Locale, Messages> = {
  zh: zhMessages as Messages,
  en: enMessages as Messages,
};

const STORAGE_KEY = "cb.locale";

function detectInitialLocale(): Locale {
  if (typeof window === "undefined") return "zh";
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "zh" || saved === "en") return saved;
  } catch {
    // localStorage may be unavailable (privacy mode); fall through.
  }
  const lang =
    typeof navigator !== "undefined" ? navigator.language || "" : "";
  const primary = lang.toLowerCase().slice(0, 2);
  if (primary === "en") return "en";
  return "zh";
}

function format(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (m, k) =>
    Object.prototype.hasOwnProperty.call(params, k) ? String(params[k]) : m,
  );
}

/** Pure translation: current locale → fallback 'zh' → key. No hooks, no React. */
export function tFor(locale: Locale, key: string, params?: Record<string, string | number>): string {
  const cur = MESSAGES[locale]?.[key];
  if (cur !== undefined) return format(cur, params);
  if (locale !== "zh") {
    const fb = MESSAGES.zh?.[key];
    if (fb !== undefined) return format(fb, params);
  }
  return format(key, params);
}

interface I18nContextValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  locales: ReadonlyArray<Locale>;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("zh");

  // On mount (client only) sync from localStorage / navigator.
  useEffect(() => {
    setLocaleState(detectInitialLocale());
  }, []);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    try {
      if (typeof window !== "undefined") {
        window.localStorage.setItem(STORAGE_KEY, l);
      }
    } catch {
      // ignore storage failures
    }
  }, []);

  const value = useMemo(
    () => ({ locale, setLocale, locales: SUPPORTED_LOCALES }),
    [locale, setLocale],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

function useI18nContext(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) {
    // Defensive fallback when used outside I18nProvider (e.g. standalone tests).
    return {
      locale: "zh",
      setLocale: () => undefined,
      locales: SUPPORTED_LOCALES,
    };
  }
  return ctx;
}

/** Hook: returns a ``t(key, params?)`` function bound to the current locale. */
export function useT(): (key: string, params?: Record<string, string | number>) => string {
  const { locale } = useI18nContext();
  return useCallback(
    (key: string, params?: Record<string, string | number>) => tFor(locale, key, params),
    [locale],
  );
}

/** Hook: returns ``{ locale, setLocale, locales }``. */
export function useLocale(): I18nContextValue {
  return useI18nContext();
}
