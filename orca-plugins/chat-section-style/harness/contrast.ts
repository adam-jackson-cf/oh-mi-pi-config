#!/usr/bin/env bun
import { $ } from 'bun'
import { withChrome, type DevToolsPage } from './devtools.ts'

type Rgba = [number, number, number, number]

interface ContrastRow {
  kind: string
  what: string
  ratio: number
  faded: boolean
}

interface ThemeShot {
  theme: string
  variant: string | undefined
}

const EXTRA_SHOTS = new Map<string, ThemeShot[]>([
  ['ayu', [{ theme: 'dark', variant: 'ayu-mirage' }]],
  ['github', [{ theme: 'dark', variant: 'github-dark-dimmed' }]],
])
const palette = process.argv[2] ?? ''
const modes = process.argv.length > 3 ? process.argv.slice(3) : ['document', 'split-cards', 'gutter', 'editorial']
if (!palette) { console.error('Usage: bun harness/contrast.ts <palette> [mode ...]'); process.exit(2) }

const version = (await $`plutil -extract CFBundleShortVersionString raw /Applications/Orca.app/Contents/Info.plist`.text()).trim()
const pagePath = `${(process.env['TMPDIR'] || '/tmp/').replace(/\/?$/, '/')}orca-harness-${version}/out/renderer/transcript.html`
if (!(await Bun.file(pagePath).exists())) { console.error(`Missing ${pagePath}; run harness/shoot.sh first`); process.exit(2) }

function over(top: Rgba, under: Rgba, alpha = top[3]): Rgba {
  const blend = (index: 0 | 1 | 2) => top[index] * alpha + under[index] * (1 - alpha)
  return [blend(0), blend(1), blend(2), 1]
}

function linearChannel(value: number): number {
  const channel = value / 255
  return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
}

function luminance([r, g, b]: Rgba): number {
  return 0.2126 * linearChannel(r) + 0.7152 * linearChannel(g) + 0.0722 * linearChannel(b)
}

function ratio(a: Rgba, b: Rgba): number {
  const x = Math.max(luminance(a), luminance(b))
  const y = Math.min(luminance(a), luminance(b))
  return (x + 0.05) / (y + 0.05)
}

function opacity(el: Element): number {
  let total = 1
  for (let e: Element | null = el; e; e = e.parentElement) total *= parseFloat(getComputedStyle(e).opacity)
  return total
}

function fadedByLayout(el: Element): boolean {
  return opacity(el) < 1
}

function label(el: HTMLElement): string {
  const section = el.dataset['ompIdSection']
  return `${el.tagName.toLowerCase()}${section ? `[${section}]` : ''} "${(el.textContent || '').trim().slice(0, 32)}"`
}

function audit(): ContrastRow[] {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) throw new Error('A 2D canvas is required to resolve painted colours')
  const probe = document.createElement('span')
  document.body.appendChild(probe)
  const rgba = (value: string): Rgba => {
    probe.style.color = ''
    probe.style.color = value
    ctx.clearRect(0, 0, 1, 1)
    ctx.fillStyle = getComputedStyle(probe).color
    ctx.fillRect(0, 0, 1, 1)
    const [r = 0, g = 0, b = 0, a = 0] = ctx.getImageData(0, 0, 1, 1).data
    return [r, g, b, a / 255]
  }
  const background = (el: Element): Rgba => {
    const layers: Rgba[] = []
    for (let e: Element | null = el; e; e = e.parentElement) {
      const colour = rgba(getComputedStyle(e).backgroundColor)
      if (colour[3] > 0) layers.push(colour)
      if (colour[3] >= 1) break
    }
    return layers.toReversed().reduce<Rgba>((base, layer) => over(layer, base), [255, 255, 255, 1])
  }
  const measure = (fg: Rgba, el: Element) => {
    const bg = background(el)
    return ratio(over(fg, bg, fg[3] * opacity(el)), bg)
  }
  const pseudoRows = (el: HTMLElement): ContrastRow[] =>
    ['::before', '::after'].flatMap((pseudo) => {
      const style = getComputedStyle(el, pseudo)
      if (!/[A-Za-z]{2,}/.test(style.content) || style.content === 'none' || style.content === 'normal') return []
      return [{ kind: 'label', what: `${label(el)}${pseudo} ${style.content.slice(0, 24)}`, ratio: measure(rgba(style.color), el), faded: fadedByLayout(el) }]
    })
  const elementRows = (el: HTMLElement): ContrastRow[] => {
    const box = el.getBoundingClientRect()
    if (!box.width || !box.height || getComputedStyle(el).visibility === 'hidden') return []
    const hasText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent?.trim())
    const text = hasText ? [{ kind: 'text', what: label(el), ratio: measure(rgba(getComputedStyle(el).color), el), faded: fadedByLayout(el) }] : []
    return [...text, ...pseudoRows(el)]
  }
  const highlightRows = (): ContrastRow[] => {
    const rows: ContrastRow[] = []
    for (const [name, highlight] of CSS.highlights) {
      for (const range of highlight) {
        const el = range.startContainer.parentElement
        if (!el) continue
        const style = getComputedStyle(el, `::highlight(${name})`)
        const bg = over(rgba(style.backgroundColor), background(el))
        const fg = rgba(style.color)
        rows.push({ kind: 'id', what: `${name} "${String(range).slice(0, 12)}" in ${label(el)}`, ratio: ratio(over(fg, bg, fg[3] * opacity(el)), bg), faded: fadedByLayout(el) })
      }
    }
    return rows
  }
  const rows = [...[...document.querySelectorAll<HTMLElement>('#chat-root *')].flatMap(elementRows), ...highlightRows()]
  probe.remove()
  return rows
}

const PAGE_HELPERS = [over, linearChannel, luminance, ratio, opacity, fadedByLayout, label]
const AUDIT_EXPRESSION = `(() => {\n${PAGE_HELPERS.map((helper) => helper.toString()).join('\n')}\nreturn (${audit})()\n})()`

async function auditShot(page: DevToolsPage, shot: ThemeShot, mode: string): Promise<boolean> {
  const url = `file://${pagePath}?mode=${mode}&theme=${shot.theme}&palette=${palette}${shot.variant ? `&variant=${shot.variant}` : ''}`
  await page.call('Page.navigate', { url })
  await Bun.sleep(900)
  const rows = ((await page.evaluate<ContrastRow[]>(AUDIT_EXPRESSION)) ?? []).filter((row) => row.ratio < 4.5)
  const seen = new Set<string>()
  let failed = false
  for (const row of rows) {
    const line = `${row.ratio.toFixed(2)}  ${row.kind.padEnd(5)} ${row.faded ? 'faded ' : ''}${row.what}`
    if (seen.has(line)) continue
    seen.add(line)
    if (!row.faded) failed = true
    console.log(`${palette}/${shot.variant ?? shot.theme}/${mode}  ${line}`)
  }
  if (!rows.length) console.log(`${palette}/${shot.variant ?? shot.theme}/${mode}  ok`)
  return failed
}

async function auditAll(page: DevToolsPage): Promise<boolean> {
  const shots: ThemeShot[] = [{ theme: 'light', variant: undefined }, { theme: 'dark', variant: undefined }, ...(EXTRA_SHOTS.get(palette) ?? [])]
  let failed = false
  for (const shot of shots) {
    for (const mode of modes) failed = (await auditShot(page, shot, mode)) || failed
  }
  return failed
}

const failed = await withChrome(`/tmp/orca-contrast-chrome-${process.pid}`, ['--allow-file-access-from-files', '--window-size=1500,3000'], auditAll)
process.exitCode = failed ? 1 : 0
