import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { PLUGIN_PALETTES } from '../theme-palettes.mjs'
import { PALETTES } from './palettes.mjs'
import { applyMode, TERMINAL_MODES } from './modes.mjs'

export { TERMINAL_MODES }
export const TERMINAL_PALETTES = ['catppuccin', 'ayu', 'github', 'vscode']
export const VARIANTS = {
  catppuccin: { dark: ['mocha'], light: ['latte'] },
  ayu: { dark: ['dark', 'mirage'], light: ['light'] },
  github: { dark: ['dark', 'dimmed'], light: ['light'] },
  vscode: { dark: ['dark'], light: ['light'] },
}

export function resolveSlots(palette, settings) {
  if (palette !== 'auto' && !TERMINAL_PALETTES.includes(palette)) throw new Error(`Unknown terminal palette: ${palette}`)
  return Object.fromEntries(['dark', 'light'].map(slot => {
    const selected = settings?.[slot === 'dark' ? 'pluginAppThemeDark' : 'pluginAppThemeLight']
    const match = /^plugin:([^/]+)\/(.+)$/.exec(selected ?? '')
    const active = match && PLUGIN_PALETTES[match[1]]
    const family = palette === 'auto' ? active || 'catppuccin' : palette
    let variant = VARIANTS[family][slot][0]
    if (active === family && slot === 'dark') {
      if (family === 'ayu' && match[2].includes('mirage')) variant = 'mirage'
      if (family === 'github' && match[2].includes('dimmed')) variant = 'dimmed'
    }
    return [slot, { palette: family, variant }]
  }))
}

function record(palette, variant, slot) {
  if (!VARIANTS[palette]?.[slot]?.includes(variant)) throw new Error(`Invalid terminal theme: ${palette}/${variant}/${slot}`)
  return PALETTES[palette][variant]
}

export function appThemeFor(palette, variant) {
  const data = PALETTES[palette]?.[variant]
  if (!data) throw new Error(`Unknown terminal palette variant: ${palette}/${variant}`)
  return { ...data.appTheme }
}

export function buildOrcaTerminalTheme({ palette, variant, slot }) {
  const { roles, ansi } = record(palette, variant, slot)
  return { schemaVersion: 1, mode: slot, terminal: {
    background: roles.bg, foreground: roles.fg, cursor: roles.fg,
    selectionBackground: roles.surface1, ...ansi,
  } }
}

const channels = color => [1, 3, 5].map(index => parseInt(color.slice(index, index + 2), 16))
function mix(a, b, amount) {
  const end = channels(b)
  return '#' + channels(a).map((value, index) => Math.round(value + (end[index] - value) * amount).toString(16).padStart(2, '0')).join('')
}
function luminance(color) {
  const values = channels(color).map(value => {
    const channel = value / 255
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  })
  return values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722
}
function contrast(a, b) {
  const x = luminance(a), y = luminance(b)
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}
// Preserve hue families, moving only failing text towards the slot's black/white pole.
function readable(color, backgrounds, slot, minimum = 4.5) {
  if (backgrounds.every(bg => contrast(color, bg) >= minimum)) return color
  const pole = slot === 'dark' ? '#ffffff' : '#000000'
  let low = 0, high = 1
  for (let step = 0; step < 16; step++) {
    const mid = (low + high) / 2
    if (backgrounds.every(bg => contrast(mix(color, pole, mid), bg) >= minimum)) high = mid
    else low = mid
  }
  return mix(color, pole, high)
}

