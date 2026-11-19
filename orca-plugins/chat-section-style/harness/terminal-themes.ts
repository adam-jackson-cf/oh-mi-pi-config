#!/usr/bin/env bun
import fs from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { dirname, join, relative, resolve } from 'node:path'
import { SLOTS, TERMINAL_MODES, TERMINAL_PALETTES, VARIANTS, appThemeFor, buildOmpTheme,
  buildOrcaTerminalTheme, paletteVariant, resolveSlots, syncTerminalThemes, type AppThemeSettings } from '../terminal/build.ts'
import { LOCAL_THEME_DIRS, PLUGIN_PALETTES, THEME_PLUGINS_DIR, paletteForPlugin, readThemes } from '../theme-palettes.ts'

type OmpTheme = ReturnType<typeof buildOmpTheme>
type SyncResult = ReturnType<typeof syncTerminalThemes>
type Comparable = string | number | null | undefined

interface CheckedTheme {
  colors: Map<string, string>
  vars: Map<string, string>
  minimum: number
}

interface PluginManifest {
  contributes?: { themes?: { id: string }[] }
}

interface SlotCase {
  palette: string
  settings: AppThemeSettings | null
  want: Record<'dark' | 'light', { palette: string; variant: string }>
}

const required = new Set(`accent border borderAccent borderMuted success error warning muted dim text thinkingText
selectedBg userMessageBg customMessageBg toolPendingBg toolSuccessBg toolErrorBg statusLineBg userMessageText
customMessageText customMessageLabel toolTitle toolOutput mdHeading mdLink mdLinkUrl mdCode mdCodeBlock
mdCodeBlockBorder mdQuote mdQuoteBorder mdHr mdListBullet toolDiffAdded toolDiffRemoved toolDiffContext
syntaxComment syntaxKeyword syntaxFunction syntaxVariable syntaxString syntaxNumber syntaxType syntaxOperator
syntaxPunctuation thinkingOff thinkingMinimal thinkingLow thinkingMedium thinkingHigh thinkingXhigh bashMode pythonMode
statusLineSep statusLineModel statusLinePath statusLineGitClean statusLineGitDirty statusLineContext statusLineSpend
statusLineStaged statusLineDirty statusLineUntracked statusLineOutput statusLineCost statusLineSubagents`.split(/\s+/))
const allowed = new Set([...required, 'thinkingMax', 'assistantMessageText'])
const decorative = new Set(['border', 'borderAccent', 'borderMuted', 'mdHr', 'mdQuoteBorder', 'mdCodeBlockBorder',
  'thinkingOff', 'thinkingMinimal', 'dim', 'statusLineSep'])
const terminalKeys = `background foreground cursor selectionBackground black red green yellow blue magenta cyan white
brightBlack brightRed brightGreen brightYellow brightBlue brightMagenta brightCyan brightWhite`.split(/\s+/)
const areaTokens = ['mdHeading', 'accent', 'toolTitle', 'muted', 'thinkingText',
  'success', 'error', 'warning', 'customMessageLabel']
const bullets = new Map(Object.entries({ document: '•', 'split-cards': '•', gutter: '●', editorial: '◆' }))
const hex = /^#[0-9a-f]{6}$/i
const isHex = (value: Comparable) => hex.test(String(value))
const canonical = (value: Comparable) => (isHex(value) ? String(value).toLowerCase() : value)
let failures = 0
const summaries: string[] = []
const fail = (context: string, token: string, message: string) => {
  failures++
  console.error(`FAIL ${context}/${token}: ${message}`)
}
const equal = (context: string, token: string, got: Comparable, want: Comparable) => {
  if (canonical(got) !== canonical(want)) fail(context, token, `${JSON.stringify(got)} != ${JSON.stringify(want)}`)
}
const attempt = <Value>(context: string, token: string, run: () => Value): Value | null => {
  try { return run() } catch (error) { fail(context, token, error instanceof Error ? error.message : String(error)); return null }
}

function linearChannels(color: string): [number, number, number] {
  const linear = (index: number) => {
    const value = parseInt(color.slice(index, index + 2), 16) / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  }
  return [linear(1), linear(3), linear(5)]
}

