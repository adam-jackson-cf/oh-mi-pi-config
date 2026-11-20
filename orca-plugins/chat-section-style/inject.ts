#!/usr/bin/env node
import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync, readdirSync, unlinkSync, writeFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import type { DatabaseSync } from 'node:sqlite'
import { fileURLToPath } from 'node:url'
import { LOCAL_THEME_DIRS, PLUGIN_PALETTES, THEME_PLUGINS_DIR, readThemes } from './theme-palettes.ts'
import { syncTerminalThemes, type AppThemeSettings } from './terminal/build.ts'

const ORCA_BINARY = '/Applications/Orca.app/Contents/MacOS/Orca'
const INSPECTOR_LIST_URL = 'http://127.0.0.1:9229/json/list'
const STYLE_DIR = dirname(fileURLToPath(import.meta.url))
const CSS_PATH = join(STYLE_DIR, 'chat.css')
const SCRIPT_PATH = join(STYLE_DIR, 'id-highlight.js')
const MODE_PATH = join(STYLE_DIR, 'mode')
const DEFAULT_MODE = 'gutter'
const MODES = ['document', 'split-cards', 'gutter', 'editorial']
const PALETTE_PATH = join(STYLE_DIR, 'palette')
const DEFAULT_PALETTE = 'auto'
const PALETTE_CSS_PATHS = readdirSync(join(STYLE_DIR, 'palettes')).filter(name => name.endsWith('.css')).toSorted().map(name => join(STYLE_DIR, 'palettes', name))
const PALETTES = PALETTE_CSS_PATHS.map(path => path.slice(path.lastIndexOf('/') + 1, -4))
const PALETTE_CHOICES = [...PALETTES, 'auto']
const AUTO_PATH = join(STYLE_DIR, 'palette-auto.js')
const MARKER_PATH = join(tmpdir(), 'orca-chat-section-style.pid')
const ORCA_CLI = '/Applications/Orca.app/Contents/Resources/bin/orca'

interface InstallerPaths {
  cssPath: string
  scriptPath: string
  modePath: string
  palettePath: string
  paletteCssPaths: string[]
  autoPath: string
}

interface InstallerResult {
  pid: number
  styled: number[]
  mode: string
  palette: string
  appliedPalette: string
  lastError: string | null
}

interface InspectorReply {
  id: number
  result?: { exceptionDetails?: object; result: { value: InstallerResult } }
}

interface InspectorTarget {
  webSocketDebuggerUrl: string
}

function findOrcaMainPid(): number | null {
  const rows = execFileSync('ps', ['-axo', 'pid=,ppid=,comm=']).toString().split('\n')
  for (const row of rows) {
    const [pid, ppid, comm] = row.trim().split(/\s+/)
    if (ppid === '1' && comm === ORCA_BINARY) return Number(pid)
  }
  return null
}

async function inspectorIsListening(): Promise<boolean> {
  try {
    await fetch(INSPECTOR_LIST_URL)
    return true
  } catch {
    return false
  }
}

