// Which palette each installed Orca theme plugin pairs with, and where those themes' token files live.
// Used by palette `auto`: inject.mjs embeds readThemes in Orca's main-process hook; harness/palette-auto.mjs tests it.
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

// Self-contained (only its arguments) so its source can run inside Orca's main process.
// Returns [{ palette, tokens, source }] for every theme JSON of a mapped plugin, in any installed version folder.
export function readThemes(fs, pluginsDir, localDirs, pluginPalettes) {
  const themes = []
  const add = (pluginKey, dir) => {
    const palette = pluginPalettes[pluginKey]
    if (!palette) return
    let names = []
    try { names = fs.readdirSync(dir) } catch { return }
    for (const name of names.filter((n) => n.endsWith('.json'))) {
      try {
        const tokens = JSON.parse(fs.readFileSync(dir + '/' + name, 'utf8')).tokens
        if (tokens) themes.push({ palette, tokens, source: pluginKey + '/' + name })
      } catch {}
    }
  }
  for (const [pluginKey, dir] of Object.entries(localDirs)) add(pluginKey, dir)
  let plugins = []
  try { plugins = fs.readdirSync(pluginsDir) } catch {}
  for (const pluginKey of plugins) {
    let versions = []
    try { versions = fs.readdirSync(pluginsDir + '/' + pluginKey) } catch {}
    for (const version of versions) add(pluginKey, pluginsDir + '/' + pluginKey + '/' + version + '/themes')
  }
  return themes
}
