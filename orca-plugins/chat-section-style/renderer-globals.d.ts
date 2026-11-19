import type { InstalledTheme } from './theme-palettes.ts'

declare global {
  interface IdMatch {
    node: Text
    match: RegExpExecArray
  }

  interface IdHighlighter {
    observer: MutationObserver
    sections: Set<Element>
    firstText(block: Element): Text | null
    idMatch(block: Element): IdMatch | null
    endsSection(element: Element): boolean
    opensWithBoldLead(lead: Element): boolean
    mark(marked: Set<Element>, element: Element, kind: string, part: string): void
    markSection(section: Element, kind: string, marked: Set<Element>): void
    markBlock(block: Element, ranges: Map<string, Range[]>, marked: Set<Element>): void
    emptyRanges(): Map<string, Range[]>
    paint(): void
  }

  interface ExpectedTheme {
    palette: string
    tokens: [string, string][]
  }

  interface PaletteAuto {
    observer: MutationObserver
    timer: number
    themes: InstalledTheme[]
    normalise(value: string): string | null
    expectedTokens(tokens: Record<string, string>): [string, string][]
    expectedThemes(): ExpectedTheme[]
    detect(): string
    schedule(): void
  }

  interface Window {
    orcaIdHighlight: IdHighlighter | undefined
    ompPaletteAuto: PaletteAuto | undefined
    ompApplyPalette: (choice: string, themes: InstalledTheme[], palettes: string[]) => string
    HARNESS_TOKENS: Record<string, Record<string, string>>
  }
}
