import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { paletteForPlugin } from '../theme-palettes.ts'
import { PALETTES } from './palettes.ts'
import { applyMode, modeSymbols, terminalMode, TERMINAL_MODES } from './modes.ts'

export { TERMINAL_MODES }
export const TERMINAL_PALETTES = ['catppuccin', 'ayu', 'github', 'vscode'] as const
export type TerminalPalette = (typeof TERMINAL_PALETTES)[number]
export const SLOTS = ['dark', 'light'] as const
export type Slot = (typeof SLOTS)[number]
export const VARIANTS = {
  catppuccin: { dark: ['mocha'], light: ['latte'] },
  ayu: { dark: ['dark', 'mirage'], light: ['light'] },
  github: { dark: ['dark', 'dimmed'], light: ['light'] },
  vscode: { dark: ['dark'], light: ['light'] },
} satisfies Record<TerminalPalette, Record<Slot, string[]>>

type PaletteVariant = (typeof PALETTES)['catppuccin']['mocha']
type PaletteRoles = PaletteVariant['roles']

export interface AppThemeSettings {
  pluginAppThemeDark?: string
  pluginAppThemeLight?: string
}

export interface SlotChoice {
  palette: TerminalPalette
  variant: string
}

export interface ThemeChoice {
  palette: string
  variant: string
  slot: string
}

export interface ModeThemeChoice extends ThemeChoice {
  mode: string
}

export interface SyncOptions {
  mode: string
  palette: string
  settings: AppThemeSettings | null
  ompThemesDir: string
  pluginDir: string
}

const PALETTE_VARIANTS = new Map<string, PaletteVariant>(
  Object.entries(PALETTES).flatMap(([palette, variants]) =>
    Object.entries(variants).map(([variant, data]) => [`${palette}/${variant}`, data]),
  ),
)

export function paletteVariant(palette: string, variant: string): PaletteVariant | undefined {
  return PALETTE_VARIANTS.get(`${palette}/${variant}`)
}

export function terminalPalette(value: string): TerminalPalette | undefined {
  return TERMINAL_PALETTES.find((palette) => palette === value)
}

export function terminalSlot(value: string): Slot | undefined {
  return SLOTS.find((slot) => slot === value)
}

export function slotVariants(palette: string, slot: string): string[] {
  const family = terminalPalette(palette)
  const known = terminalSlot(slot)
  return family && known ? VARIANTS[family][known] : []
}

function darkVariant(family: TerminalPalette, themeId: string): string | undefined {
  if (family === 'ayu' && themeId.includes('mirage')) return 'mirage'
  if (family === 'github' && themeId.includes('dimmed')) return 'dimmed'
  return undefined
}

interface ActiveTheme {
  palette: TerminalPalette | undefined
  themeId: string
}

function selectedTheme(settings: AppThemeSettings | null, slot: Slot): string {
  if (!settings) return ''
  return (slot === 'dark' ? settings.pluginAppThemeDark : settings.pluginAppThemeLight) ?? ''
}

function activeTheme(selected: string): ActiveTheme {
  const match = /^plugin:([^/]+)\/(.+)$/.exec(selected)
  if (!match) return { palette: undefined, themeId: '' }
  const [, pluginKey = '', themeId = ''] = match
  return { palette: terminalPalette(paletteForPlugin(pluginKey) ?? ''), themeId }
}

function slotChoice(palette: TerminalPalette | 'auto', settings: AppThemeSettings | null, slot: Slot): SlotChoice {
  const active = activeTheme(selectedTheme(settings, slot))
  const family = palette === 'auto' ? (active.palette ?? 'catppuccin') : palette
  const preferred = active.palette === family && slot === 'dark' ? darkVariant(family, active.themeId) : undefined
  return { palette: family, variant: preferred ?? VARIANTS[family][slot][0] ?? '' }
}

export function resolveSlots(palette: string, settings: AppThemeSettings | null) {
  const family = palette === 'auto' ? 'auto' : terminalPalette(palette)
  if (family === undefined) throw new Error(`Unknown terminal palette: ${palette}`)
  return { dark: slotChoice(family, settings, 'dark'), light: slotChoice(family, settings, 'light') }
}