export function buildOmpTheme({ palette, variant, mode, slot }) {
  if (!TERMINAL_MODES.includes(mode)) throw new Error(`Unknown terminal mode: ${mode}`)
  const data = record(palette, variant, slot)
  const r = { ...data.roles }
  r.successBg = mix(r.bg, r.suggestion, 0.07)
  r.errorBg = mix(r.bg, r.gap, 0.07)
  r.customBg = mix(r.bg, r.related, 0.08)
  r.thinking = mix(r.subtle, r.decision, 0.25)
  r.quietSubtle = mix(r.subtle, r.bg, 0.15)
  r.quietThinking = mix(r.thinking, r.bg, 0.15)
  // Counts, durations and ages are quiet text, not decorative border ink.
  r.dim = readable(mix(r.subtle, r.bg, 0.5), [r.bg], slot, 3.5)
  const colors = {
    accent: r.decision, border: r.surface1, borderAccent: r.key, borderMuted: r.surface0,
    success: r.suggestion, error: r.gap, warning: r.review, muted: r.subtle, dim: r.dim,
    text: '', thinkingText: r.thinking, selectedBg: r.surface0, userMessageBg: r.userMessage,
    customMessageBg: r.mantle, toolPendingBg: r.bg, toolSuccessBg: r.bg, toolErrorBg: r.bg,
    statusLineBg: r.mantle, userMessageText: '', customMessageText: '', customMessageLabel: r.related,
    toolTitle: r.code, toolOutput: r.subtle, assistantMessageText: '', mdHeading: r.accent,
    mdLink: r.key, mdLinkUrl: r.subtle, mdCode: r.code, mdCodeBlock: r.fg,
    mdCodeBlockBorder: r.code, mdQuote: r.subtle, mdQuoteBorder: r.surface2, mdHr: r.surface1,
    mdListBullet: r.accent, toolDiffAdded: r.suggestion, toolDiffRemoved: r.gap, toolDiffContext: r.subtle,
    thinkingOff: r.surface2, thinkingMinimal: r.surface1, thinkingLow: r.related, thinkingMedium: r.review,
    thinkingHigh: r.key, thinkingXhigh: r.gap, thinkingMax: r.decision, bashMode: r.suggestion,
    pythonMode: r.code, statusLineSep: r.surface2, statusLineModel: r.key, statusLinePath: r.related,
    statusLineGitClean: r.suggestion, statusLineGitDirty: r.decision, statusLineContext: r.review,
    statusLineSpend: r.command, statusLineStaged: r.suggestion, statusLineDirty: r.decision,
    statusLineUntracked: r.gap, statusLineOutput: r.fg, statusLineCost: r.decision, statusLineSubagents: r.key,
  }
  for (const key of ['Comment', 'Keyword', 'Function', 'Variable', 'String', 'Number', 'Type', 'Operator', 'Punctuation']) colors[`syntax${key}`] = r[`syntax${key}`]
  const theme = { vars: { base: r.bg, text: r.fg }, colors, export: { pageBg: r.bg, cardBg: r.mantle, infoBg: r.surface0 } }
  theme.name = `orca-sections-${slot}`
  applyMode(theme, r, mode)
  const decorative = new Set(['border', 'borderAccent', 'borderMuted', 'mdHr', 'mdQuoteBorder', 'mdCodeBlockBorder', 'thinkingOff', 'thinkingMinimal', 'dim', 'statusLineSep'])
  const resolve = value => theme.vars[value] ?? value
  for (const [token, value] of Object.entries(theme.colors)) {
    if (!value || token.endsWith('Bg') || decorative.has(token)) continue
    const backgrounds = token.startsWith('statusLine') ? [resolve(theme.colors.statusLineBg)] : [r.bg]
    if (token === 'toolOutput' || token === 'toolTitle' || token === 'muted') backgrounds.push(...['toolPendingBg', 'toolSuccessBg', 'toolErrorBg'].map(key => resolve(theme.colors[key])))
    if (token === 'customMessageLabel') backgrounds.push(resolve(theme.colors.customMessageBg), resolve(theme.colors.userMessageBg))
    const adjusted = readable(resolve(value), backgrounds, slot)
    if (adjusted !== resolve(value)) theme.colors[token] = adjusted
  }
  // Background fills may not diminish the app foreground's readability.
  for (const token of ['userMessageBg', 'selectedBg', 'customMessageBg']) {
    let background = resolve(theme.colors[token])
    if (contrast(r.fg, background) < 4.5) {
      for (let step = 1; step <= 100; step++) {
        const candidate = mix(background, r.bg, step / 100)
        if (contrast(r.fg, candidate) >= 4.5) { theme.colors[token] = candidate; break }
      }
    }
  }
  return theme
}

export function syncTerminalThemes({ mode, palette, settings, ompThemesDir, pluginDir }) {
  const slots = resolveSlots(palette, settings)
  const changed = { omp: [], orca: [] }
  for (const slot of ['dark', 'light']) {
    const options = { ...slots[slot], mode, slot }
    for (const [category, directory, file, value] of [
      ['omp', ompThemesDir, `orca-sections-${slot}.json`, buildOmpTheme(options)],
      ['orca', join(pluginDir, 'terminal'), `${slot}.json`, buildOrcaTerminalTheme(options)],
    ]) {
      const path = join(directory, file)
      const bytes = JSON.stringify(value, null, 2) + '\n'
      let previous
      try { previous = readFileSync(path, 'utf8') } catch (error) { if (error.code !== 'ENOENT') throw error }
      if (previous === bytes) continue
      mkdirSync(directory, { recursive: true })
      writeFileSync(path, bytes)
      changed[category].push(path)
    }
  }
  return { mode, palette, slots, changed }
}
