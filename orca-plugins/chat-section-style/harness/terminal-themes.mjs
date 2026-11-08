#!/usr/bin/env bun
// Usage: bun orca-plugins/chat-section-style/harness/terminal-themes.mjs
// Pure theme checks plus one isolated filesystem conformance case; never opens Orca's DB.
import fs from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { TERMINAL_MODES, TERMINAL_PALETTES, VARIANTS, appThemeFor, buildOmpTheme,
  buildOrcaTerminalTheme, resolveSlots, syncTerminalThemes } from '../terminal/build.mjs'
import { LOCAL_THEME_DIRS, PLUGIN_PALETTES, THEME_PLUGINS_DIR, readThemes } from '../theme-palettes.mjs'
import { PALETTES } from '../terminal/palettes.mjs'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
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
const hex = /^#[0-9a-f]{6}$/i
const object = (value) => Object.prototype.toString.call(value) === '[object Object]'
const canonical = (value) => hex.test(value) ? value.toLowerCase() : value
let failures = 0
const summaries = []
const fail = (context, token, message) => {
  failures++
  console.error(`FAIL ${context}/${token}: ${message}`)
}
const equal = (context, token, got, want) => {
  if (canonical(got) !== canonical(want)) fail(context, token, `${JSON.stringify(got)} != ${JSON.stringify(want)}`)
}
const attempt = (context, token, run) => {
  try { return run() } catch (error) { fail(context, token, error.message); return null }
}

