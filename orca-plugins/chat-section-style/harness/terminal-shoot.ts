#!/usr/bin/env bun
import fs from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { spawnSync } from 'node:child_process'
import { Terminal, type IBuffer } from '@xterm/headless'
import { SerializeAddon } from '@xterm/addon-serialize'
import { buildOmpTheme, buildOrcaTerminalTheme, slotVariants, SLOTS, TERMINAL_MODES, terminalPalette } from '../terminal/build.ts'
import { terminalMode } from '../terminal/modes.ts'

const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const columns = 150
const rows = 60
const fontSize = 13
const lineHeight = 1.3

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

type TerminalColours = ReturnType<typeof buildOrcaTerminalTheme>['terminal']

export interface CaptureRequest {
  session: string
  palette?: string
  variant?: string
  mode?: string
  slot?: string
  seconds?: number
}

export interface RenderRequest {
  bytes: Buffer
  terminal: TerminalColours
  lines?: number
  anchor?: string | undefined
}

interface Shot {
  palette: string
  variant: string
  mode: string
  slot: string
}

interface ShootOptions {
  palette: string
  variant: string | undefined
  mode: string
  slot: string
  lines: number
  anchor: string | undefined
  all: boolean
}

const firstVariant = (palette: string, slot: string) => slotVariants(palette, slot)[0] ?? ''

function writePreviewThemes(agent: string, shot: Shot, theme: ReturnType<typeof buildOmpTheme>): void {
  for (const previewSlot of SLOTS) {
    const preview = previewSlot === shot.slot ? theme : buildOmpTheme({ palette: shot.palette, variant: firstVariant(shot.palette, previewSlot), mode: shot.mode, slot: previewSlot })
    fs.writeFileSync(join(agent, 'themes', `preview-${previewSlot}.json`), JSON.stringify({ ...preview, name: `preview-${previewSlot}` }))
  }
}

function runCapture(copy: string, agent: string, seconds: number, slot: string): Buffer {
  const result = spawnSync('python3', ['-c', capturePty, copy, agent, String(seconds)], {
    env: { ...process.env, PI_CODING_AGENT_DIR: agent, COLORTERM: 'truecolor', TERM: 'xterm-256color', COLORFGBG: slot === 'dark' ? '15;0' : '0;15' },
    maxBuffer: 32 * 1024 * 1024,
  })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`PTY capture failed: ${result.stderr.toString().trim()}`)
  if (!result.stdout.length) throw new Error('Empty omp capture')
  return result.stdout
}

export async function captureSession(request: CaptureRequest) {
  const { session, palette = 'catppuccin', mode = 'gutter', slot = 'dark', seconds = 10 } = request
  const shot = { palette, variant: request.variant ?? firstVariant(palette, slot), mode, slot }
  const theme = buildOmpTheme(shot)
  const terminal = buildOrcaTerminalTheme(shot).terminal
  if (!Number.isFinite(seconds) || seconds <= 0) throw new Error('seconds must be positive')
  const agent = fs.mkdtempSync(join(tmpdir(), 'terminal-shoot-'))
  try {
    fs.mkdirSync(join(agent, 'themes'))
    fs.mkdirSync(join(agent, 'sessions'))
    const copy = join(agent, 'sessions', basename(session))
    fs.copyFileSync(session, copy)
    fs.writeFileSync(join(agent, 'config.yml'), 'symbolPreset: nerd\ntheme:\n  dark: preview-dark\n  light: preview-light\n')
    writePreviewThemes(agent, shot, theme)
    return { bytes: runCapture(copy, agent, seconds, slot), theme, terminal }
  } finally {
    fs.rmSync(agent, { recursive: true, force: true })
  }
}

const lineText = (buffer: IBuffer, index: number) => buffer.getLine(index)?.translateToString(true) ?? ''

function lastContentLine(buffer: IBuffer): number {
  let last = buffer.length - 1
  while (last >= 0 && !lineText(buffer, last).trim()) last--
  if (last < 0) throw new Error('Blank terminal render')
  return last
}

function firstShownLine(buffer: IBuffer, last: number, lines: number, anchor: string | undefined): number {
  if (!anchor) return Math.max(0, last - lines + 1)
  let match = last
  while (match >= 0 && !lineText(buffer, match).includes(anchor)) match--
  if (match < 0) throw new Error(`Anchor not found in terminal capture: ${anchor}`)
  return Math.max(0, match - Math.floor(lines * 0.3))
}

export async function renderHtml(request: RenderRequest): Promise<string> {
  const { bytes, terminal, lines = 70, anchor } = request
  if (!Number.isSafeInteger(lines) || lines <= 0) throw new Error('lines must be a positive integer')
  if (!bytes.length) throw new Error('Empty omp capture')
  const options = { cols: columns, rows, scrollback: 20000, theme: terminal, fontSize, fontFamily: '"SF Mono", Menlo, monospace', allowProposedApi: true }
  const emulator = new Terminal(options)
  const addon = new SerializeAddon()
  emulator.loadAddon(addon)
  try {
    await new Promise<void>((done) => emulator.write(bytes, done))
    const buffer = emulator.buffer.active
    const last = lastContentLine(buffer)
    const start = firstShownLine(buffer, last, lines, anchor)
    const end = Math.min(last, start + lines - 1)
    const serialized = addon.serializeAsHTML({ includeGlobalBackground: true, range: { startLine: start, endLine: end, startCol: 0 } })
    const html = serialized.split('<!--StartFragment-->')[1]?.split('<!--EndFragment-->')[0]
    if (html === undefined) throw new Error('The serialized terminal has no HTML fragment')
    return `<!doctype html><html><head><meta charset="utf-8"><title>omp terminal preview</title><style>html,body{margin:0;background:${terminal.background};color:${terminal.foreground}}body{padding:12px}pre{margin:0;line-height:${lineHeight}}</style></head><body>${html}</body></html>`
  } finally {
    emulator.dispose()
  }
}

