#!/usr/bin/env bun
// Capture a copied session in the real omp TUI, never the live agent directory.
import fs from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { spawnSync } from 'node:child_process'
import headless from '@xterm/headless'
import serialize from '@xterm/addon-serialize'
import { buildOmpTheme, buildOrcaTerminalTheme, VARIANTS, TERMINAL_MODES } from '../terminal/build.mjs'

const { Terminal } = headless
const { SerializeAddon } = serialize
const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const columns = 150
const rows = 60
const fontSize = 13
const lineHeight = 1.3

// A PTY is required even when the harness itself has no controlling terminal.
// The child gets no input, and its process group is killed and reaped on every path.
const capturePty = String.raw`
import errno, fcntl, os, pty, select, shutil, signal, struct, sys, termios, time
command = shutil.which('omp')
if not command:
    sys.exit('omp executable not found on PATH')
pid, fd = pty.fork()
if pid == 0:
    fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack('HHHH', 60, 150, 0, 0))
    os.chdir(sys.argv[2])
    os.execv(command, [command, '--resume', sys.argv[1]])
try:
    deadline = time.monotonic() + float(sys.argv[3])
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], min(0.1, max(0, deadline - time.monotonic())))
        if not ready:
            continue
        try:
            data = os.read(fd, 65536)
        except OSError as error:
            if error.errno == errno.EIO:
                break
            raise
        if not data:
            break
        sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
finally:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.close(fd)
    _, status = os.waitpid(pid, 0)
if os.WIFEXITED(status) and os.WEXITSTATUS(status):
    sys.exit('omp exited with status ' + str(os.WEXITSTATUS(status)))
`

export async function captureSession({ session, palette = 'catppuccin', variant, mode = 'gutter', slot = 'dark', seconds = 10 }) {
  variant ??= VARIANTS[palette]?.[slot]?.[0]
  const theme = buildOmpTheme({ palette, variant, mode, slot })
  const terminal = buildOrcaTerminalTheme({ palette, variant, slot }).terminal
  if (!Number.isFinite(seconds) || seconds <= 0) throw new Error('seconds must be positive')
  const agent = fs.mkdtempSync(join(tmpdir(), 'terminal-shoot-'))
  try {
    fs.mkdirSync(join(agent, 'themes'))
    fs.mkdirSync(join(agent, 'sessions'))
    const copy = join(agent, 'sessions', basename(session))
    fs.copyFileSync(session, copy)
    fs.writeFileSync(join(agent, 'config.yml'), 'symbolPreset: nerd\ntheme:\n  dark: preview-dark\n  light: preview-light\n')
    for (const previewSlot of ['dark', 'light']) {
      // Both configured names exist, even if omp probes the other colour slot at startup.
      const preview = previewSlot === slot ? theme : buildOmpTheme({ palette, variant: VARIANTS[palette][previewSlot][0], mode, slot: previewSlot })
      fs.writeFileSync(join(agent, 'themes', `preview-${previewSlot}.json`), JSON.stringify({ ...preview, name: `preview-${previewSlot}` }))
    }
    const result = spawnSync('python3', ['-c', capturePty, copy, agent, String(seconds)], {
      env: { ...process.env, PI_CODING_AGENT_DIR: agent, COLORTERM: 'truecolor', TERM: 'xterm-256color', COLORFGBG: slot === 'dark' ? '15;0' : '0;15' },
      maxBuffer: 32 * 1024 * 1024,
    })
    if (result.error) throw result.error
    if (result.status !== 0) throw new Error(`PTY capture failed: ${result.stderr.toString().trim()}`)
    if (!result.stdout.length) throw new Error('Empty omp capture')
    return { bytes: result.stdout, theme, terminal }
  } finally {
    fs.rmSync(agent, { recursive: true, force: true })
  }
}

export async function renderHtml({ bytes, terminal, lines = 70, anchor }) {
  if (!Number.isSafeInteger(lines) || lines <= 0) throw new Error('lines must be a positive integer')
  if (!bytes?.length) throw new Error('Empty omp capture')
  const emulator = new Terminal({ cols: columns, rows, scrollback: 20000, theme: terminal, fontSize, fontFamily: '"SF Mono", Menlo, monospace', allowProposedApi: true })
  const addon = new SerializeAddon()
  emulator.loadAddon(addon)
  try {
    await new Promise(done => emulator.write(bytes, done))
    const buffer = emulator.buffer.active
    let last = buffer.length - 1
    while (last >= 0 && !buffer.getLine(last).translateToString(true).trim()) last--
    if (last < 0) throw new Error('Blank terminal render')
    let start = Math.max(0, last - lines + 1)
    if (anchor) {
      let match = last
      while (match >= 0 && !buffer.getLine(match).translateToString(true).includes(anchor)) match--
      if (match < 0) throw new Error(`Anchor not found in terminal capture: ${anchor}`)
      start = Math.max(0, match - Math.floor(lines * 0.3))
    }
    const end = Math.min(last, start + lines - 1)
    const serialized = addon.serializeAsHTML({ includeGlobalBackground: true, range: { startLine: start, endLine: end, startCol: 0 } })
    const html = serialized.split('<!--StartFragment-->')[1].split('<!--EndFragment-->')[0]
    return `<!doctype html><html><head><meta charset="utf-8"><title>omp terminal preview</title><style>html,body{margin:0;background:${terminal.background};color:${terminal.foreground}}body{padding:12px}pre{margin:0;line-height:${lineHeight}}</style></head><body>${html}</body></html>`
  } finally {
    emulator.dispose()
  }
}