function luminance(color) {
  const channels = [1, 3, 5].map((i) => {
    const value = parseInt(color.slice(i, i + 2), 16) / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
}

function contrast(a, b) {
  const x = luminance(a), y = luminance(b)
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

function oklab(color) {
  const [r, g, b] = [1, 3, 5].map(index => {
    const value = parseInt(color.slice(index, index + 2), 16) / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  // Björn Ottosson's published linear-sRGB → OKLab matrices:
  // https://bottosson.github.io/posts/oklab/#converting-from-linear-srgb-to-oklab
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
  ]
}

function checkAreas({ colors }, context) {
  const labs = new Map()
  for (const token of areaTokens) {
    if (!hex.test(colors[token])) {
      fail(context, token, `area color must resolve to non-empty #RRGGBB, got ${JSON.stringify(colors[token])}`)
    } else labs.set(token, oklab(colors[token]))
  }
  let minimum = Infinity
  for (let i = 0; i < areaTokens.length; i++) {
    const a = areaTokens[i], lab = labs.get(a)
    if (!lab) continue
    for (const b of areaTokens.slice(i + 1)) {
      const other = labs.get(b)
      if (!other) continue
      const distance = Math.hypot(...lab.map((value, index) => value - other[index]))
      minimum = Math.min(minimum, distance)
      if (distance < 0.05) fail(context, `${a}+${b}`, `OKLab distance ${distance.toFixed(6)} < 0.05`)
    }
  }
  return minimum
}

function checkOmp(theme, context, slot) {
  equal(context, 'name', theme.name, `orca-sections-${slot}`)
  if (!object(theme.colors)) fail(context, 'colors', 'must be an object')
  if (!object(theme.vars)) fail(context, 'vars', 'must be an object')
  const colors = object(theme.colors) ? theme.colors : {}
  const vars = object(theme.vars) ? theme.vars : {}
  for (const token of required) if (!Object.hasOwn(colors, token)) fail(context, token, 'missing color')
  for (const token of Object.keys(colors)) if (!allowed.has(token)) fail(context, token, 'unknown color')
  const resolveColor = (value, chain = []) => {
    if (Object.prototype.toString.call(value) !== '[object String]') throw new Error(`expected a color or variable, got ${JSON.stringify(value)}`)
    if (value === '' || hex.test(value)) return canonical(value)
    if (chain.includes(value)) throw new Error(`variable cycle ${[...chain, value].join(' -> ')}`)
    if (!Object.hasOwn(vars, value)) throw new Error(`missing variable ${value}`)
    return resolveColor(vars[value], [...chain, value])
  }
  const resolved = {}
  for (const [token, value] of Object.entries(colors)) {
    try { resolved[token] = resolveColor(value) } catch (error) { fail(context, token, error.message) }
  }
  const resolvedVars = {}
  for (const key of ['base', 'text']) {
    try {
      resolvedVars[key] = resolveColor(vars[key], [key])
      if (!hex.test(resolvedVars[key])) fail(context, `vars.${key}`, 'must resolve to #RRGGBB')
    } catch (error) { fail(context, `vars.${key}`, error.message) }
  }
  let minimum = Infinity
  const measure = (token, color, backgroundToken, background) => {
    if (color === '' || color === undefined) return
    if (!hex.test(background)) {
      fail(context, token, `${color} on ${backgroundToken} ${JSON.stringify(background)}: contrast unavailable`)
      return
    }
    const a = luminance(color), b = luminance(background)
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
    minimum = Math.min(minimum, ratio)
    if (ratio < 4.5) fail(context, token, `${color} on ${backgroundToken} ${background}: ${ratio.toFixed(2)}:1 < 4.5:1`)
  }
  for (const [token, color] of Object.entries(resolved)) {
    if (token.endsWith('Bg') || decorative.has(token)) continue
    if (token.startsWith('statusLine')) measure(token, color, 'statusLineBg', resolved.statusLineBg)
    else {
      measure(token, color, 'vars.base', resolvedVars.base)
      if (token === 'toolOutput') {
        for (const bg of ['toolPendingBg', 'toolSuccessBg', 'toolErrorBg']) measure(token, color, bg, resolved[bg])
      }
    }
  }
  for (const bg of ['userMessageBg', 'selectedBg', 'customMessageBg']) measure('vars.text', resolvedVars.text, bg, resolved[bg])
  if ([resolved.dim, resolved.muted, resolvedVars.base].every(color => hex.test(color))) {
    const dim = contrast(resolved.dim, resolvedVars.base)
    const muted = contrast(resolved.muted, resolvedVars.base)
    if (dim < 3) fail(context, 'dim', `${resolved.dim} on vars.base ${resolvedVars.base}: ${dim.toFixed(6)}:1 < 3.0:1`)
    if (!(dim < muted)) fail(context, 'dim', `contrast ${dim.toFixed(6)}:1 must be strictly lower than muted ${muted.toFixed(6)}:1`)
  } else fail(context, 'dim', 'dim/muted contrast against vars.base unavailable')
  return { colors: resolved, vars: resolvedVars, minimum }
}

function checkMode(theme, checked, context, mode, mantle) {
  const { colors, vars } = checked
  const symbols = theme.symbols?.overrides ?? {}
  const expectedBullet = { document: '•', 'split-cards': '•', gutter: '●', editorial: '◆' }[mode]
  equal(context, 'symbols.md.bullet', symbols['md.bullet'], expectedBullet)
  for (const token of ['toolPendingBg', 'toolSuccessBg', 'toolErrorBg']) {
    if (mode === 'split-cards') {
      if (colors[token] === vars.base) fail(context, token, 'split-cards must be filled, not base')
    } else equal(context, token, colors[token], vars.base)
  }
  if (mode === 'document') equal(context, 'customMessageBg', colors.customMessageBg, vars.base)
  if (mode === 'gutter') equal(context, 'customMessageBg', colors.customMessageBg, mantle)
  if (mode === 'split-cards') {
    if (colors.customMessageBg === vars.base) fail(context, 'customMessageBg', 'split-cards must differ from bg')
    if (colors.customMessageBg === canonical(mantle)) fail(context, 'customMessageBg', 'split-cards must differ from mantle')
  }
  if (mode === 'gutter' || mode === 'editorial') {
    for (const part of ['topLeft', 'topRight', 'bottomLeft', 'bottomRight', 'vertical']) {
      equal(context, `symbols.boxRound.${part}`, symbols[`boxRound.${part}`], ' ')
    }
    equal(context, 'symbols.boxRound.horizontal', symbols['boxRound.horizontal'], '─')
  } else {
    equal(context, 'symbols.boxRound.vertical', symbols['boxRound.vertical'], '│')
  }
  if (mode === 'document') {
    // Neutral frames share the border token; coloured embeds share the inline-code hue.
    equal(context, 'mdCodeBlockBorder', colors.mdCodeBlockBorder, colors.border)
    if (colors.mdCodeBlockBorder === colors.mdCode) fail(context, 'mdCodeBlockBorder', 'document frame must be neutral, not code hue')
    equal(context, 'symbols.md.hrChar', symbols['md.hrChar'], '─')
  } else {
    equal(context, 'mdCodeBlockBorder', colors.mdCodeBlockBorder, colors.mdCode)
    equal(context, 'mdListBullet', colors.mdListBullet, colors.mdHeading)
  }
  if (mode === 'split-cards') equal(context, 'borderAccent', colors.borderAccent, colors.accent)
  if (mode === 'editorial') {
    equal(context, 'symbols.md.quoteBorder', symbols['md.quoteBorder'], '▏')
    equal(context, 'symbols.md.hrChar', symbols['md.hrChar'], '─')
  }
}

const themes = attempt('all/all/all/all', 'readThemes', () => readThemes(fs, THEME_PLUGINS_DIR, LOCAL_THEME_DIRS, PLUGIN_PALETTES)) ?? []
for (const palette of TERMINAL_PALETTES) {
  let count = 0, minimum = Infinity, minimumPair = Infinity
  for (const slot of ['dark', 'light']) {
    for (const variant of VARIANTS[palette][slot]) {
      const checkedModes = new Map()
      for (const mode of TERMINAL_MODES) {
        const context = `${palette}/${variant}/${mode}/${slot}`
        const options = { palette, variant, mode, slot }
        const omp = attempt(context, 'buildOmpTheme', () => buildOmpTheme(options))
        const terminal = attempt(context, 'buildOrcaTerminalTheme', () => buildOrcaTerminalTheme(options))
        if (omp) {
          const checked = checkOmp(omp, context, slot)
          minimum = Math.min(minimum, checked.minimum)
          minimumPair = Math.min(minimumPair, checkAreas(checked, context))
          checkMode(omp, checked, context, mode, PALETTES[palette][variant].roles.mantle)
          checkedModes.set(mode, checked)
          count++
        }
        if (!terminal) continue
        equal(context, 'schemaVersion', terminal.schemaVersion, 1)
        equal(context, 'mode', terminal.mode, slot)
        const colors = terminal.terminal ?? {}
        for (const token of terminalKeys) if (!hex.test(colors[token])) fail(context, `terminal.${token}`, `expected #rrggbb, got ${JSON.stringify(colors[token])}`)
        for (const token of Object.keys(colors)) if (!terminalKeys.includes(token)) fail(context, `terminal.${token}`, 'unknown terminal color')
        const paired = attempt(context, 'appThemeFor', () => appThemeFor(palette, variant))
        if (!paired) continue
        const source = `${paired.pluginKey}/${paired.file}`
        const app = themes.find((theme) => theme.source === source)
        if (!app) { fail(context, 'appThemeFor', `paired app theme not installed: ${source}`); continue }
        for (const [token, appToken] of [['background', '--background'], ['foreground', '--foreground']]) {
          if (!hex.test(app.tokens[appToken])) fail(context, appToken, `invalid app color in ${source}`)
          equal(context, `terminal.${token}`, colors[token], app.tokens[appToken])
        }
      }
      const editorial = checkedModes.get('editorial'), gutter = checkedModes.get('gutter')
      if (editorial && gutter) {
        const context = `${palette}/${variant}/editorial/${slot}`
        if ([editorial.colors.muted, editorial.vars.base, gutter.colors.muted, gutter.vars.base].every(color => hex.test(color))) {
          const quiet = contrast(editorial.colors.muted, editorial.vars.base)
          const normal = contrast(gutter.colors.muted, gutter.vars.base)
          if (!(quiet < normal)) fail(context, 'muted', `contrast ${quiet.toFixed(6)}:1 must be strictly lower than gutter ${normal.toFixed(6)}:1`)
        } else fail(context, 'muted', 'editorial/gutter contrast unavailable')
      }
    }
  }
  summaries.push(`${palette}: ${count} themes, min contrast ${minimum.toFixed(2)}:1, min area distance ${minimumPair.toFixed(6)}`)
}

const defaults = { dark: { palette: 'catppuccin', variant: 'mocha' }, light: { palette: 'catppuccin', variant: 'latte' } }
const ayuSettings = { pluginAppThemeDark: 'plugin:stablyai.orca-ayu-theme/ayu-mirage',
  pluginAppThemeLight: 'plugin:stablyai.orca-github-vscode-theme/github-light-default' }
const slotCases = [
  ['auto', ayuSettings, { dark: { palette: 'ayu', variant: 'mirage' }, light: { palette: 'github', variant: 'light' } }],
  ['auto', { pluginAppThemeDark: 'plugin:local.catppuccin-sections/catppuccin-sections-mocha' }, defaults],
  ['auto', { pluginAppThemeDark: 'plugin:stablyai.orca-github-vscode-theme/github-dark-dimmed' }, { ...defaults, dark: { palette: 'github', variant: 'dimmed' } }],
  ['auto', { pluginAppThemeDark: 'plugin:unknown.plugin/ayu-mirage', pluginAppThemeLight: 'plugin:unknown.plugin/github-light-default' }, defaults],
  ['auto', null, defaults],
  ['vscode', ayuSettings, { dark: { palette: 'vscode', variant: 'dark' }, light: { palette: 'vscode', variant: 'light' } }],
  ['ayu', ayuSettings, { dark: { palette: 'ayu', variant: 'mirage' }, light: { palette: 'ayu', variant: 'light' } }],
  ['github', { pluginAppThemeDark: 'plugin:stablyai.orca-github-vscode-theme/github-dark-high-contrast',
    pluginAppThemeLight: 'plugin:stablyai.orca-github-vscode-theme/github-light-high-contrast' },
  { dark: { palette: 'github', variant: 'dark' }, light: { palette: 'github', variant: 'light' } }],
]
for (const [index, [palette, settings, want]] of slotCases.entries()) {
  const context = `${palette}/case-${index}/resolveSlots`
  const got = attempt(`${context}/both`, 'resolveSlots', () => resolveSlots(palette, settings))
  for (const slot of ['dark', 'light']) {
    for (const token of ['palette', 'variant']) equal(`${context}/${slot}`, token, got?.[slot]?.[token], want[slot][token])
  }
}

// Read contribution ids, not filenames: settings store manifest ids.
const manifests = []
for (const [pluginKey, directory] of Object.entries(LOCAL_THEME_DIRS)) {
  manifests.push([pluginKey, join(dirname(directory), 'orca-plugin.json')])
}
for (const pluginKey of Object.keys(PLUGIN_PALETTES).sort()) {
  const directory = join(THEME_PLUGINS_DIR, pluginKey)
  if (!fs.existsSync(directory)) continue
  for (const version of fs.readdirSync(directory).sort()) {
    const path = join(directory, version, 'orca-plugin.json')
    if (fs.existsSync(path)) manifests.push([pluginKey, path])
  }
}
for (const [pluginKey, path] of manifests) {
  const manifest = attempt('auto/installed/resolveSlots/both', pluginKey, () => JSON.parse(fs.readFileSync(path, 'utf8')))
  for (const theme of manifest?.contributes?.themes ?? []) {
    const palette = PLUGIN_PALETTES[pluginKey]
    const slot = /light|latte/.test(theme.id) ? 'light' : 'dark'
    const variant = palette === 'catppuccin' ? (slot === 'dark' ? 'mocha' : 'latte')
      : slot === 'light' ? 'light' : palette === 'ayu' && theme.id.includes('mirage') ? 'mirage'
      : palette === 'github' && theme.id.includes('dimmed') ? 'dimmed' : 'dark'
    const context = `${palette}/${variant}/resolveSlots/${slot}`
    const got = attempt(context, theme.id, () => resolveSlots('auto', { [slot === 'dark' ? 'pluginAppThemeDark' : 'pluginAppThemeLight']: `plugin:${pluginKey}/${theme.id}` }))
    equal(context, `${theme.id}.palette`, got?.[slot]?.palette, palette)
    equal(context, `${theme.id}.variant`, got?.[slot]?.variant, variant)
  }
}

// Only direct children of the top-level theme mapping count.
function parseThemeBlock(source) {
  const theme = {}
  let inTheme = false, childIndent
  for (const line of source.split(/\r?\n/)) {
    if (/^\s*(?:#.*)?$/.test(line)) continue
    if (!/^\s/.test(line)) {
      inTheme = /^theme:\s*(?:#.*)?$/.test(line)
      childIndent = undefined
      continue
    }
    if (!inTheme) continue
    const indent = line.match(/^\s*/)[0].length
    childIndent ??= indent
    if (indent !== childIndent) continue
    const match = line.match(/^\s+(dark|light):\s*(?:"([^"\n]*)"|'([^'\n]*)'|([^#\n]*?))\s*(?:#.*)?$/)
    if (match) theme[match[1]] = (match[2] ?? match[3] ?? match[4]).trim()
  }
  return { theme }
}
const config = attempt('all/all/config/both', 'agent/config.yml', () => parseThemeBlock(fs.readFileSync(join(root, 'agent/config.yml'), 'utf8')))
for (const slot of ['dark', 'light']) equal(`all/all/config/${slot}`, 'theme', config?.theme?.[slot], `orca-sections-${slot}`)

let temporary
try {
  temporary = fs.mkdtempSync(join(tmpdir(), 'orca-terminal-themes-'))
  const ompThemesDir = join(temporary, 'omp-themes'), pluginDir = join(temporary, 'plugin')
  const context = 'catppuccin/default/sync/both'
  const options = { mode: 'document', palette: 'catppuccin', settings: null, ompThemesDir, pluginDir }
  const expectedOmp = ['orca-sections-dark.json', 'orca-sections-light.json']
  const expectedOrca = ['terminal/dark.json', 'terminal/light.json']
  const checkChanged = (result, category, directory, expected, label) => {
    const paths = result?.changed?.[category]
    if (!Array.isArray(paths)) { fail(context, `${label}.changed.${category}`, 'must be an array'); return }
    const names = paths.map((path) => relative(directory, resolve(directory, path))).sort()
    equal(context, `${label}.changed.${category}`, JSON.stringify(names), JSON.stringify([...expected].sort()))
  }
  const first = await syncTerminalThemes(options)
  checkChanged(first, 'omp', ompThemesDir, expectedOmp, 'first')
  checkChanged(first, 'orca', pluginDir, expectedOrca, 'first')
  const bytes = new Map()
  for (const [directory, files] of [[ompThemesDir, expectedOmp], [pluginDir, expectedOrca]]) {
    for (const file of files) {
      const path = join(directory, file)
      const value = attempt(context, file, () => fs.readFileSync(path, 'utf8'))
      bytes.set(path, value)
      if (value !== null) attempt(context, file, () => JSON.parse(value))
    }
  }
  const second = await syncTerminalThemes(options)
  checkChanged(second, 'omp', ompThemesDir, [], 'identical')
  checkChanged(second, 'orca', pluginDir, [], 'identical')
  for (const [path, value] of bytes) equal(context, `identical.${relative(temporary, path)}`, fs.readFileSync(path, 'utf8'), value)
  const changed = await syncTerminalThemes({ ...options, mode: 'split-cards' })
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
} catch (error) { fail('catppuccin/default/sync/both', 'syncTerminalThemes', error.message) }
finally { if (temporary) fs.rmSync(temporary, { recursive: true, force: true }) }

if (!failures) for (const summary of summaries) console.log(summary)
process.exitCode = failures ? 1 : 0
