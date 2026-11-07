// Colours completion-message IDs (K1, C2, S1, R1, A1, F1) at the start of chat list items and paragraphs,
// and tags the list or paragraph holding them so chat.css can tint each section by kind.
// Runs in Orca's renderer; inject.mjs re-runs it after each load and on edits.
// ID text uses the CSS Custom Highlight API (chat.css styles ::highlight(omp-id-*)), so React-owned text nodes
// are never split. Sections get only a data-omp-id-section attribute, which React neither sets nor removes.
(() => {
  const KINDS = { K: 'key', C: 'gap', S: 'suggestion', R: 'related', A: 'decision', F: 'review' }
  const ID_PATTERN = /^\s*([KCSRAF])\d+\b/
  const BLOCKS = '[data-native-chat-window] [data-native-chat-quote-source] :is(li, p)'
  const SECTION_ATTRIBUTE = 'data-omp-id-section'
  // whole: a section on its own; start, middle, end: one paragraph section spread over several blocks.
  const PART_ATTRIBUTE = 'data-omp-id-part'

  const previous = window.__orcaIdHighlight
  previous?.observer.disconnect()
  const state = { observer: null, sections: previous?.sections ?? new Set() }
  window.__orcaIdHighlight = state

  const firstText = (block) => {
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
      acceptNode: (node) => (node.data.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP)
    })
    const node = walker.nextNode()
    // Only text before any nested block counts; a nested list's first item is handled on its own.
    return node && node.parentElement.closest('li, p') === block ? node : null
  }

  const idMatch = (block) => {
    const node = firstText(block)
    const match = node && ID_PATTERN.exec(node.data)
    return match ? { node, match } : null
  }

  // A paragraph section (e.g. "K2 — …") runs on over the blocks after it until a heading, a rule,
  // another ID'd block or a bold lead-in paragraph such as "Gaps & Contradictions".
  const endsSection = (element) => {
    if (element.matches('h1, h2, h3, h4, h5, h6, hr')) return true
    const lead = element.matches('ul, ol') ? element.querySelector(':scope > li') : element.matches('p') ? element : null
    if (!lead) return false
    if (idMatch(lead)) return true
    const node = firstText(lead)
    return lead.tagName === 'P' && lead.firstElementChild?.tagName === 'STRONG' && node?.parentElement.closest('strong') === lead.firstElementChild
  }

  const mark = (marked, element, kind, part) => {
    marked.add(element)
    if (element.getAttribute(SECTION_ATTRIBUTE) !== kind) element.setAttribute(SECTION_ATTRIBUTE, kind)
    if (element.getAttribute(PART_ATTRIBUTE) !== part) element.setAttribute(PART_ATTRIBUTE, part)
  }

  const paint = () => {
    const ranges = Object.fromEntries(Object.values(KINDS).map((kind) => [kind, []]))
    const marked = new Set()
    for (const block of document.querySelectorAll(BLOCKS)) {
      const found = idMatch(block)
      if (!found) continue
      const { node, match } = found
      const kind = KINDS[match[1]]
      const range = new Range()
      const start = match[0].length - match[0].trimStart().length
      range.setStart(node, start)
      range.setEnd(node, match[0].length)
      ranges[kind].push(range)
      // A list takes the kind of its first ID'd item; a paragraph also claims the blocks that follow it.
      const section = block.tagName === 'LI' ? block.parentElement : block
      if (marked.has(section)) continue
      const next = section.tagName === 'P' ? section.nextElementSibling : null
      const continues = next && !endsSection(next)
      mark(marked, section, kind, continues ? 'start' : 'whole')
      for (let element = next; element && !endsSection(element); element = element.nextElementSibling) {
        const last = !element.nextElementSibling || endsSection(element.nextElementSibling)
        mark(marked, element, kind, last ? 'end' : 'middle')
      }
    }
    for (const stale of state.sections) {
      if (marked.has(stale)) continue
      stale.removeAttribute(SECTION_ATTRIBUTE)
      stale.removeAttribute(PART_ATTRIBUTE)
    }
    state.sections = marked
    for (const [kind, list] of Object.entries(ranges)) CSS.highlights.set(`omp-id-${kind}`, new Highlight(...list))
  }

  let queued = false
  const schedule = () => {
    if (queued) return
    queued = true
    requestAnimationFrame(() => { queued = false; paint() })
  }
  state.observer = new MutationObserver(schedule)
  state.observer.observe(document.body, { childList: true, subtree: true, characterData: true })
  paint()
})()