async function main() {
  const args = process.argv.slice(2)
  const session = args.shift()
  const output = args.shift()
  if (!session || !output || session.startsWith('--') || output.startsWith('--')) throw new Error('Usage: terminal-shoot.mjs <session.jsonl> <out-dir> [--palette p] [--variant v] [--mode m] [--slot dark|light|both] [--lines N] [--anchor TEXT] [--all]')
  const options = { palette: 'catppuccin', mode: 'gutter', slot: 'both', lines: 70 }
  for (let index = 0; index < args.length; index++) {
    const key = args[index].slice(2)
    if (args[index] === '--all') { options.all = true; continue }
    if (!args[index].startsWith('--') || !['palette', 'variant', 'mode', 'slot', 'lines', 'anchor'].includes(key)) throw new Error(`Unknown option: ${args[index]}`)
    const value = args[++index]
    if (value === undefined || value.startsWith('--')) throw new Error(`Missing value for --${key}`)
    options[key] = key === 'lines' ? Number(value) : value
  }
  if (!VARIANTS[options.palette]) throw new Error(`Unknown terminal palette: ${options.palette}`)
  if (!TERMINAL_MODES.includes(options.mode)) throw new Error(`Unknown terminal mode: ${options.mode}`)
  if (!['dark', 'light', 'both'].includes(options.slot)) throw new Error(`Unknown terminal slot: ${options.slot}`)
  if (!Number.isSafeInteger(options.lines) || options.lines <= 0) throw new Error('lines must be a positive integer')
  fs.accessSync(chrome, fs.constants.X_OK)
  const shots = []
  for (const mode of options.all ? TERMINAL_MODES : [options.mode]) {
    for (const slot of options.all || options.slot === 'both' ? ['dark', 'light'] : [options.slot]) {
      for (const variant of options.all ? VARIANTS[options.palette][slot] : [options.variant ?? VARIANTS[options.palette][slot][0]]) {
        const shot = { palette: options.palette, variant, mode, slot }
        buildOmpTheme(shot)
        shots.push(shot)
      }
    }
  }
  const directory = resolve(output)
  fs.mkdirSync(directory, { recursive: true })
  // A separate profile prevents Chrome from sharing state with a running browser.
  const profile = fs.mkdtempSync(join(tmpdir(), 'terminal-shoot-chrome-'))
  try {
    for (const shot of shots) {
      const { bytes, terminal } = await captureSession({ session: resolve(session), ...shot })
      const html = join(directory, `${shot.palette}-${shot.variant}-${shot.mode}-${shot.slot}.html`)
      const png = html.slice(0, -5) + '.png'
      const rendered = await renderHtml({ bytes, terminal, lines: options.lines, anchor: options.anchor })
      fs.writeFileSync(html, rendered)
      const height = Math.ceil(options.lines * fontSize * lineHeight + 24)
      const result = spawnSync(chrome, ['--headless=new', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1', `--user-data-dir=${profile}`, `--window-size=1260,${height}`, '--virtual-time-budget=1000', `--screenshot=${png}`, pathToFileURL(html).href], { encoding: 'utf8', timeout: 30000 })
      if (result.error) throw result.error
      if (result.status !== 0) throw new Error(`Chrome failed: ${result.stderr}`)
      const image = fs.readFileSync(png)
      if (image.length <= 24 || image.toString('ascii', 12, 16) !== 'IHDR') throw new Error(`Invalid Chrome PNG: ${png}`)
      console.log(JSON.stringify({ png, html, ...shot, bytes: bytes.length, lines: options.lines }))
    }
  } finally {
    fs.rmSync(profile, { recursive: true, force: true })
  }
}

if (import.meta.url === pathToFileURL(resolve(process.argv[1] ?? '')).href) {
  main().catch(error => { console.error(error.message); process.exitCode = 1 })
}
