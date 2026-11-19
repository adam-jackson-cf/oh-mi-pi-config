#!/usr/bin/env node
import { globSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

interface InstalledPlugin {
  plugin: string
  ids: string[]
}

const home = homedir()
const here = dirname(fileURLToPath(import.meta.url))
const pluginRoot = join(home, 'Library/Application Support/orca/plugins')
const catppuccinThemes = join(here, '../../catppuccin-sections/themes')
const installed: InstalledPlugin[] = [
  { plugin: 'stablyai.orca-ayu-theme', ids: ['ayu-light', 'ayu-mirage', 'ayu-dark'] },
  { plugin: 'stablyai.orca-github-vscode-theme', ids: ['github-light-default', 'github-dark-default', 'github-dark-dimmed'] },
  { plugin: 'stablyai.orca-vscode-default-themes', ids: ['vs-code-light-modern', 'vs-code-dark-modern'] },
]

function installedSource(plugin: string, id: string): [string, string] {
  const paths = globSync(join(pluginRoot, plugin, '*', 'themes', `${id}.json`)).toSorted()
  const [path] = paths
  if (paths.length !== 1 || !path) throw new Error(`Expected one installed source for ${id}; found ${paths.length}: ${paths.join(', ')}`)
  return [id, path]
}

const sources: [string, string][] = [
  ['latte', join(catppuccinThemes, 'latte.json')],
  ['mocha', join(catppuccinThemes, 'mocha.json')],
  ...installed.flatMap(({ plugin, ids }) => ids.map((id) => installedSource(plugin, id))),
]
const tokens = Object.fromEntries(sources.map(([id, path]) => {
  const theme = JSON.parse(readFileSync(path, 'utf8'))
  if (!['light', 'dark'].includes(theme.base) || !theme.tokens) throw new Error(`Invalid theme: ${path}`)
  return [id, theme.tokens]
}))
writeFileSync(join(here, 'theme-tokens.js'), `window.HARNESS_TOKENS = ${JSON.stringify(tokens)};\n`)