function luminance(color: string): number {
  const [r, g, b] = linearChannels(color)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a: string, b: string): number {
  const x = luminance(a), y = luminance(b)
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

function oklab(color: string): [number, number, number] {
  const [r, g, b] = linearChannels(color)
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
  ]
}

function checkAreas(checked: CheckedTheme, context: string): number {
  const labs = new Map<string, [number, number, number]>()
  for (const token of areaTokens) {
    const color = checked.colors.get(token)
    if (!color || !hex.test(color)) {
      fail(context, token, `area color must resolve to non-empty #RRGGBB, got ${JSON.stringify(color)}`)
    } else labs.set(token, oklab(color))
  }
  let minimum = Infinity
  for (const [index, a] of areaTokens.entries()) {
    for (const b of areaTokens.slice(index + 1)) {
      const lab = labs.get(a), other = labs.get(b)
      if (!lab || !other) continue
      const distance = Math.hypot(lab[0] - other[0], lab[1] - other[1], lab[2] - other[2])
      minimum = Math.min(minimum, distance)
      if (distance < 0.05) fail(context, `${a}+${b}`, `OKLab distance ${distance.toFixed(6)} < 0.05`)
    }
  }
  return minimum
}

function resolveColor(vars: Map<string, string>, value: string, chain: string[] = []): string {
  if (value === '' || hex.test(value)) return value.toLowerCase()
  if (chain.includes(value)) throw new Error(`variable cycle ${[...chain, value].join(' -> ')}`)
  const next = vars.get(value)
  if (next === undefined) throw new Error(`missing variable ${value}`)
  return resolveColor(vars, next, [...chain, value])
}

function resolveTheme(theme: OmpTheme, context: string) {
  const vars = new Map(Object.entries(theme.vars))
  const colors = new Map<string, string>()
  for (const [token, value] of Object.entries(theme.colors)) {
    try { colors.set(token, resolveColor(vars, value)) } catch (error) { fail(context, token, String(error)) }
  }
  const resolvedVars = new Map<string, string>()
  for (const key of ['base', 'text']) {
    try {
      const color = resolveColor(vars, vars.get(key) ?? '', [key])
      resolvedVars.set(key, color)
      if (!hex.test(color)) fail(context, `vars.${key}`, 'must resolve to #RRGGBB')
    } catch (error) { fail(context, `vars.${key}`, String(error)) }
  }
  return { colors, vars: resolvedVars }
}

class ContrastMeter {
  minimum = Infinity
  readonly context: string

  constructor(context: string) {
    this.context = context
  }

  measure(token: string, color: string | undefined, backgroundToken: string, background: string | undefined): void {
    if (color === '' || color === undefined) return
    if (!background || !hex.test(background)) {
      fail(this.context, token, `${color} on ${backgroundToken} ${JSON.stringify(background)}: contrast unavailable`)
      return
    }
    const ratio = contrast(color, background)
    this.minimum = Math.min(this.minimum, ratio)
    if (ratio < 4.5) fail(this.context, token, `${color} on ${backgroundToken} ${background}: ${ratio.toFixed(2)}:1 < 4.5:1`)
  }
}

function measureInks(meter: ContrastMeter, colors: Map<string, string>, vars: Map<string, string>): void {
  for (const [token, color] of colors) {
    if (token.endsWith('Bg') || decorative.has(token)) continue
    if (token.startsWith('statusLine')) {
      meter.measure(token, color, 'statusLineBg', colors.get('statusLineBg'))
      continue
    }
    meter.measure(token, color, 'vars.base', vars.get('base'))
    if (token !== 'toolOutput') continue
    for (const bg of ['toolPendingBg', 'toolSuccessBg', 'toolErrorBg']) meter.measure(token, color, bg, colors.get(bg))
  }
  for (const bg of ['userMessageBg', 'selectedBg', 'customMessageBg']) meter.measure('vars.text', vars.get('text'), bg, colors.get(bg))
}

