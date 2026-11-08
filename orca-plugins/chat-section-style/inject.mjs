#!/usr/bin/env node
// Installs chat.css, palettes/*.css, id-highlight.js and palette-auto.js into Orca's renderer through the
// main-process inspector. Applies layout from `mode` and colour family from `palette` as
// <html data-omp-chat-mode data-omp-chat-palette>. Palette `auto` follows the active Orca theme.
//
// Orca has no custom-CSS setting. Its Electron build keeps the EnableNodeCliInspectArguments fuse on,
// so SIGUSR1 opens the main-process inspector on 127.0.0.1:9229. This script connects, installs a
// hook that re-applies these files after renderer reloads and edits, then closes the port.
//
// Usage: node inject.mjs               install now (or refresh an existing install)
//        node inject.mjs --if-needed   skip when this Orca process already has the hook (for launchd)
//        node inject.mjs --mode NAME   switch layout mode; a hooked Orca picks it up within a second
//        node inject.mjs --palette NAME switch palette (a palette name or auto); combinable with --mode NAME
//        node inject.mjs --surface chat|terminal|both --mode NAME --palette NAME
//        Choices default to both surfaces; chat-only changes pin the terminal's current choice.

import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync, readdirSync, unlinkSync, writeFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { LOCAL_THEME_DIRS, PLUGIN_PALETTES, THEME_PLUGINS_DIR, readThemes } from './theme-palettes.mjs'
import { syncTerminalThemes } from './terminal/build.mjs'

const ORCA_BINARY = '/Applications/Orca.app/Contents/MacOS/Orca'
const INSPECTOR_LIST_URL = 'http://127.0.0.1:9229/json/list'
const STYLE_DIR = dirname(fileURLToPath(import.meta.url))
const CSS_PATH = join(STYLE_DIR, 'chat.css')
const SCRIPT_PATH = join(STYLE_DIR, 'id-highlight.js')
const MODE_PATH = join(STYLE_DIR, 'mode')
const MODES = ['document', 'split-cards', 'gutter', 'editorial']
const PALETTE_PATH = join(STYLE_DIR, 'palette')
const PALETTE_CSS_PATHS = readdirSync(join(STYLE_DIR, 'palettes')).filter(name => name.endsWith('.css')).sort().map(name => join(STYLE_DIR, 'palettes', name))
const PALETTES = PALETTE_CSS_PATHS.map(path => path.slice(path.lastIndexOf('/') + 1, -4))
const PALETTE_CHOICES = [...PALETTES, 'auto']
const AUTO_PATH = join(STYLE_DIR, 'palette-auto.js')
const MARKER_PATH = join(tmpdir(), 'orca-chat-section-style.pid')

function findOrcaMainPid() {
  const rows = execFileSync('ps', ['-axo', 'pid=,ppid=,comm=']).toString().split('\n')
  for (const row of rows) {
    const [pid, ppid, comm] = row.trim().split(/\s+/)
    if (ppid === '1' && comm === ORCA_BINARY) return Number(pid)
  }
  return null
}

async function inspectorIsListening() {
  try {
    await fetch(INSPECTOR_LIST_URL)
    return true
  } catch {
    return false
  }
}

