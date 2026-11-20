export const TERMINAL_MODES = ['document', 'split-cards', 'gutter', 'editorial'] as const
export type TerminalMode = (typeof TERMINAL_MODES)[number]

export interface ModeRoles {
  bg: string
  mantle: string
  successBg: string
  errorBg: string
  customBg: string
  userMessage: string
  surface1: string
  surface2: string
  code: string
  accent: string
  decision: string
  quietSubtle: string
  quietThinking: string
}

export function terminalMode(value: string): TerminalMode | undefined {
  return TERMINAL_MODES.find((mode) => mode === value)
}

function baseColors(roles: ModeRoles) {
  return {
    toolPendingBg: roles.bg,
    toolSuccessBg: roles.bg,
    toolErrorBg: roles.bg,
    userMessageBg: roles.userMessage,
    customMessageBg: roles.mantle,
    border: roles.surface1,
    mdCodeBlockBorder: roles.code,
    mdListBullet: roles.accent,
    mdHr: roles.surface1,
  }
}

const MODE_COLORS = {
  document: (roles: ModeRoles) => ({
    ...baseColors(roles),
    userMessageBg: roles.mantle,
    customMessageBg: roles.bg,
    mdCodeBlockBorder: roles.surface1,
    mdHr: roles.surface2,
  }),
  'split-cards': (roles: ModeRoles) => ({
    ...baseColors(roles),
    toolPendingBg: roles.mantle,
    toolSuccessBg: roles.successBg,
    toolErrorBg: roles.errorBg,
    customMessageBg: roles.customBg,
    border: roles.surface2,
    borderAccent: roles.decision,
  }),
  gutter: baseColors,
  editorial: (roles: ModeRoles) => ({
    ...baseColors(roles),
    muted: roles.quietSubtle,
    toolOutput: roles.quietSubtle,
    thinkingText: roles.quietThinking,
  }),
} satisfies Record<TerminalMode, (roles: ModeRoles) => Record<string, string>>

const BULLETS = { document: '•', 'split-cards': '•', gutter: '●', editorial: '◆' } satisfies Record<TerminalMode, string>

export function applyMode(colors: Map<string, string>, roles: ModeRoles, mode: TerminalMode): void {
  for (const [token, color] of Object.entries(MODE_COLORS[mode](roles))) colors.set(token, color)
}

export function modeSymbols(mode: TerminalMode) {
  const open = mode === 'gutter' || mode === 'editorial'
  const frame = (glyph: string) => (open ? ' ' : glyph)
  return {
    overrides: {
      'boxRound.topLeft': frame('╭'),
      'boxRound.topRight': frame('╮'),
      'boxRound.bottomLeft': frame('╰'),
      'boxRound.bottomRight': frame('╯'),
      'boxRound.horizontal': '─',
      'boxRound.vertical': frame('│'),
      'md.bullet': BULLETS[mode],
      'md.hrChar': '─',
      'md.quoteBorder': mode === 'editorial' ? '▏' : '│',
    },
  }
}
