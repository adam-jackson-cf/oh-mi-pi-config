#!/usr/bin/env bun
import fs from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { LOCAL_THEME_DIRS, PLUGIN_PALETTES, THEME_PLUGINS_DIR, readThemes } from '../theme-palettes.ts'
import { withChrome, type DevToolsPage } from './devtools.ts'

const styleDir = dirname(dirname(fileURLToPath(import.meta.url)))
const palettes = fs.readdirSync(join(styleDir, 'palettes')).filter((n) => n.endsWith('.css')).map((n) => n.slice(0, -4)).toSorted()
const themes = readThemes(fs, THEME_PLUGINS_DIR, LOCAL_THEME_DIRS, PLUGIN_PALETTES)
const autoSource = fs.readFileSync(join(styleDir, 'palette-auto.js'), 'utf8')
const cssOf = (tokens: Record<string, string>) => ':root{' + Object.entries(tokens).map(([k, v]) => `${k}:${v}`).join(';') + ';--radius:0.5rem;--omp-unrelated:#123456}'
const themeNamed = (file: string) => themes.find((t) => t.source.endsWith('/' + file))?.tokens ?? {}
const setTheme = (css: string) => `document.getElementById('theme').textContent = ${JSON.stringify(css)}`

async function checkPalettes(page: DevToolsPage): Promise<boolean> {
  const apply = (choice: string) => page.evaluate<string>(`window.ompApplyPalette(${JSON.stringify(choice)}, ${JSON.stringify(themes)}, ${JSON.stringify(palettes)})`)
  const afterSwap = (css: string) => page.evaluate<string>(`(() => { ${setTheme(css)}; return new Promise((r) => setTimeout(() => r(document.documentElement.dataset.ompChatPalette), 400)) })()`)
  await page.evaluate(`document.head.innerHTML = '<style id="theme"></style>'; ${autoSource}; true`)
  let failed = false
  const check = (label: string, got: string | undefined, want: string) => {
    if (got !== want) failed = true
    console.log(`${got === want ? 'ok  ' : 'FAIL'} ${label.padEnd(56)} ${got}${got === want ? '' : ` (want ${want})`}`)
  }
  for (const theme of themes) {
    await page.evaluate(setTheme(cssOf(theme.tokens)))
    check(theme.source, await apply('auto'), theme.palette)
  }
  await page.evaluate(setTheme(cssOf(themeNamed('ayu-light.json'))))
  await apply('auto')
  check('swap ayu-light -> github-dark-dimmed (auto)', await afterSwap(cssOf(themeNamed('github-dark-dimmed.json'))), 'github')
  await apply('vscode')
  check('swap to ayu-light under fixed vscode', await afterSwap(cssOf(themeNamed('ayu-light.json'))), 'vscode')
  await page.evaluate(setTheme(':root{--background:#123123;--foreground:#ff00ff}'))
  check('unknown theme (auto)', await apply('auto'), 'catppuccin')
  return failed
}

const failed = await withChrome(`/tmp/orca-palette-auto-chrome-${process.pid}`, [], checkPalettes)
process.exitCode = failed ? 1 : 0