// Runs inside Orca's main process. Every callback catches its own errors: an uncaught exception
// there shows Orca's error dialog.
const installerSource = (cssPath, scriptPath, modePath, palettePath, paletteCssPaths, autoPath) => `(() => {
  const { app, webContents } = require('electron')
  const fs = require('fs')
  const inspector = require('inspector')
  const isAppUi = (wc) => !wc.isDestroyed() && wc.getURL().includes('/out/renderer/index.html')
  const state = globalThis.__orcaChatSectionStyle ??= { keys: new Map(), hooked: new WeakSet() }
  state.cssPath = ${JSON.stringify(cssPath)}
  state.scriptPath = ${JSON.stringify(scriptPath)}
  state.modePath = ${JSON.stringify(modePath)}
  state.modes = ${JSON.stringify(MODES)}
  state.palettePath = ${JSON.stringify(palettePath)}
  state.paletteCssPaths = ${JSON.stringify(paletteCssPaths)}
  state.palettes = ${JSON.stringify(PALETTES)}
  state.autoPath = ${JSON.stringify(autoPath)}
  state.themePluginsDir = ${JSON.stringify(THEME_PLUGINS_DIR)}
  state.localThemeDirs = ${JSON.stringify(LOCAL_THEME_DIRS)}
  state.pluginPalettes = ${JSON.stringify(PLUGIN_PALETTES)}
  // Installed theme tokens, read on every apply so a theme plugin update needs no re-install.
  state.readThemes = () => (${readThemes.toString()})(fs, state.themePluginsDir, state.localThemeDirs, state.pluginPalettes)
  state.lastError = null
  // One apply at a time per window: several watched files change together (an edit touching chat.css and a
  // palette), and two overlapping applies both read the same previous key, so one inserted stylesheet leaked
  // and kept stale rules live.
  state.queues ??= new Map()

  const applyNow = async (wc) => {
    try {
      if (!isAppUi(wc)) return
      const css = [state.cssPath, ...state.paletteCssPaths].map(path => fs.readFileSync(path, 'utf8')).join('\\n')
      const previous = state.keys.get(wc.id)
      const key = await wc.insertCSS(css)
      state.keys.set(wc.id, key)
      if (previous) await wc.removeInsertedCSS(previous)
      await wc.executeJavaScript(fs.readFileSync(state.scriptPath, 'utf8'))
      const mode = fs.readFileSync(state.modePath, 'utf8').trim()
      if (!state.modes.includes(mode)) throw new Error('Unknown chat mode "' + mode + '" in ' + state.modePath)
      await wc.executeJavaScript('document.documentElement.dataset.ompChatMode = ' + JSON.stringify(mode))
      const palette = fs.readFileSync(state.palettePath, 'utf8').trim()
      if (palette !== 'auto' && !state.palettes.includes(palette)) throw new Error('Unknown chat palette "' + palette + '" in ' + state.palettePath)
      const themes = palette === 'auto' ? state.readThemes() : []
      // The script's completion value is a function, which executeJavaScript cannot clone back; return 0 instead.
      await wc.executeJavaScript(fs.readFileSync(state.autoPath, 'utf8') + '\\n;0')
      state.appliedPalette = await wc.executeJavaScript('window.__ompApplyPalette(' + JSON.stringify(palette) + ', ' + JSON.stringify(themes) + ', ' + JSON.stringify(state.palettes) + ')')
    } catch (error) {
      state.lastError = String(error)
    }
  }
  state.apply = (wc) => {
    const next = (state.queues.get(wc.id) ?? Promise.resolve()).then(() => applyNow(wc))
    state.queues.set(wc.id, next)
    return next
  }
  const hook = (wc) => {
    if (state.hooked.has(wc)) return
    state.hooked.add(wc)
    wc.on('did-finish-load', () => { state.keys.delete(wc.id); state.apply(wc) })
    wc.on('destroyed', () => { state.keys.delete(wc.id); state.queues.delete(wc.id) })
  }
  const applyAll = () => Promise.all(webContents.getAllWebContents().map((wc) => { hook(wc); return state.apply(wc) }))

  if (!state.listening) {
    state.listening = true
    app.on('web-contents-created', (_event, wc) => { try { hook(wc) } catch (error) { state.lastError = String(error) } })
  }
  // unwatchFile drops listeners from earlier runs, so a refresh never stacks watchers.
  for (const path of [state.cssPath, state.scriptPath, state.modePath, state.palettePath, state.autoPath, ...state.paletteCssPaths]) {
    fs.unwatchFile(path)
    fs.watchFile(path, { interval: 1000 }, () => { applyAll().catch((error) => { state.lastError = String(error) }) })
  }
  return applyAll().then(async () => {
    setTimeout(() => { try { inspector.close() } catch {} }, 1000)
    return { pid: process.pid, styled: [...state.keys.keys()], mode: fs.readFileSync(state.modePath, 'utf8').trim(), palette: fs.readFileSync(state.palettePath, 'utf8').trim(), appliedPalette: state.appliedPalette, lastError: state.lastError }
  })
})()`

async function evaluateInOrca(expression) {
  const list = await (await fetch(INSPECTOR_LIST_URL)).json()
  const ws = new WebSocket(list[0].webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject })
  const reply = await new Promise((resolve) => {
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data)
      if (message.id === 1) resolve(message)
    }
    ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression, includeCommandLineAPI: true, awaitPromise: true, returnByValue: true } }))
  })
  ws.close()
  if (reply.result?.exceptionDetails) throw new Error(JSON.stringify(reply.result.exceptionDetails))
  return reply.result.result.value
}