const installerSource = (paths: InstallerPaths) => `(() => {
  const { app, webContents } = require('electron')
  const fs = require('fs')
  const inspector = require('inspector')
  const isAppUi = (wc) => !wc.isDestroyed() && wc.getURL().includes('/out/renderer/index.html')
  const state = globalThis.__orcaChatSectionStyle ??= { keys: new Map(), hooked: new WeakSet() }
  state.cssPath = ${JSON.stringify(paths.cssPath)}
  state.scriptPath = ${JSON.stringify(paths.scriptPath)}
  state.modePath = ${JSON.stringify(paths.modePath)}
  state.modes = ${JSON.stringify(MODES)}
  state.palettePath = ${JSON.stringify(paths.palettePath)}
  state.paletteCssPaths = ${JSON.stringify(paths.paletteCssPaths)}
  state.palettes = ${JSON.stringify(PALETTES)}
  state.autoPath = ${JSON.stringify(paths.autoPath)}
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
      state.appliedPalette = await wc.executeJavaScript('window.ompApplyPalette(' + JSON.stringify(palette) + ', ' + JSON.stringify(themes) + ', ' + JSON.stringify(state.palettes) + ')')
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

async function evaluateInOrca(expression: string): Promise<InstallerResult> {
  const list: InspectorTarget[] = JSON.parse(await (await fetch(INSPECTOR_LIST_URL)).text())
  const ws = new WebSocket(list[0]?.webSocketDebuggerUrl ?? '')
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve)
    ws.addEventListener('error', reject)
  })
  const reply = await new Promise<InspectorReply>((resolve) => {
    ws.addEventListener('message', (event) => {
      const message: InspectorReply = JSON.parse(String(event.data))
      if (message.id === 1) resolve(message)
    })
    ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression, includeCommandLineAPI: true, awaitPromise: true, returnByValue: true } }))
  })
  ws.close()
  if (reply.result?.exceptionDetails) throw new Error(JSON.stringify(reply.result.exceptionDetails))
  if (!reply.result) throw new Error('Orca inspector returned no result')
  return reply.result.result.value
}

if (!existsSync(MODE_PATH)) writeFileSync(MODE_PATH, `${DEFAULT_MODE}\n`, { flag: 'wx' })
if (!existsSync(PALETTE_PATH)) writeFileSync(PALETTE_PATH, `${DEFAULT_PALETTE}\n`, { flag: 'wx' })

const CHOICES = new Map<string, string[]>([['mode', MODES], ['palette', PALETTE_CHOICES], ['surface', ['chat', 'terminal', 'both']]])

function refuse(message: string): never {
  console.error(message)
  process.exit(2)
}

function parseFlags(argv: string[]) {
  const values = new Map<string, string>()
  let ifNeeded = false
  for (let index = 0; index < argv.length; index++) {
    const flag = argv[index] ?? ''
    if (flag === '--if-needed' && !ifNeeded) { ifNeeded = true; continue }
    const label = flag.slice(2)
    const allowed = CHOICES.get(label)
    if (!flag.startsWith('--') || !allowed || values.has(label)) refuse(`Unknown or duplicate flag "${flag}"`)
    const value = argv[++index]
    if (value === undefined || !allowed.includes(value)) refuse(`Unknown ${label} "${value}". Choose one of: ${allowed.join(', ')}`)
    values.set(label, value)
  }
  return { ifNeeded, values }
}

const flags = parseFlags(process.argv.slice(2))
const requestedMode = flags.values.get('mode')
const requestedPalette = flags.values.get('palette')
if (flags.values.has('surface') && !requestedMode && !requestedPalette) refuse('--surface requires --mode or --palette')
const surface = flags.values.get('surface') ?? 'both'
const LABELS = ['mode', 'palette'] as const
type ChoiceLabel = (typeof LABELS)[number]
const terminalPaths = { mode: join(STYLE_DIR, 'terminal-mode'), palette: join(STYLE_DIR, 'terminal-palette') }
const chatPaths = { mode: MODE_PATH, palette: PALETTE_PATH }
const effective = (label: ChoiceLabel) => readFileSync(existsSync(terminalPaths[label]) ? terminalPaths[label] : chatPaths[label], 'utf8').trim()
const current = { mode: effective('mode'), palette: effective('palette') }
for (const label of LABELS) {
  if (!CHOICES.get(label)?.includes(current[label])) throw new Error(`Unknown terminal ${label} "${current[label]}"`)
}
if (requestedMode || requestedPalette) {
  for (const label of LABELS) {
    const requested = flags.values.get(label)
    if (surface === 'chat' && !existsSync(terminalPaths[label])) writeFileSync(terminalPaths[label], `${current[label]}\n`)
    if (requested) writeFileSync(surface === 'terminal' ? terminalPaths[label] : chatPaths[label], `${requested}\n`)
    if (surface === 'both' && existsSync(terminalPaths[label])) unlinkSync(terminalPaths[label])
  }
}

async function readTerminalSettings(): Promise<AppThemeSettings | null> {
  let database: DatabaseSync | undefined
  try {
    const sqlite = await import('node:sqlite')
    database = new sqlite.DatabaseSync(join(homedir(), 'Library/Application Support/orca/profiles/local-default/profile-state.db'), { readOnly: true })
    const row = database.prepare("select payload from profile_state_documents where domain='settings'").get()
    return row ? JSON.parse(String(row['payload'])) : null
  } catch { return null }
  finally { database?.close() }
}

function reloadTerminalPlugin(pluginDir: string) {
  try {
    const plugins = JSON.parse(execFileSync(ORCA_CLI, ['plugin', 'list', '--json'], { encoding: 'utf8' }))
    if (!JSON.stringify(plugins).includes('local.terminal-sections')) return { status: 'not-loaded' }
    const output = execFileSync(ORCA_CLI, ['plugin', 'dev', 'reload', pluginDir], { encoding: 'utf8' })
    return { status: 'reloaded', output: output.trim() }
  } catch (error) { return { status: 'error', error: String(error) } }
}

async function syncTerminal() {
  try {
    const pluginDir = join(dirname(STYLE_DIR), 'terminal-sections')
    const synced = syncTerminalThemes({ mode: effective('mode'), palette: effective('palette'),
      settings: await readTerminalSettings(), ompThemesDir: join(homedir(), '.omp/agent/themes'), pluginDir })
    return { ...synced, reload: synced.changed.orca.length ? reloadTerminalPlugin(pluginDir) : { status: 'unchanged' } }
  } catch (error) { return { mode: effective('mode'), palette: effective('palette'), error: String(error) } }
}

function readMarker(): number | null {
  try { return Number(readFileSync(MARKER_PATH, 'utf8')) } catch { return null }
}

const terminal = await syncTerminal()
const printResult = <Report>(result: Report) => console.log(JSON.stringify({ ...result, terminal }))

const pid = findOrcaMainPid()
if (pid === null) {
  printResult({ message: 'Orca is not running' })
  process.exit(0)
}
const marker = readMarker()
if ((requestedMode || requestedPalette) && marker === pid) {
  printResult({ mode: readFileSync(MODE_PATH, 'utf8').trim(), palette: readFileSync(PALETTE_PATH, 'utf8').trim(), message: 'Orca applies settings within a second' })
  process.exit(0)
}
if (flags.ifNeeded && marker === pid) { printResult({ pid, skipped: true }); process.exit(0) }
if (await inspectorIsListening()) {
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

const result = await evaluateInOrca(installerSource({ cssPath: CSS_PATH, scriptPath: SCRIPT_PATH, modePath: MODE_PATH, palettePath: PALETTE_PATH, paletteCssPaths: PALETTE_CSS_PATHS, autoPath: AUTO_PATH }))
if (result.pid !== pid) throw new Error(`Inspector belongs to pid ${result.pid}, expected Orca ${pid}`)
if (result.lastError) throw new Error(`Orca reported: ${result.lastError}`)
writeFileSync(MARKER_PATH, String(pid))
printResult(result)
