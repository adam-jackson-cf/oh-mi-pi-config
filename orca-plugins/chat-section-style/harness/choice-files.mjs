#!/usr/bin/env bun
// Usage: bun orca-plugins/chat-section-style/harness/choice-files.mjs
// Git state checks plus isolated injector conformance; never runs against the real style directory.
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const harnessDir = dirname(fileURLToPath(import.meta.url))
const root = resolve(harnessDir, '../../..')
const styleDir = dirname(harnessDir)
const choiceFiles = ['mode', 'palette', 'terminal-mode', 'terminal-palette']
let failures = 0
const fail = (context, message) => {
  failures++
  console.error(`FAIL ${context}: ${message}`)
}
const equal = (context, got, want) => {
  if (got !== want) fail(context, `${JSON.stringify(got)} != ${JSON.stringify(want)}`)
}
const run = (command, args, cwd) => {
  const result = spawnSync(command, args, { cwd, encoding: 'utf8', timeout: 10000 })
  if (result.error) throw result.error
  if (result.signal) throw new Error(`${command} terminated by ${result.signal}`)
  return result
}

for (const name of choiceFiles) {
  const path = `orca-plugins/chat-section-style/${name}`
  try {
    const ignored = run('git', ['check-ignore', '-q', path], root)
    if (ignored.status !== 0) fail(path, `unignored (git check-ignore exited ${ignored.status})`)
    const tracked = run('git', ['ls-files', '--error-unmatch', path], root)
    if (tracked.status === 0) fail(path, 'tracked (git ls-files --error-unmatch exited 0)')
  } catch (error) { fail(path, error.message) }
}

let temporary
try {
  temporary = fs.mkdtempSync(join(tmpdir(), 'orca-choice-files-'))
  const copy = join(temporary, 'chat-section-style')
  const excludedPaths = new Set([harnessDir, ...choiceFiles.map(name => join(styleDir, name))])
  fs.cpSync(styleDir, copy, { recursive: true, filter: path => !excludedPaths.has(path) })
  const inject = () => {
    const result = run('node', [join(copy, 'inject.mjs'), '--surface', 'terminal'], copy)
    equal('isolated/inject/exit', result.status, 2)
    if (result.status !== 2) fail('isolated/inject/diagnostic', result.stderr.trim())
  }
  const checkDefault = (name, want) => {
    const path = join(copy, name)
    if (!fs.existsSync(path)) fail(`isolated/${name}`, 'missing default creation')
    else equal(`isolated/${name}`, fs.readFileSync(path, 'utf8'), want)
  }
  const checkOverrides = () => {
    for (const name of ['terminal-mode', 'terminal-palette']) {
      if (fs.existsSync(join(copy, name))) fail(`isolated/${name}`, 'terminal override must not be created')
    }
  }
  inject()
  checkDefault('mode', 'gutter\n')
  checkDefault('palette', 'auto\n')
  checkOverrides()
  fs.writeFileSync(join(copy, 'mode'), 'editorial\n')
  inject()
  equal('isolated/mode/preserved', fs.readFileSync(join(copy, 'mode'), 'utf8'), 'editorial\n')
  checkOverrides()
} catch (error) { fail('isolated/inject', error.message) }
finally { if (temporary) fs.rmSync(temporary, { recursive: true, force: true }) }

if (!failures) console.log('PASS choice files are machine-local; isolated defaults created and existing mode preserved')
process.exitCode = failures ? 1 : 0