function record(palette: string, variant: string, slot: string): PaletteVariant {
  const data = PALETTE_VARIANTS.get(`${palette}/${variant}`)
  if (!data || !slotVariants(palette, slot).includes(variant)) throw new Error(`Invalid terminal theme: ${palette}/${variant}/${slot}`)
  return data
}

export function appThemeFor(palette: string, variant: string): PaletteVariant['appTheme'] {
  const data = PALETTE_VARIANTS.get(`${palette}/${variant}`)
  if (!data) throw new Error(`Unknown terminal palette variant: ${palette}/${variant}`)
  return { ...data.appTheme }
}

export function buildOrcaTerminalTheme(choice: ThemeChoice) {
  const { roles, ansi } = record(choice.palette, choice.variant, choice.slot)
  return {
    schemaVersion: 1,
    mode: choice.slot,
    terminal: {
      background: roles.bg,
      foreground: roles.fg,
      cursor: roles.fg,
      selectionBackground: roles.surface1,
      ...ansi,
    },
  }
}

const hexByte = (color: string, index: number) => parseInt(color.slice(index, index + 2), 16)
const channels = (color: string): [number, number, number] => [hexByte(color, 1), hexByte(color, 3), hexByte(color, 5)]
const mixChannel = (from: number, to: number, amount: number) =>
  Math.round(from + (to - from) * amount).toString(16).padStart(2, '0')
function mix(a: string, b: string, amount: number): string {
  const [ar, ag, ab] = channels(a)
  const [br, bg, bb] = channels(b)
  return '#' + mixChannel(ar, br, amount) + mixChannel(ag, bg, amount) + mixChannel(ab, bb, amount)
}
function linear(value: number): number {
  const channel = value / 255
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
}
function luminance(color: string): number {
  const [r, g, b] = channels(color)
  return linear(r) * 0.2126 + linear(g) * 0.7152 + linear(b) * 0.0722
}
function contrast(a: string, b: string): number {
  const x = luminance(a), y = luminance(b)
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}
function readable(color: string, backgrounds: string[], slot: string, minimum = 4.5): string {
  if (backgrounds.every((bg) => contrast(color, bg) >= minimum)) return color
  const pole = slot === 'dark' ? '#ffffff' : '#000000'
  let low = 0, high = 1
  for (let step = 0; step < 16; step++) {
    const mid = (low + high) / 2
    if (backgrounds.every((bg) => contrast(mix(color, pole, mid), bg) >= minimum)) high = mid
    else low = mid
  }
  return mix(color, pole, high)
}

function themeRoles(roles: PaletteRoles, slot: string) {
  const thinking = mix(roles.subtle, roles.decision, 0.25)
  return {
    ...roles,
    successBg: mix(roles.bg, roles.suggestion, 0.07),
    errorBg: mix(roles.bg, roles.gap, 0.07),
    customBg: mix(roles.bg, roles.related, 0.08),
    thinking,
    quietSubtle: mix(roles.subtle, roles.bg, 0.15),
    quietThinking: mix(thinking, roles.bg, 0.15),
    dim: readable(mix(roles.subtle, roles.bg, 0.5), [roles.bg], slot, 3.5),
  }
}
type ThemeRoles = ReturnType<typeof themeRoles>

function baseColors(r: ThemeRoles) {
  return {
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
    syntaxComment: r.syntaxComment, syntaxKeyword: r.syntaxKeyword, syntaxFunction: r.syntaxFunction,
    syntaxVariable: r.syntaxVariable, syntaxString: r.syntaxString, syntaxNumber: r.syntaxNumber,
    syntaxType: r.syntaxType, syntaxOperator: r.syntaxOperator, syntaxPunctuation: r.syntaxPunctuation,
  }
}

