(() => {
  const KIND_BY_LETTER = new Map(Object.entries({ K: 'key', C: 'gap', S: 'suggestion', R: 'related', A: 'decision', F: 'review' }))
  const ID_PATTERN = /^\s*([KCSRAF])\d+\b/
  const BLOCKS = '[data-native-chat-window] [data-native-chat-quote-source] :is(li, p)'
  const SECTION_ATTRIBUTE = 'data-omp-id-section'
  const PART_ATTRIBUTE = 'data-omp-id-part'

  const previous = window.orcaIdHighlight
  previous?.observer.disconnect()
  let queued = false
  const highlighter = window.orcaIdHighlight = {
    observer: new MutationObserver(() => {
      if (queued) return
      queued = true
      requestAnimationFrame(() => {
        queued = false
        window.orcaIdHighlight?.paint()
      })
    }),
    sections: previous?.sections ?? new Set(),

    firstText(block) {
      const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
        acceptNode: (node) => (node.textContent?.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP),
      })
      const node = walker.nextNode()
      return node instanceof Text && node.parentElement?.closest('li, p') === block ? node : null
    },

    idMatch(block) {
      const node = this.firstText(block)
      const match = node && ID_PATTERN.exec(node.data)
      return node && match ? { node, match } : null
    },

    endsSection(element) {
      if (element.matches('h1, h2, h3, h4, h5, h6, hr')) return true
      const lead = element.matches('ul, ol') ? element.querySelector(':scope > li') : element.matches('p') ? element : null
      if (!lead) return false
      return Boolean(this.idMatch(lead)) || this.opensWithBoldLead(lead)
    },

    opensWithBoldLead(lead) {
      const node = this.firstText(lead)
      return lead.tagName === 'P' && lead.firstElementChild?.tagName === 'STRONG' && node?.parentElement?.closest('strong') === lead.firstElementChild
    },

    mark(marked, element, kind, part) {
      marked.add(element)
      if (element.getAttribute(SECTION_ATTRIBUTE) !== kind) element.setAttribute(SECTION_ATTRIBUTE, kind)
      if (element.getAttribute(PART_ATTRIBUTE) !== part) element.setAttribute(PART_ATTRIBUTE, part)
    },

    markSection(section, kind, marked) {
      const next = section.tagName === 'P' ? section.nextElementSibling : null
      this.mark(marked, section, kind, next && !this.endsSection(next) ? 'start' : 'whole')
      for (let element = next; element && !this.endsSection(element); element = element.nextElementSibling) {
        const last = !element.nextElementSibling || this.endsSection(element.nextElementSibling)
        this.mark(marked, element, kind, last ? 'end' : 'middle')
      }
    },

    markBlock(block, ranges, marked) {
      const found = this.idMatch(block)
      const kind = found && KIND_BY_LETTER.get(found.match[1] ?? '')
      if (!found || !kind) return
      const { node, match } = found
      const range = new Range()
      range.setStart(node, match[0].length - match[0].trimStart().length)
      range.setEnd(node, match[0].length)
      ranges.get(kind)?.push(range)
      const section = block.tagName === 'LI' ? block.parentElement : block
      if (section && !marked.has(section)) this.markSection(section, kind, marked)
    },

    emptyRanges() {
      return new Map([...KIND_BY_LETTER.values()].map((kind) => [kind, []]))
    },

    paint() {
      const ranges = this.emptyRanges()
      const stale = this.sections
      this.sections = new Set()
      for (const block of document.querySelectorAll(BLOCKS)) this.markBlock(block, ranges, this.sections)
      for (const element of stale) {
        if (this.sections.has(element)) continue
        element.removeAttribute(SECTION_ATTRIBUTE)
        element.removeAttribute(PART_ATTRIBUTE)
      }
      for (const [kind, list] of ranges) CSS.highlights.set(`omp-id-${kind}`, new Highlight(...list))
    },
  }
  highlighter.observer.observe(document.body, { childList: true, subtree: true, characterData: true })
  highlighter.paint()
})()
