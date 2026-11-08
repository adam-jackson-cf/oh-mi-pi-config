#!/usr/bin/env bun
// Usage: bun orca-plugins/chat-section-style/harness/terminal-shoot-check.mjs
// P1 boundary proof: real omp PTYs and Chrome, isolated from the live agent directory.
import fs from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { VARIANTS, buildOrcaTerminalTheme } from '../terminal/build.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const fixture = join(here, 'fixtures/terminal-shoot-session.jsonl')
const userText = 'Terminal shoot synthetic preview'
const ircSender = 'PaletteScout'
const palette = 'catppuccin'
const mode = 'gutter'
let failures = 0
const summaries = []
const fail = (context, message) => {
  failures++
  console.error(`FAIL ${context}: ${message}`)
}
const check = (context, condition, message) => {
  if (!condition) fail(context, message)
}
const checkHtml = (context, html, background) => {
  check(`${context}/user`, html.includes(userText), `missing fixture user text: ${userText}`)
  check(`${context}/irc`, html.includes(ircSender), `missing IRC sender: ${ircSender}`)
  check(`${context}/background`, html.toLowerCase().includes(background.toLowerCase()), `missing terminal background ${background}`)
  const spans = html.match(/<span\b[^>]*\bstyle\s*=\s*["'][^"']+["'][^>]*>/gi)?.length ?? 0
  check(`${context}/styled-spans`, spans > 20, `expected > 20 styled spans, got ${spans}`)
}

let original
let temporary
try {
  original = fs.readFileSync(fixture)
  // Dynamic import gives a clear FAIL diagnostic when this new surface is absent.
  const { captureSession, renderHtml } = await import('./terminal-shoot.mjs')
  temporary = fs.mkdtempSync(join(tmpdir(), 'terminal-shoot-check-'))
  for (const slot of ['dark', 'light']) {
    const variant = VARIANTS[palette][slot][0]
    const context = `${palette}/${variant}/${mode}/${slot}`
    try {
      const { bytes, theme, terminal } = await captureSession({ session: fixture, palette, variant, mode, slot, seconds: 10 })
      check(`${context}/capture`, Buffer.isBuffer(bytes) && bytes.length > 0, 'expected non-empty captured Buffer')
      const ansi = bytes.toString('utf8')
      for (const token of ['toolTitle', 'thinkingText']) {
        const color = theme.vars[theme.colors[token]] ?? theme.colors[token]
        check(`${context}/${token}`, /^#[0-9a-f]{6}$/i.test(color), `expected resolved RGB hex, got ${color}`)
        const sgr = `38;2;${[1, 3, 5].map(index => parseInt(color.slice(index, index + 2), 16)).join(';')}`
        check(`${context}/${token}/SGR`, new RegExp(`\\x1b\\[[0-9;]*${sgr}(?:;[0-9;]*)?m`).test(ansi), `capture lacks truecolor SGR ${sgr}`)
      }
      const html = await renderHtml({ bytes, terminal, lines: 70, anchor: userText })
      const background = buildOrcaTerminalTheme({ palette, variant, slot }).terminal.background
      checkHtml(`${context}/render`, html, background)
      summaries.push(`PASS ${context}: capture ${bytes.length} bytes; themed text and HTML`)
    } catch (error) { fail(context, error.message) }
  }

  const cli = spawnSync(process.execPath, [join(here, 'terminal-shoot.mjs'), fixture, temporary,
    '--palette', palette, '--mode', mode, '--lines', '70', '--anchor', userText],
  { encoding: 'utf8', timeout: 90000, maxBuffer: 4 * 1024 * 1024 })
  check('CLI/exit', !cli.error && cli.status === 0,
    cli.error?.message ?? `exit ${cli.status}, signal ${cli.signal}\n${cli.stderr}\n${cli.stdout}`)
  for (const slot of ['dark', 'light']) {
    const variant = VARIANTS[palette][slot][0]
    const context = `${palette}/${variant}/${mode}/${slot}/CLI`
    try {
      const stem = join(temporary, `${palette}-${variant}-${mode}-${slot}`)
      const png = fs.readFileSync(`${stem}.png`)
      check(`${context}/png-size`, png.length > 10 * 1024, `expected PNG > 10 KB, got ${png.length} bytes`)
      check(`${context}/png-header`, png.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) && png.toString('ascii', 12, 16) === 'IHDR', 'invalid PNG signature or IHDR')
      const width = png.readUInt32BE(16)
      const height = png.readUInt32BE(20)
      check(`${context}/dimensions`, width > 0 && height > 0, `invalid PNG dimensions ${width}x${height}`)
      checkHtml(context, fs.readFileSync(`${stem}.html`, 'utf8'), buildOrcaTerminalTheme({ palette, variant, slot }).terminal.background)
      summaries.push(`PASS ${context}: PNG ${png.length} bytes, ${width}x${height}; > 20 styled spans`)
    } catch (error) { fail(context, error.message) }
  }
} catch (error) { fail('terminal-shoot', error.message) }
finally {
  if (original) {
    try { check('fixture/unchanged', fs.readFileSync(fixture).equals(original), 'original fixture bytes changed') }
    catch (error) { fail('fixture/unchanged', error.message) }
  }
  if (temporary) fs.rmSync(temporary, { recursive: true, force: true })
}
if (!failures) {
  for (const summary of summaries) console.log(summary)
  console.log('PASS terminal-shoot P1: both slots; original fixture unchanged')
}
process.exitCode = failures ? 1 : 0