function checkDim(context: string, colors: Map<string, string>, vars: Map<string, string>): void {
  const dim = colors.get('dim') ?? '', muted = colors.get('muted') ?? '', base = vars.get('base') ?? ''
  if (![dim, muted, base].every((color) => hex.test(color))) {
    fail(context, 'dim', 'dim/muted contrast against vars.base unavailable')
    return
  }
  const dimRatio = contrast(dim, base)
  const mutedRatio = contrast(muted, base)
  if (dimRatio < 3) fail(context, 'dim', `${dim} on vars.base ${base}: ${dimRatio.toFixed(6)}:1 < 3.0:1`)
  if (!(dimRatio < mutedRatio)) fail(context, 'dim', `contrast ${dimRatio.toFixed(6)}:1 must be strictly lower than muted ${mutedRatio.toFixed(6)}:1`)
}

function checkOmp(theme: OmpTheme, context: string, slot: string): CheckedTheme {
  equal(context, 'name', theme.name, `orca-sections-${slot}`)
  for (const token of required) if (!Object.hasOwn(theme.colors, token)) fail(context, token, 'missing color')
  for (const token of Object.keys(theme.colors)) if (!allowed.has(token)) fail(context, token, 'unknown color')
  const { colors, vars } = resolveTheme(theme, context)
  const meter = new ContrastMeter(context)
  measureInks(meter, colors, vars)
  checkDim(context, colors, vars)
  return { colors, vars, minimum: meter.minimum }
}

function checkModeFills(checked: CheckedTheme, context: string, mode: string, mantle: string): void {
  const { colors, vars } = checked
  for (const token of ['toolPendingBg', 'toolSuccessBg', 'toolErrorBg']) {
    if (mode !== 'split-cards') equal(context, token, colors.get(token), vars.get('base'))
    else if (colors.get(token) === vars.get('base')) fail(context, token, 'split-cards must be filled, not base')
  }
  if (mode === 'document') equal(context, 'customMessageBg', colors.get('customMessageBg'), vars.get('base'))
  if (mode === 'gutter') equal(context, 'customMessageBg', colors.get('customMessageBg'), mantle)
  if (mode !== 'split-cards') return
  if (colors.get('customMessageBg') === vars.get('base')) fail(context, 'customMessageBg', 'split-cards must differ from bg')
  if (colors.get('customMessageBg') === canonical(mantle)) fail(context, 'customMessageBg', 'split-cards must differ from mantle')
  equal(context, 'borderAccent', colors.get('borderAccent'), colors.get('accent'))
}

function checkModeFrames(symbols: Map<string, string>, context: string, mode: string): void {
  if (mode === 'gutter' || mode === 'editorial') {
    for (const part of ['topLeft', 'topRight', 'bottomLeft', 'bottomRight', 'vertical']) {
      equal(context, `symbols.boxRound.${part}`, symbols.get(`boxRound.${part}`), ' ')
    }
    equal(context, 'symbols.boxRound.horizontal', symbols.get('boxRound.horizontal'), '─')
  } else {
    equal(context, 'symbols.boxRound.vertical', symbols.get('boxRound.vertical'), '│')
  }
  if (mode === 'editorial') {
    equal(context, 'symbols.md.quoteBorder', symbols.get('md.quoteBorder'), '▏')
    equal(context, 'symbols.md.hrChar', symbols.get('md.hrChar'), '─')
  }
}

function checkModeMarkdown(checked: CheckedTheme, symbols: Map<string, string>, context: string, mode: string): void {
  const { colors } = checked
  if (mode !== 'document') {
    equal(context, 'mdCodeBlockBorder', colors.get('mdCodeBlockBorder'), colors.get('mdCode'))
    equal(context, 'mdListBullet', colors.get('mdListBullet'), colors.get('mdHeading'))
    return
  }
  equal(context, 'mdCodeBlockBorder', colors.get('mdCodeBlockBorder'), colors.get('border'))
  if (colors.get('mdCodeBlockBorder') === colors.get('mdCode')) fail(context, 'mdCodeBlockBorder', 'document frame must be neutral, not code hue')
  equal(context, 'symbols.md.hrChar', symbols.get('md.hrChar'), '─')
}

function checkMode(theme: OmpTheme, checked: CheckedTheme, context: string, mode: string, mantle: string): void {
  const symbols = new Map(Object.entries(theme.symbols.overrides))
  equal(context, 'symbols.md.bullet', symbols.get('md.bullet'), bullets.get(mode))
  checkModeFills(checked, context, mode, mantle)
  checkModeFrames(symbols, context, mode)
  checkModeMarkdown(checked, symbols, context, mode)
}