const DECORATIVE = new Set(['border', 'borderAccent', 'borderMuted', 'mdHr', 'mdQuoteBorder', 'mdCodeBlockBorder', 'thinkingOff', 'thinkingMinimal', 'dim', 'statusLineSep'])
const TOOL_BACKGROUNDS = ['toolPendingBg', 'toolSuccessBg', 'toolErrorBg']
const TOOL_INKS = new Set(['toolOutput', 'toolTitle', 'muted'])

class ThemePaint {
  readonly colors: Map<string, string>
  readonly vars: Map<string, string>

  constructor(colors: Map<string, string>, vars: Map<string, string>) {
    this.colors = colors
    this.vars = vars
  }

  resolve(value: string): string {
    return this.vars.get(value) ?? value
  }

  color(token: string): string {
    return this.resolve(this.colors.get(token) ?? '')
  }

  backgroundsFor(token: string, base: string): string[] {
    if (token.startsWith('statusLine')) return [this.color('statusLineBg')]
    if (TOOL_INKS.has(token)) return [base, ...TOOL_BACKGROUNDS.map((key) => this.color(key))]
    if (token === 'customMessageLabel') return [base, this.color('customMessageBg'), this.color('userMessageBg')]
    return [base]
  }

  makeInksReadable(base: string, slot: string): void {
    for (const [token, value] of this.colors) {
      if (!value || token.endsWith('Bg') || DECORATIVE.has(token)) continue
      const adjusted = readable(this.resolve(value), this.backgroundsFor(token, base), slot)
      if (adjusted !== this.resolve(value)) this.colors.set(token, adjusted)
    }
  }

  keepForegroundReadable(foreground: string, base: string): void {
    for (const token of ['userMessageBg', 'selectedBg', 'customMessageBg']) {
      const background = this.color(token)
      if (contrast(foreground, background) >= 4.5) continue
      const candidate = Array.from({ length: 100 }, (_, index) => mix(background, base, (index + 1) / 100))
        .find((mixed) => contrast(foreground, mixed) >= 4.5)
      if (candidate) this.colors.set(token, candidate)
    }
  }
}

export function buildOmpTheme(choice: ModeThemeChoice) {
  const mode = terminalMode(choice.mode)
  if (!mode) throw new Error(`Unknown terminal mode: ${choice.mode}`)
  const r = themeRoles(record(choice.palette, choice.variant, choice.slot).roles, choice.slot)
  const vars = { base: r.bg, text: r.fg }
  const paint = new ThemePaint(new Map(Object.entries(baseColors(r))), new Map(Object.entries(vars)))
  applyMode(paint.colors, r, mode)
  paint.makeInksReadable(r.bg, choice.slot)
  paint.keepForegroundReadable(r.fg, r.bg)
  return {
    vars,
    colors: Object.fromEntries(paint.colors),
    export: { pageBg: r.bg, cardBg: r.mantle, infoBg: r.surface0 },
    name: `orca-sections-${choice.slot}`,
    symbols: modeSymbols(mode),
  }
}

function writeChanged(directory: string, name: string, bytes: string): string | undefined {
  const path = join(directory, name)
  if (existsSync(path) && readFileSync(path, 'utf8') === bytes) return undefined
  mkdirSync(directory, { recursive: true })
  writeFileSync(path, bytes)
  return path
}

const jsonBytes = (value: ReturnType<typeof buildOmpTheme> | ReturnType<typeof buildOrcaTerminalTheme>) =>
  JSON.stringify(value, null, 2) + '\n'

export function syncTerminalThemes(options: SyncOptions) {
  const slots = resolveSlots(options.palette, options.settings)
  const changed = { omp: new Array<string>(), orca: new Array<string>() }
  for (const slot of SLOTS) {
    const choice = { ...slots[slot], mode: options.mode, slot }
    const omp = writeChanged(options.ompThemesDir, `orca-sections-${slot}.json`, jsonBytes(buildOmpTheme(choice)))
    if (omp) changed.omp.push(omp)
    const orca = writeChanged(join(options.pluginDir, 'terminal'), `${slot}.json`, jsonBytes(buildOrcaTerminalTheme(choice)))
    if (orca) changed.orca.push(orca)
  }
  return { mode: options.mode, palette: options.palette, slots, changed }
}
