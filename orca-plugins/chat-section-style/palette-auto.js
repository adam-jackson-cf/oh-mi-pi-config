// Sets <html data-omp-chat-palette>. Runs in Orca's renderer; inject.mjs's hook calls
// window.__ompApplyPalette(choice, themes, palettes) after every apply.
// choice 'auto' picks the palette whose installed theme tokens best match the live page, and re-checks whenever
// Orca swaps theme (class, inline style or <head> style changes). Any other choice is applied as-is.
// themes: [{ palette, tokens: { '--background': '#…', … } }] read from the installed theme JSON files.
window.__ompApplyPalette = (choice, themes, palettes) => {
  const root = document.documentElement
  window.__ompPaletteObserver?.disconnect()
  window.__ompPaletteObserver = null
  if (choice !== 'auto') {
    root.dataset.ompChatPalette = choice
    return choice
  }

  // Normalise any CSS colour to rgba through a 1px canvas, so '#FFF', 'white' and 'rgb(…)' compare equal.
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  const normalise = (value) => {
    if (!value) return null
    ctx.clearRect(0, 0, 1, 1)
    ctx.fillStyle = '#010203'
    ctx.fillStyle = value.trim()
    if (ctx.fillStyle === '#010203' && value.trim().toLowerCase() !== '#010203') return null
    ctx.fillRect(0, 0, 1, 1)
    return ctx.getImageData(0, 0, 1, 1).data.join(',')
  }
  const expected = themes.map((theme) => ({
    palette: theme.palette,
    tokens: Object.entries(theme.tokens).map(([name, value]) => [name, normalise(value)]).filter(([, value]) => value),
  }))

  const detect = () => {
    const style = getComputedStyle(root)
    let best = null
    let bestScore = 0
    for (const theme of expected) {
      if (!theme.tokens.length) continue
      const matched = theme.tokens.filter(([name, value]) => normalise(style.getPropertyValue(name)) === value).length
      const score = matched / theme.tokens.length
      if (score > bestScore) { best = theme; bestScore = score }
    }
    // Below 60% of a theme's tokens the page is running some other theme: keep the default palette.
    const pick = best && bestScore >= 0.6 && palettes.includes(best.palette) ? best.palette : palettes.includes('catppuccin') ? 'catppuccin' : palettes[0]
    if (root.dataset.ompChatPalette !== pick) root.dataset.ompChatPalette = pick
    return pick
  }

  let timer = null
  const observer = new MutationObserver(() => {
    clearTimeout(timer)
    timer = setTimeout(detect, 120)
  })
  observer.observe(root, { attributes: true, attributeFilter: ['class', 'style'] })
  observer.observe(document.head, { childList: true, subtree: true, characterData: true, attributes: true })
  window.__ompPaletteObserver = observer
  return detect()
}
