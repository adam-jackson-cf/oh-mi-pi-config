#!/usr/bin/env node
// Regenerate the harness's partial Orca token maps; renderer CSS supplies base defaults.
import { globSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const home = homedir()
const pluginRoot = join(home, 'Library/Application Support/orca/plugins')
const sources = [
  ['latte', join(home, '.omp/orca-plugins/catppuccin-sections/themes/latte.json')],
  ['mocha', join(home, '.omp/orca-plugins/catppuccin-sections/themes/mocha.json')],
]
for (const [plugin, ids] of [
  ['stablyai.orca-ayu-theme', ['ayu-light', 'ayu-mirage', 'ayu-dark']],
  ['stablyai.orca-github-vscode-theme', ['github-light-default', 'github-dark-default', 'github-dark-dimmed']],
  ['stablyai.orca-vscode-default-themes', ['vs-code-light-modern', 'vs-code-dark-modern']],
]) {
  for (const id of ids) {
    const paths = globSync(join(pluginRoot, plugin, '*', 'themes', `${id}.json`)).sort()
    if (paths.length !== 1) throw new Error(`Expected one installed source for ${id}; found ${paths.length}: ${paths.join(', ')}`)
    sources.push([id, paths[0]])
  }
}
const tokens = Object.fromEntries(sources.map(([id, path]) => {
  const theme = JSON.parse(readFileSync(path, 'utf8'))
  if (!['light', 'dark'].includes(theme.base) || !theme.tokens) throw new Error(`Invalid theme: ${path}`)
  return [id, theme.tokens]
}))
writeFileSync(join(dirname(fileURLToPath(import.meta.url)), 'harness-tokens.js'), `window.HARNESS_TOKENS = ${JSON.stringify(tokens)};\n`)