const OPTION_KEYS = new Set(['palette', 'variant', 'mode', 'slot', 'lines', 'anchor'])

function parseOptions(args: string[]): ShootOptions {
  const values = new Map<string, string>()
  let all = false
  for (let index = 0; index < args.length; index++) {
    const arg = args[index] ?? ''
    if (arg === '--all') { all = true; continue }
    const key = arg.slice(2)
    if (!arg.startsWith('--') || !OPTION_KEYS.has(key)) throw new Error(`Unknown option: ${arg}`)
    const value = args[++index]
    if (value === undefined || value.startsWith('--')) throw new Error(`Missing value for --${key}`)
    values.set(key, value)
  }
  return optionsFrom(values, all)
}

function optionsFrom(values: Map<string, string>, all: boolean): ShootOptions {
  return {
    palette: values.get('palette') ?? 'catppuccin',
    variant: values.get('variant'),
    mode: values.get('mode') ?? 'gutter',
    slot: values.get('slot') ?? 'both',
    lines: Number(values.get('lines') ?? 70),
    anchor: values.get('anchor'),
    all,
  }
}

function validate(options: ShootOptions): void {
  if (!terminalPalette(options.palette)) throw new Error(`Unknown terminal palette: ${options.palette}`)
  if (!terminalMode(options.mode)) throw new Error(`Unknown terminal mode: ${options.mode}`)
  if (!['dark', 'light', 'both'].includes(options.slot)) throw new Error(`Unknown terminal slot: ${options.slot}`)
  if (!Number.isSafeInteger(options.lines) || options.lines <= 0) throw new Error('lines must be a positive integer')
}

function plannedShots(options: ShootOptions): Shot[] {
  const modes = options.all ? [...TERMINAL_MODES] : [options.mode]
  const slots = options.all || options.slot === 'both' ? [...SLOTS] : [options.slot]
  const variantsFor = (slot: string) => (options.all ? slotVariants(options.palette, slot) : [options.variant ?? firstVariant(options.palette, slot)])
  return modes.flatMap((mode) => slots.flatMap((slot) => variantsFor(slot).map((variant) => ({ palette: options.palette, variant, mode, slot }))))
}

interface ShootTarget {
  session: string
  directory: string
  profile: string
  options: ShootOptions
}

async function shoot(target: ShootTarget, shot: Shot): Promise<void> {
  const { bytes, terminal } = await captureSession({ session: resolve(target.session), ...shot })
  const html = join(target.directory, `${shot.palette}-${shot.variant}-${shot.mode}-${shot.slot}.html`)
  const png = html.slice(0, -5) + '.png'
  const rendered = await renderHtml({ bytes, terminal, lines: target.options.lines, anchor: target.options.anchor })
  fs.writeFileSync(html, rendered)
  const height = Math.ceil(target.options.lines * fontSize * lineHeight + 24)
  const result = spawnSync(chrome, ['--headless=new', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1', `--user-data-dir=${target.profile}`, `--window-size=1260,${height}`, '--virtual-time-budget=1000', `--screenshot=${png}`, pathToFileURL(html).href], { encoding: 'utf8', timeout: 30000 })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`Chrome failed: ${result.stderr}`)
  const image = fs.readFileSync(png)
  if (image.length <= 24 || image.toString('ascii', 12, 16) !== 'IHDR') throw new Error(`Invalid Chrome PNG: ${png}`)
  console.log(JSON.stringify({ png, html, ...shot, bytes: bytes.length, lines: target.options.lines }))
}

async function main(): Promise<void> {
  const args = process.argv.slice(2)
  const session = args.shift()
  const output = args.shift()
  if (!session || !output || session.startsWith('--') || output.startsWith('--')) throw new Error('Usage: terminal-shoot.ts <session.jsonl> <out-dir> [--palette p] [--variant v] [--mode m] [--slot dark|light|both] [--lines N] [--anchor TEXT] [--all]')
  const options = parseOptions(args)
  validate(options)
  fs.accessSync(chrome, fs.constants.X_OK)
  const shots = plannedShots(options)
  for (const shot of shots) buildOmpTheme(shot)
  const directory = resolve(output)
  fs.mkdirSync(directory, { recursive: true })
  const profile = fs.mkdtempSync(join(tmpdir(), 'terminal-shoot-chrome-'))
  try {
    for (const shot of shots) await shoot({ session, directory, profile, options }, shot)
  } finally {
    fs.rmSync(profile, { recursive: true, force: true })
  }
}

if (import.meta.url === pathToFileURL(resolve(process.argv[1] ?? '')).href) {
  main().catch((error) => { console.error(error instanceof Error ? error.message : String(error)); process.exitCode = 1 })
}