const themes = attempt('all/all/all/all', 'readThemes', () => readThemes(fs, THEME_PLUGINS_DIR, LOCAL_THEME_DIRS, PLUGIN_PALETTES)) ?? []

function checkTerminal(context: string, slot: string, palette: string, variant: string): void {
  const terminal = attempt(context, 'buildOrcaTerminalTheme', () => buildOrcaTerminalTheme({ palette, variant, slot }))
  if (!terminal) return
  equal(context, 'schemaVersion', terminal.schemaVersion, 1)
  equal(context, 'mode', terminal.mode, slot)
  const colors = new Map(Object.entries(terminal.terminal))
  for (const token of terminalKeys) if (!isHex(colors.get(token))) fail(context, `terminal.${token}`, `expected #rrggbb, got ${JSON.stringify(colors.get(token))}`)
  for (const token of colors.keys()) if (!terminalKeys.includes(token)) fail(context, `terminal.${token}`, 'unknown terminal color')
  const paired = attempt(context, 'appThemeFor', () => appThemeFor(palette, variant))
  if (!paired) return
  const source = `${paired.pluginKey}/${paired.file}`
  const app = themes.find((theme) => theme.source === source)
  if (!app) { fail(context, 'appThemeFor', `paired app theme not installed: ${source}`); return }
  for (const [token, appToken] of [['background', '--background'], ['foreground', '--foreground']] as const) {
    if (!isHex(app.tokens[appToken])) fail(context, appToken, `invalid app color in ${source}`)
    equal(context, `terminal.${token}`, colors.get(token), app.tokens[appToken])
  }
}

function checkQuietEditorial(context: string, editorial: CheckedTheme, gutter: CheckedTheme): void {
  const quietInk = editorial.colors.get('muted') ?? '', quietBase = editorial.vars.get('base') ?? ''
  const normalInk = gutter.colors.get('muted') ?? '', normalBase = gutter.vars.get('base') ?? ''
  if (![quietInk, quietBase, normalInk, normalBase].every((color) => hex.test(color))) {
    fail(context, 'muted', 'editorial/gutter contrast unavailable')
    return
  }
  const quiet = contrast(quietInk, quietBase)
  const normal = contrast(normalInk, normalBase)
  if (!(quiet < normal)) fail(context, 'muted', `contrast ${quiet.toFixed(6)}:1 must be strictly lower than gutter ${normal.toFixed(6)}:1`)
}

interface PaletteTally {
  count: number
  minimum: number
  minimumPair: number
}

function checkVariant(palette: string, variant: string, slot: string, tally: PaletteTally): void {
  const checkedModes = new Map<string, CheckedTheme>()
  for (const mode of TERMINAL_MODES) {
    const context = `${palette}/${variant}/${mode}/${slot}`
    const omp = attempt(context, 'buildOmpTheme', () => buildOmpTheme({ palette, variant, mode, slot }))
    if (omp) {
      const checked = checkOmp(omp, context, slot)
      tally.minimum = Math.min(tally.minimum, checked.minimum)
      tally.minimumPair = Math.min(tally.minimumPair, checkAreas(checked, context))
      checkMode(omp, checked, context, mode, paletteVariant(palette, variant)?.roles.mantle ?? '')
      checkedModes.set(mode, checked)
      tally.count++
    }
    checkTerminal(context, slot, palette, variant)
  }
  const editorial = checkedModes.get('editorial'), gutter = checkedModes.get('gutter')
  if (editorial && gutter) checkQuietEditorial(`${palette}/${variant}/editorial/${slot}`, editorial, gutter)
}

for (const palette of TERMINAL_PALETTES) {
  const tally = { count: 0, minimum: Infinity, minimumPair: Infinity }
  for (const slot of SLOTS) {
    for (const variant of VARIANTS[palette][slot]) checkVariant(palette, variant, slot, tally)
  }
  summaries.push(`${palette}: ${tally.count} themes, min contrast ${tally.minimum.toFixed(2)}:1, min area distance ${tally.minimumPair.toFixed(6)}`)
}

