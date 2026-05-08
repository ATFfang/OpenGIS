/**
 * useChatCodeTheme — shared Prism style provider for chat code blocks.
 *
 * Why this exists
 * ───────────────
 * Both `MarkdownBlock` (fenced code in assistant text) and `CodeStepRow`
 * (Python steps from CodeAgent) render syntax-highlighted code. They
 * used to hard-code `oneDark` regardless of the app theme, then forcibly
 * paint the background with `var(--bg-tertiary)`. In light mode that
 * becomes "saturated dark-theme tokens on a light-grey background",
 * which:
 *
 *   - kills contrast for low-luminance tokens (comments at #5c6370,
 *     punctuation at #abb2bf basically disappear on #f0f2f5),
 *   - makes the eye perceive a faint "shadow / blur / smear" because of
 *     the missing luminance step between fg and bg.
 *
 * Fix: pick the right base theme per app mode, and nudge the few
 * `oneLight` tokens that are still too pale on our particular light
 * surface (`--bg-tertiary = #f0f2f5`).
 *
 * Also enables crisp text rendering on Windows where small monospace
 * text on a light surface tends to look fuzzy by default.
 */

import { useEffect, useMemo, useState } from 'react'
import { oneDark, oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { useSettingsStore } from '@/stores/settingsStore'

// Crisp-text settings shared by both themes. Applied to the root
// <pre>/<code> node so children inherit. Important on Windows where
// the default sub-pixel AA on small mono text over light surfaces
// reads as "blurry".
const CRISP_TEXT_PROPS: Record<string, string> = {
  textShadow: 'none',
  WebkitFontSmoothing: 'antialiased',
  MozOsxFontSmoothing: 'grayscale',
  textRendering: 'optimizeLegibility',
  fontVariantLigatures: 'none',
}

// ─── Dark variant ────────────────────────────────────────────────
// Strip oneDark's default text-shadow (it bakes a 0 1px shadow on
// `code` which on our flat dark surface reads as a halo).
const darkBase = oneDark as Record<string, Record<string, string>>
const chatDarkTheme: Record<string, Record<string, string>> = {
  ...darkBase,
  'pre[class*="language-"]': {
    ...darkBase['pre[class*="language-"]'],
    background: 'transparent',
    margin: '0',
    padding: '0',
    ...CRISP_TEXT_PROPS,
  },
  'code[class*="language-"]': {
    ...darkBase['code[class*="language-"]'],
    background: 'transparent',
    ...CRISP_TEXT_PROPS,
  },
}

// ─── Light variant ───────────────────────────────────────────────
// oneLight is generally fine, but a few tokens are too pale on our
// `--bg-tertiary = #f0f2f5` surface. Bump them just enough to pass
// WCAG AA at 12px without changing the visual identity of the theme.
const lightBase = oneLight as Record<string, Record<string, string>>
const chatLightTheme: Record<string, Record<string, string>> = {
  ...lightBase,
  'pre[class*="language-"]': {
    ...lightBase['pre[class*="language-"]'],
    background: 'transparent',
    margin: '0',
    padding: '0',
    color: '#1f2328', // base text — darker than oneLight's #383a42
    ...CRISP_TEXT_PROPS,
  },
  'code[class*="language-"]': {
    ...lightBase['code[class*="language-"]'],
    background: 'transparent',
    color: '#1f2328',
    ...CRISP_TEXT_PROPS,
  },
  // Comments: oneLight uses #a0a1a7 italic — invisible on #f0f2f5.
  comment: {
    ...(lightBase.comment || {}),
    color: '#6a737d',
    fontStyle: 'italic',
  },
  prolog: { ...(lightBase.prolog || {}), color: '#6a737d' },
  doctype: { ...(lightBase.doctype || {}), color: '#6a737d' },
  cdata: { ...(lightBase.cdata || {}), color: '#6a737d' },
  // Punctuation / operators: oneLight uses #383a42 already, keep it.
  // Strings, numbers, keywords, functions — oneLight defaults are
  // strong enough; leave them untouched so the theme still looks like
  // oneLight rather than something we invented.
}

export type ChatCodeMode = 'dark' | 'light'

/**
 * Returns the active code-block theme + a mode flag, kept in sync with
 * the user's settings AND OS-level `prefers-color-scheme` changes when
 * theme === 'system'.
 */
export function useChatCodeTheme(): {
  style: Record<string, Record<string, string>>
  mode: ChatCodeMode
} {
  const theme = useSettingsStore((s) => s.appearance.theme)

  // Track system pref so 'system' theme reacts live without a reload.
  const [systemPrefersDark, setSystemPrefersDark] = useState<boolean>(() => {
    if (typeof window === 'undefined') return true
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  })

  useEffect(() => {
    if (theme !== 'system' || typeof window === 'undefined') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e: MediaQueryListEvent) => setSystemPrefersDark(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [theme])

  return useMemo(() => {
    const isDark =
      theme === 'dark' || (theme === 'system' && systemPrefersDark)
    return {
      style: isDark ? chatDarkTheme : chatLightTheme,
      mode: isDark ? 'dark' : 'light',
    }
  }, [theme, systemPrefersDark])
}
