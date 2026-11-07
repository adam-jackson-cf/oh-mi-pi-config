#!/usr/bin/env bun
// Usage: bun harness/palette-auto.mjs
// Checks palette `auto` (../palette-auto.js) in a private headless Chrome against every installed theme that
// ../theme-palettes.mjs maps:
//   1. a page carrying each theme's tokens (plus tokens no theme defines) is detected as that theme's palette;
//   2. after a live theme swap the observer re-detects;
//   3. a fixed palette ignores theme swaps;
//   4. an unknown theme falls back to catppuccin.
// Exit code 1 on any mismatch. Never touches the running Orca.
import fs from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { LOCAL_THEME_DIRS, PLUGIN_PALETTES, THEME_PLUGINS_DIR, readThemes } from '../theme-palettes.mjs'

const styleDir = dirname(dirname(fileURLToPath(import.meta.url)))
const palettes = fs.readdirSync(join(styleDir, 'palettes')).filter((n) => n.endsWith('.css')).map((n) => n.slice(0, -4)).sort()
const themes = readThemes(fs, THEME_PLUGINS_DIR, LOCAL_THEME_DIRS, PLUGIN_PALETTES)
const autoSource = fs.readFileSync(join(styleDir, 'palette-auto.js'), 'utf8')
const cssOf = (tokens) => ':root{' + Object.entries(tokens).map(([k, v]) => `${k}:${v}`).join(';') + ';--radius:0.5rem;--omp-unrelated:#123456}'
const themeNamed = (file) => themes.find((t) => t.source.endsWith('/' + file)).tokens

const profile = `/tmp/orca-palette-auto-chrome-${process.pid}`
const chrome = Bun.spawn(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--headless=new', '--disable-gpu',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdout: 'ignore', stderr: 'ignore' })
let ws
try {
  let port
  for (let i = 0; i < 100 && !port; i++) { try { port = (await Bun.file(`${profile}/DevToolsActivePort`).text()).split('\n')[0].trim() } catch { await Bun.sleep(100) } }
  let targets
  for (let i = 0; i < 50 && !targets; i++) { try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json() } catch { await Bun.sleep(100) } }
  ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl)
  await new Promise((resolve) => { ws.onopen = resolve })
  let nextId = 1
  const pending = new Map()
  ws.onmessage = (event) => { const m = JSON.parse(event.data); pending.get(m.id)?.(m) }
  const evaluate = (expression) => new Promise((resolve, reject) => {
    const id = nextId++
    pending.set(id, (m) => (m.result?.exceptionDetails ? reject(new Error(JSON.stringify(m.result.exceptionDetails))) : resolve(m.result.result.value)))
    ws.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression, returnByValue: true, awaitPromise: true } }))
  })
  const apply = (choice) => evaluate(`window.__ompApplyPalette(${JSON.stringify(choice)}, ${JSON.stringify(themes)}, ${JSON.stringify(palettes)})`)
  const setTheme = (css) => `document.getElementById('theme').textContent = ${JSON.stringify(css)}`
  const afterSwap = (css) => evaluate(`(() => { ${setTheme(css)}; return new Promise((r) => setTimeout(() => r(document.documentElement.dataset.ompChatPalette), 400)) })()`)

  await evaluate(`document.head.innerHTML = '<style id="theme"></style>'; ${autoSource}; true`)
  let failed = false
  const check = (label, got, want) => {
    if (got !== want) failed = true
    console.log(`${got === want ? 'ok  ' : 'FAIL'} ${label.padEnd(56)} ${got}${got === want ? '' : ` (want ${want})`}`)
  }
  for (const theme of themes) {
    await evaluate(setTheme(cssOf(theme.tokens)))
    check(theme.source, await apply('auto'), theme.palette)
  }
  await evaluate(setTheme(cssOf(themeNamed('ayu-light.json'))))
  await apply('auto')
  check('swap ayu-light -> github-dark-dimmed (auto)', await afterSwap(cssOf(themeNamed('github-dark-dimmed.json'))), 'github')
  await apply('vscode')
  check('swap to ayu-light under fixed vscode', await afterSwap(cssOf(themeNamed('ayu-light.json'))), 'vscode')
  await evaluate(setTheme(':root{--background:#123123;--foreground:#ff00ff}'))
  check('unknown theme (auto)', await apply('auto'), 'catppuccin')
  process.exitCode = failed ? 1 : 0
} finally {
  ws?.close()
  chrome.kill()
  await chrome.exited
  fs.rmSync(profile, { recursive: true, force: true })
}