const defaults = { dark: { palette: 'catppuccin', variant: 'mocha' }, light: { palette: 'catppuccin', variant: 'latte' } }
const ayuSettings = { pluginAppThemeDark: 'plugin:stablyai.orca-ayu-theme/ayu-mirage',
  pluginAppThemeLight: 'plugin:stablyai.orca-github-vscode-theme/github-light-default' }
const slotCases: SlotCase[] = [
  { palette: 'auto', settings: ayuSettings, want: { dark: { palette: 'ayu', variant: 'mirage' }, light: { palette: 'github', variant: 'light' } } },
  { palette: 'auto', settings: { pluginAppThemeDark: 'plugin:local.catppuccin-sections/catppuccin-sections-mocha' }, want: defaults },
  { palette: 'auto', settings: { pluginAppThemeDark: 'plugin:stablyai.orca-github-vscode-theme/github-dark-dimmed' }, want: { ...defaults, dark: { palette: 'github', variant: 'dimmed' } } },
  { palette: 'auto', settings: { pluginAppThemeDark: 'plugin:unknown.plugin/ayu-mirage', pluginAppThemeLight: 'plugin:unknown.plugin/github-light-default' }, want: defaults },
  { palette: 'auto', settings: null, want: defaults },
  { palette: 'vscode', settings: ayuSettings, want: { dark: { palette: 'vscode', variant: 'dark' }, light: { palette: 'vscode', variant: 'light' } } },
  { palette: 'ayu', settings: ayuSettings, want: { dark: { palette: 'ayu', variant: 'mirage' }, light: { palette: 'ayu', variant: 'light' } } },
  { palette: 'github', settings: { pluginAppThemeDark: 'plugin:stablyai.orca-github-vscode-theme/github-dark-high-contrast',
    pluginAppThemeLight: 'plugin:stablyai.orca-github-vscode-theme/github-light-high-contrast' },
  want: { dark: { palette: 'github', variant: 'dark' }, light: { palette: 'github', variant: 'light' } } },
]
for (const [index, { palette, settings, want }] of slotCases.entries()) {
  const context = `${palette}/case-${index}/resolveSlots`
  const got = attempt(`${context}/both`, 'resolveSlots', () => resolveSlots(palette, settings))
  for (const slot of SLOTS) {
    for (const token of ['palette', 'variant'] as const) equal(`${context}/${slot}`, token, got?.[slot][token], want[slot][token])
  }
}

const manifests: [string, string][] = []
for (const [pluginKey, directory] of Object.entries(LOCAL_THEME_DIRS)) {
  manifests.push([pluginKey, join(dirname(directory), 'orca-plugin.json')])
}
for (const pluginKey of Object.keys(PLUGIN_PALETTES).toSorted()) {
  const directory = join(THEME_PLUGINS_DIR, pluginKey)
  if (!fs.existsSync(directory)) continue
  for (const version of fs.readdirSync(directory).toSorted()) {
    const path = join(directory, version, 'orca-plugin.json')
    if (fs.existsSync(path)) manifests.push([pluginKey, path])
  }
}

function expectedVariant(palette: string, slot: string, themeId: string): string {
  if (palette === 'catppuccin') return slot === 'dark' ? 'mocha' : 'latte'
  if (slot === 'light') return 'light'
  if (palette === 'ayu' && themeId.includes('mirage')) return 'mirage'
  return palette === 'github' && themeId.includes('dimmed') ? 'dimmed' : 'dark'
}

for (const [pluginKey, path] of manifests) {
  const manifest = attempt<PluginManifest>('auto/installed/resolveSlots/both', pluginKey, () => JSON.parse(fs.readFileSync(path, 'utf8')))
  for (const theme of manifest?.contributes?.themes ?? []) {
    const palette = paletteForPlugin(pluginKey) ?? ''
    const slot = /light|latte/.test(theme.id) ? 'light' : 'dark'
    const variant = expectedVariant(palette, slot, theme.id)
    const context = `${palette}/${variant}/resolveSlots/${slot}`
    const got = attempt(context, theme.id, () => resolveSlots('auto', { [slot === 'dark' ? 'pluginAppThemeDark' : 'pluginAppThemeLight']: `plugin:${pluginKey}/${theme.id}` }))
    equal(context, `${theme.id}.palette`, got?.[slot].palette, palette)
    equal(context, `${theme.id}.variant`, got?.[slot].variant, variant)
  }
}

