import type { readFileSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const THEME_PLUGINS_DIR = join(homedir(), 'Library/Application Support/orca/plugins')
export const LOCAL_THEME_DIRS = {
  'local.catppuccin-sections': join(dirname(dirname(fileURLToPath(import.meta.url))), 'catppuccin-sections', 'themes'),
}
export const PLUGIN_PALETTES = {
  'local.catppuccin-sections': 'catppuccin',
  'stablyai.orca-catppuccin-theme': 'catppuccin',
  'stablyai.orca-ayu-theme': 'ayu',
  'stablyai.orca-github-vscode-theme': 'github',
  'stablyai.orca-vscode-default-themes': 'vscode',
}

const PALETTE_BY_PLUGIN = new Map(Object.entries(PLUGIN_PALETTES))

export function paletteForPlugin(pluginKey: string): string | undefined {
  return PALETTE_BY_PLUGIN.get(pluginKey)
}

export interface ThemeFiles {
  readdirSync: typeof readdirSync
  readFileSync: typeof readFileSync
}

export interface InstalledTheme {
  palette: string
  tokens: Record<string, string>
  source: string
}

export function readThemes(fs: ThemeFiles, pluginsDir: string, localDirs: Record<string, string>, pluginPalettes: Record<string, string>): InstalledTheme[] {
  const themes: InstalledTheme[] = []
  const entries = (dir: string): string[] => {
    try { return fs.readdirSync(dir) } catch { return [] }
  }
  const tokensOf = (path: string): Record<string, string> | undefined => {
    try { return JSON.parse(fs.readFileSync(path, 'utf8')).tokens } catch { return undefined }
  }
  const add = (pluginKey: string, dir: string) => {
    const palette = pluginPalettes[pluginKey]
    if (!palette) return
    for (const name of entries(dir).filter((n) => n.endsWith('.json'))) {
      const tokens = tokensOf(dir + '/' + name)
      if (tokens) themes.push({ palette, tokens, source: pluginKey + '/' + name })
    }
  }
  for (const [pluginKey, dir] of Object.entries(localDirs)) add(pluginKey, dir)
  for (const pluginKey of entries(pluginsDir)) {
    for (const version of entries(pluginsDir + '/' + pluginKey)) add(pluginKey, pluginsDir + '/' + pluginKey + '/' + version + '/themes')
  }
  return themes
}
