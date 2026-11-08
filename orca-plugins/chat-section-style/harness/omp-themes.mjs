#!/usr/bin/env bun
// Usage: bun orca-plugins/chat-section-style/harness/omp-themes.mjs [repo-root]
// Checks terminal theme tokens, Orca palette parity, WCAG text contrast and configured light/dark slots.
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(process.argv[2] ?? resolve(dirname(fileURLToPath(import.meta.url)), '../../..'))
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
const hex = /^#[0-9a-f]{6}$/i
const object = (value) => Object.prototype.toString.call(value) === '[object Object]'
let failures = 0
const fail = (message) => { failures++; console.log(`FAIL ${message}`) }

if (process.argv.length > 3) {
  console.log('Usage: bun orca-plugins/chat-section-style/harness/omp-themes.mjs [repo-root]')
  process.exit(1)
}

async function readJSON(path) {
  try {
    const value = JSON.parse(await Bun.file(resolve(root, path)).text())
    if (!object(value)) throw new Error('expected an object')
    return value
  } catch (error) { fail(`${path}: ${error.message}`); return null }
}

function luminance(color) {
  const channels = [1, 3, 5].map((i) => {
    const value = parseInt(color.slice(i, i + 2), 16) / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
}

for (const variant of ['mocha', 'latte']) {
  const name = `catppuccin-sections-${variant}`
  const before = failures
  const theme = await readJSON(`agent/themes/${name}.json`)
  const app = await readJSON(`orca-plugins/catppuccin-sections/themes/${variant}.json`)
  if (!theme) continue
  if (theme.name !== name) fail(`${name}: name is ${JSON.stringify(theme.name)}, want ${name}`)
  if (!object(theme.colors)) fail(`${name}: colors must be an object`)
  if (!object(theme.vars)) fail(`${name}: vars must be an object`)
  const colors = object(theme.colors) ? theme.colors : {}
  const vars = object(theme.vars) ? theme.vars : {}
  for (const token of required) if (!Object.hasOwn(colors, token)) fail(`${name}: missing color ${token}`)
  for (const token of Object.keys(colors)) if (!allowed.has(token)) fail(`${name}: unknown color ${token}`)

  const resolveColor = (value, chain = []) => {
    if (Object.prototype.toString.call(value) !== '[object String]') throw new Error(`expected a color or variable, got ${JSON.stringify(value)}`)
    if (value === '' || hex.test(value)) return value
    if (chain.includes(value)) throw new Error(`variable cycle ${[...chain, value].join(' -> ')}`)
    if (!Object.hasOwn(vars, value)) throw new Error(`missing variable ${value}`)
    return resolveColor(vars[value], [...chain, value])
  }
  const resolved = {}
  for (const [token, value] of Object.entries(colors)) {
    try { resolved[token] = resolveColor(value) } catch (error) { fail(`${name}: ${token}: ${error.message}`) }
  }
  const resolvedVars = {}
  for (const key of ['base', 'text']) {
    try {
      resolvedVars[key] = resolveColor(vars[key], [key])
      if (!hex.test(resolvedVars[key])) fail(`${name}: vars.${key} must resolve to #RRGGBB`)
    } catch (error) { fail(`${name}: vars.${key}: ${error.message}`) }
    const appToken = key === 'base' ? '--background' : '--foreground'
    if (app && (!hex.test(vars[key]) || !hex.test(app.tokens?.[appToken]) ||
        vars[key].toLowerCase() !== app.tokens[appToken].toLowerCase())) {
      fail(`${name}: vars.${key} ${JSON.stringify(vars[key])} != app ${appToken} ${JSON.stringify(app.tokens?.[appToken])}`)
    }
  }

  let minimum = Infinity
  const measure = (token, color, backgroundToken, background) => {
    if (color === '' || color === undefined) return
    if (!hex.test(background)) {
      fail(`${name}: ${token} ${color} on ${backgroundToken} ${JSON.stringify(background)}: contrast unavailable`)
      return
    }
    const a = luminance(color), b = luminance(background)
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
    minimum = Math.min(minimum, ratio)
    if (ratio < 4.5) fail(`${name}: ${token} ${color} on ${backgroundToken} ${background}: ${ratio.toFixed(2)}:1 < 4.5:1`)
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
  if (failures === before) console.log(`${name}: ${Object.keys(colors).length} tokens, min contrast ${minimum.toFixed(2)}`)
}

// Only direct children of the top-level theme mapping count; nested or unrelated dark/light keys do not.
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

let config
try {
  const source = await Bun.file(resolve(root, 'agent/config.yml')).text()
  config = Bun.YAML?.parse instanceof Function ? Bun.YAML.parse(source) : parseThemeBlock(source)
} catch (error) { fail(`agent/config.yml: ${error.message}`) }
const configBefore = failures
for (const [slot, variant] of [['dark', 'mocha'], ['light', 'latte']]) {
  const expected = `catppuccin-sections-${variant}`
  if (config?.theme?.[slot] !== expected) {
    fail(`agent/config.yml: theme.${slot} is ${JSON.stringify(config?.theme?.[slot]) ?? 'missing'}, want ${expected}`)
  }
}
if (failures === configBefore) console.log('agent/config.yml: theme.dark / theme.light ok')
process.exitCode = failures ? 1 : 0