const choices = { mode: MODES, palette: PALETTE_CHOICES, surface: ['chat', 'terminal', 'both'] }
const flags = {}
for (let index = 2; index < process.argv.length; index++) {
  const flag = process.argv[index]
  if (flag === '--if-needed' && !flags.ifNeeded) { flags.ifNeeded = true; continue }
  const label = flag.slice(2)
  if (!flag.startsWith('--') || !choices[label] || Object.hasOwn(flags, label)) {
    console.error(`Unknown or duplicate flag "${flag}"`)
    process.exit(2)
  }
  const value = process.argv[++index]
  if (!choices[label].includes(value)) {
    console.error(`Unknown ${label} "${value}". Choose one of: ${choices[label].join(', ')}`)
    process.exit(2)
  }
  flags[label] = value
}
if (flags.surface && !flags.mode && !flags.palette) {
  console.error('--surface requires --mode or --palette')
  process.exit(2)
}
const surface = flags.surface ?? 'both'
const modeIndex = flags.mode ? 1 : -1
const paletteIndex = flags.palette ? 1 : -1
const terminalPaths = { mode: join(STYLE_DIR, 'terminal-mode'), palette: join(STYLE_DIR, 'terminal-palette') }
const chatPaths = { mode: MODE_PATH, palette: PALETTE_PATH }
const effective = label => readFileSync(existsSync(terminalPaths[label]) ? terminalPaths[label] : chatPaths[label], 'utf8').trim()
// Validate current choices as well: no partial writes if an input file is invalid.
const current = Object.fromEntries(['mode', 'palette'].map(label => [label, effective(label)]))
for (const label of ['mode', 'palette']) {
  if (!choices[label].includes(current[label])) throw new Error(`Unknown terminal ${label} "${current[label]}"`)
}
if (flags.mode || flags.palette) {
  // Pin both effective choices before a chat-only switch; both clears all pins.
  for (const label of ['mode', 'palette']) {
    if (surface === 'chat' && !existsSync(terminalPaths[label])) writeFileSync(terminalPaths[label], `${current[label]}\n`)
    if (flags[label]) writeFileSync(surface === 'terminal' ? terminalPaths[label] : chatPaths[label], `${flags[label]}\n`)
    if (surface === 'both' && existsSync(terminalPaths[label])) unlinkSync(terminalPaths[label])
  }
}

async function readTerminalSettings() {
  let database
  try {
    const { DatabaseSync } = await import('node:sqlite')
    database = new DatabaseSync(join(homedir(), 'Library/Application Support/orca/profiles/local-default/profile-state.db'), { readOnly: true })
    const row = database.prepare("select payload from profile_state_documents where domain='settings'").get()
    return row ? JSON.parse(row.payload) : null
  } catch { return null }
  finally { database?.close() }
}
const ORCA_CLI = '/Applications/Orca.app/Contents/Resources/bin/orca'
let terminal
try {
  const pluginDir = join(dirname(STYLE_DIR), 'terminal-sections')
  terminal = syncTerminalThemes({ mode: effective('mode'), palette: effective('palette'),
    settings: await readTerminalSettings(), ompThemesDir: join(homedir(), '.omp/agent/themes'), pluginDir })
  terminal.reload = { status: 'unchanged' }
  if (terminal.changed.orca.length) {
    try {
      const plugins = JSON.parse(execFileSync(ORCA_CLI, ['plugin', 'list', '--json'], { encoding: 'utf8' }))
      if (JSON.stringify(plugins).includes('local.terminal-sections')) {
        const output = execFileSync(ORCA_CLI, ['plugin', 'dev', 'reload', pluginDir], { encoding: 'utf8' })
        terminal.reload = { status: 'reloaded', output: output.trim() }
      } else terminal.reload = { status: 'not-loaded' }
    } catch (error) { terminal.reload = { status: 'error', error: String(error) } }
  }
} catch (error) { terminal = { mode: effective('mode'), palette: effective('palette'), error: String(error) } }
const printResult = result => console.log(JSON.stringify({ ...result, terminal }))

const pid = findOrcaMainPid()
if (pid === null) {
  printResult({ message: 'Orca is not running' })
  process.exit(0)
}
let marker = null
try { marker = Number(readFileSync(MARKER_PATH, 'utf8')) } catch {}
if ((modeIndex !== -1 || paletteIndex !== -1) && marker === pid) {
  // The hook watches both setting files, so no inspector session is needed.
  printResult({ mode: readFileSync(MODE_PATH, 'utf8').trim(), palette: readFileSync(PALETTE_PATH, 'utf8').trim(), message: 'Orca applies settings within a second' })
  process.exit(0)
}
if (process.argv.includes('--if-needed') && marker === pid) { printResult({ pid, skipped: true }); process.exit(0) }
if (await inspectorIsListening()) {
  // Another process owns 9229, or a previous run left Orca's inspector open; never talk to a stranger.
  console.error('Port 9229 is already in use; not injecting')
  process.exit(1)
}

process.kill(pid, 'SIGUSR1')
let ready = false
for (let attempt = 0; attempt < 50 && !ready; attempt++) {
  ready = await inspectorIsListening()
  if (!ready) await new Promise((resolve) => setTimeout(resolve, 100))
}
if (!ready) {
  console.error('Orca inspector did not open')
  process.exit(1)
}

const result = await evaluateInOrca(installerSource(CSS_PATH, SCRIPT_PATH, MODE_PATH, PALETTE_PATH, PALETTE_CSS_PATHS, AUTO_PATH))
if (result.pid !== pid) throw new Error(`Inspector belongs to pid ${result.pid}, expected Orca ${pid}`)
if (result.lastError) throw new Error(`Orca reported: ${result.lastError}`)
writeFileSync(MARKER_PATH, String(pid))
printResult(result)
