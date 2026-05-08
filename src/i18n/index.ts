/**
 * i18n — Internationalization system for OpenGIS.
 *
 * Lightweight, zero-dependency i18n using zustand settings store.
 * Supports 'en' and 'zh' locales.
 */
import { useSettingsStore } from '@/stores/settingsStore'
import { en } from './en'
import { zh } from './zh'

export type Locale = 'en' | 'zh'
export type TranslationKeys = typeof en

const translations: Record<Locale, TranslationKeys> = { en, zh }

/**
 * Get the current translation object based on settings.
 * Use this hook in React components.
 */
export function useT(): TranslationKeys {
  const locale = useSettingsStore((s) => s.appearance.language)
  return translations[locale] ?? translations.en
}

/**
 * Get translation for a specific locale (non-reactive, for use outside React).
 */
export function getT(locale: Locale): TranslationKeys {
  return translations[locale] ?? translations.en
}

/**
 * Get current locale from store (non-reactive).
 */
export function getCurrentLocale(): Locale {
  return useSettingsStore.getState().appearance.language
}
