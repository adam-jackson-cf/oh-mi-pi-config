window.ompApplyPalette = (choice, themes, palettes) => {
  const root = document.documentElement
  window.ompPaletteAuto?.observer.disconnect()
  window.ompPaletteAuto = undefined
  if (choice !== 'auto') {
    root.dataset['ompChatPalette'] = choice
    return choice
  }

  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) throw new Error('A 2D canvas is required to compare theme colours')
  const auto = window.ompPaletteAuto = {
    observer: new MutationObserver(() => window.ompPaletteAuto?.schedule()),
    timer: 0,
    themes,
    normalise(value) {
      if (!value) return null
      ctx.clearRect(0, 0, 1, 1)
      ctx.fillStyle = '#010203'
      ctx.fillStyle = value.trim()
      if (ctx.fillStyle === '#010203' && value.trim().toLowerCase() !== '#010203') return null
      ctx.fillRect(0, 0, 1, 1)
      return ctx.getImageData(0, 0, 1, 1).data.join(',')
    },
    expectedTokens(tokens) {
      return Object.entries(tokens).flatMap(([name, value]) => {
        const colour = this.normalise(value)
        return colour ? [[name, colour]] : []
      })
    },
    expectedThemes() {
      return this.themes.map((theme) => ({ palette: theme.palette, tokens: this.expectedTokens(theme.tokens) }))
    },
    detect() {
      const style = getComputedStyle(root)
      const best = this.expectedThemes()
        .filter((theme) => theme.tokens.length)
        .map((theme) => ({
          palette: theme.palette,
          score: theme.tokens.filter(([name, value]) => this.normalise(style.getPropertyValue(name)) === value).length / theme.tokens.length,
        }))
        .reduce((top, entry) => (entry.score > top.score ? entry : top), { palette: '', score: 0 })
      const fallback = palettes.includes('catppuccin') ? 'catppuccin' : (palettes[0] ?? 'catppuccin')
      const pick = best.score >= 0.6 && palettes.includes(best.palette) ? best.palette : fallback
      if (root.dataset['ompChatPalette'] !== pick) root.dataset['ompChatPalette'] = pick
      return pick
    },
    schedule() {
      window.clearTimeout(this.timer)
      this.timer = window.setTimeout(() => this.detect(), 120)
    },
  }
  auto.observer.observe(root, { attributes: true, attributeFilter: ['class', 'style'] })
  auto.observer.observe(document.head, { childList: true, subtree: true, characterData: true, attributes: true })
  return auto.detect()
}