function themeEntry(line: string): [string, string] | undefined {
  const match = line.match(/^\s+(dark|light):\s*(?:"([^"\n]*)"|'([^'\n]*)'|([^#\n]*?))\s*(?:#.*)?$/)
  if (!match?.[1]) return undefined
  return [match[1], (match[2] ?? match[3] ?? match[4] ?? '').trim()]
}

function parseThemeBlock(source: string) {
  const theme = new Map<string, string>()
  let inTheme = false
  let childIndent: number | undefined
  for (const line of source.split(/\r?\n/)) {
    if (/^\s*(?:#.*)?$/.test(line)) continue
    if (!/^\s/.test(line)) {
      inTheme = /^theme:\s*(?:#.*)?$/.test(line)
      childIndent = undefined
      continue
    }
    const indent = line.length - line.trimStart().length
    childIndent ??= inTheme ? indent : undefined
    if (!inTheme || indent !== childIndent) continue
    const entry = themeEntry(line)
    if (entry) theme.set(...entry)
  }
  return { theme }
}
const configPath = join(homedir(), '.omp/agent/config.yml')
const config = attempt('all/all/config/both', configPath, () => parseThemeBlock(fs.readFileSync(configPath, 'utf8')))
for (const slot of SLOTS) equal(`all/all/config/${slot}`, 'theme', config?.theme.get(slot), `orca-sections-${slot}`)

function checkChanged(result: SyncResult, category: 'omp' | 'orca', directory: string, expected: string[], label: string): void {
  const names = result.changed[category].map((path) => relative(directory, resolve(directory, path))).toSorted()
  equal(context, `${label}.changed.${category}`, JSON.stringify(names), JSON.stringify(expected.toSorted()))
}

const context = 'catppuccin/default/sync/both'
let temporary: string | undefined
try {
  temporary = fs.mkdtempSync(join(tmpdir(), 'orca-terminal-themes-'))
  const ompThemesDir = join(temporary, 'omp-themes'), pluginDir = join(temporary, 'plugin')
  const options = { mode: 'document', palette: 'catppuccin', settings: null, ompThemesDir, pluginDir }
  const expectedOmp = ['orca-sections-dark.json', 'orca-sections-light.json']
  const expectedOrca = ['terminal/dark.json', 'terminal/light.json']
  const first = syncTerminalThemes(options)
  checkChanged(first, 'omp', ompThemesDir, expectedOmp, 'first')
  checkChanged(first, 'orca', pluginDir, expectedOrca, 'first')
  const bytes = new Map<string, string | null>()
  for (const [directory, files] of [[ompThemesDir, expectedOmp], [pluginDir, expectedOrca]] as const) {
    for (const file of files) {
      const path = join(directory, file)
      const value = attempt(context, file, () => fs.readFileSync(path, 'utf8'))
      bytes.set(path, value)
      if (value !== null) attempt(context, file, () => JSON.parse(value))
    }
  }
  const second = syncTerminalThemes(options)
  checkChanged(second, 'omp', ompThemesDir, [], 'identical')
  checkChanged(second, 'orca', pluginDir, [], 'identical')
  for (const [path, value] of bytes) equal(context, `identical.${relative(temporary, path)}`, fs.readFileSync(path, 'utf8'), value)
  const changed = syncTerminalThemes({ ...options, mode: 'split-cards' })
  checkChanged(changed, 'omp', ompThemesDir, expectedOmp, 'mode-change')
  checkChanged(changed, 'orca', pluginDir, [], 'mode-change')
  for (const file of expectedOmp) {
    const path = join(ompThemesDir, file)
    if (fs.readFileSync(path, 'utf8') === bytes.get(path)) fail(context, `mode-change.${file}`, 'omp bytes did not change')
  }
  for (const file of expectedOrca) {
    const path = join(pluginDir, file)
    equal(context, `mode-change.${file}`, fs.readFileSync(path, 'utf8'), bytes.get(path))
  }
} catch (error) { fail(context, 'syncTerminalThemes', String(error)) }
finally { if (temporary) fs.rmSync(temporary, { recursive: true, force: true }) }

if (!failures) for (const summary of summaries) console.log(summary)
process.exitCode = failures ? 1 : 0
