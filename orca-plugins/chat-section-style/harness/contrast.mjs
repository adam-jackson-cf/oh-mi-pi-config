#!/usr/bin/env bun
// Usage: bun harness/contrast.mjs <palette> [mode ...]
// Measures rendered WCAG contrast of every text run, text ::before/::after label and ::highlight(omp-id-*) ID in the
// harness transcript, for each theme variant the palette pairs with (light, dark, plus Ayu Mirage / GitHub Dimmed).
// Prints each distinct text below 4.5:1. "faded" rows sit in an older turn or a settled tool row whose opacity the
// layout lowers on purpose (hover restores it). Exit code 1 when any non-faded row fails.
// Run harness/shoot.sh once first so the harness page exists. Each run starts its own headless Chrome on an
// OS-assigned debugging port, so parallel runs never share a browser.
import { $ } from 'bun'

const VARIANTS = { ayu: [['dark', 'ayu-mirage']], github: [['dark', 'github-dark-dimmed']] }
const palette = process.argv[2]
const modes = process.argv.length > 3 ? process.argv.slice(3) : ['document', 'split-cards', 'gutter', 'editorial']
if (!palette) { console.error('Usage: bun harness/contrast.mjs <palette> [mode ...]'); process.exit(2) }

const version = (await $`plutil -extract CFBundleShortVersionString raw /Applications/Orca.app/Contents/Info.plist`.text()).trim()
const pagePath = `${(process.env.TMPDIR || '/tmp/').replace(/\/?$/, '/')}orca-harness-${version}/out/renderer/harness.html`
if (!(await Bun.file(pagePath).exists())) { console.error(`Missing ${pagePath}; run harness/shoot.sh first`); process.exit(2) }

const profile = `/tmp/orca-contrast-chrome-${process.pid}`
const chrome = Bun.spawn(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--headless=new', '--disable-gpu',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--allow-file-access-from-files', '--window-size=1500,3000', 'about:blank'],
{ stdout: 'ignore', stderr: 'ignore' })
let ws
try {
  // Chrome writes its chosen port to DevToolsActivePort in the profile directory.
  let port
  for (let i = 0; i < 100 && !port; i++) {
    try { port = (await Bun.file(`${profile}/DevToolsActivePort`).text()).split('\n')[0].trim() } catch { await Bun.sleep(100) }
  }
  let targets
  for (let i = 0; i < 50 && !targets; i++) { try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json() } catch { await Bun.sleep(100) } }
  ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl)
  await new Promise((resolve) => { ws.onopen = resolve })
  let nextId = 1
  const pending = new Map()
  ws.onmessage = (event) => { const m = JSON.parse(event.data); if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } }
  const cdp = (method, params = {}) => new Promise((resolve) => { const id = nextId++; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })) })

  let failed = false
  for (const [theme, variant] of [['light'], ['dark'], ...(VARIANTS[palette] ?? [])]) {
    for (const mode of modes) {
      const url = `file://${pagePath}?mode=${mode}&theme=${theme}&palette=${palette}${variant ? `&variant=${variant}` : ''}`
      await cdp('Page.navigate', { url })
      await Bun.sleep(900)
      const reply = await cdp('Runtime.evaluate', { expression: `(${audit})()`, returnByValue: true })
      if (reply.result.exceptionDetails) throw new Error(JSON.stringify(reply.result.exceptionDetails))
      const rows = reply.result.result.value.filter((row) => row.ratio < 4.5)
      const seen = new Set()
      for (const row of rows) {
        const line = `${row.ratio.toFixed(2)}  ${row.kind.padEnd(5)} ${row.faded ? 'faded ' : ''}${row.what}`
        if (seen.has(line)) continue
        seen.add(line)
        if (!row.faded) failed = true
        console.log(`${palette}/${variant ?? theme}/${mode}  ${line}`)
      }
      if (!rows.length) console.log(`${palette}/${variant ?? theme}/${mode}  ok`)
    }
  }
  process.exitCode = failed ? 1 : 0
} finally {
  ws?.close()
  chrome.kill()
  await chrome.exited
  await $`rm -rf ${profile}`.quiet()
}

// Runs in the page. Colours are resolved through a canvas so color-mix() and alpha composite exactly as painted.
function audit() {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  const probe = document.createElement('span')
  document.body.appendChild(probe)
  const rgba = (value) => {
    probe.style.color = ''
    probe.style.color = value
    ctx.clearRect(0, 0, 1, 1)
    ctx.fillStyle = getComputedStyle(probe).color
    ctx.fillRect(0, 0, 1, 1)
    const d = ctx.getImageData(0, 0, 1, 1).data
    return [d[0], d[1], d[2], d[3] / 255]
  }
  const over = (top, under, alpha = top[3]) => [0, 1, 2].map((i) => top[i] * alpha + under[i] * (1 - alpha)).concat(1)
  const luminance = ([r, g, b]) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
  }
  const ratio = (a, b) => { const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05) }
  const background = (el) => {
    const layers = []
    for (let e = el; e; e = e.parentElement) {
      const c = rgba(getComputedStyle(e).backgroundColor)
      if (c[3] > 0) layers.push(c)
      if (c[3] >= 1) break
    }
    let base = [255, 255, 255, 1]
    for (const layer of layers.reverse()) base = over(layer, base)
    return base
  }
  const opacity = (el) => { let o = 1; for (let e = el; e; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity); return o }
  const fadedByLayout = (el) => opacity(el) < 1
  const label = (el) => `${el.tagName.toLowerCase()}${el.dataset.ompIdSection ? `[${el.dataset.ompIdSection}]` : ''} "${(el.textContent || '').trim().slice(0, 32)}"`
  const measure = (fg, el) => { const bg = background(el); return ratio(over(fg, bg, fg[3] * opacity(el)), bg) }
  const rows = []
  for (const el of document.querySelectorAll('#chat-root *')) {
    const box = el.getBoundingClientRect()
    if (!box.width || !box.height || getComputedStyle(el).visibility === 'hidden') continue
    if ([...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) {
      rows.push({ kind: 'text', what: label(el), ratio: measure(rgba(getComputedStyle(el).color), el), faded: fadedByLayout(el) })
    }
    for (const pseudo of ['::before', '::after']) {
      const style = getComputedStyle(el, pseudo)
      if (/[A-Za-z]{2,}/.test(style.content) && style.content !== 'none' && style.content !== 'normal') {
        rows.push({ kind: 'label', what: `${label(el)}${pseudo} ${style.content.slice(0, 24)}`, ratio: measure(rgba(style.color), el), faded: fadedByLayout(el) })
      }
    }
  }
  for (const [name, highlight] of CSS.highlights) {
    for (const range of highlight) {
      const el = range.startContainer.parentElement
      const style = getComputedStyle(el, `::highlight(${name})`)
      const bg = over(rgba(style.backgroundColor), background(el))
      const fg = rgba(style.color)
      rows.push({ kind: 'id', what: `${name} "${range.toString().slice(0, 12)}" in ${label(el)}`, ratio: ratio(over(fg, bg, fg[3] * opacity(el)), bg), faded: fadedByLayout(el) })
    }
  }
  probe.remove()
  return rows
}
