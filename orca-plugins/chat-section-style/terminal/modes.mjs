// Colour and single-cell symbol overlays for the four chat layout intents.
export const TERMINAL_MODES = ['document', 'split-cards', 'gutter', 'editorial']

export function applyMode(theme, roles, mode) {
  const colors = theme.colors
  const open = mode === 'gutter' || mode === 'editorial'
  const cards = mode === 'split-cards'
  const document = mode === 'document'
  Object.assign(colors, {
    toolPendingBg: cards ? roles.mantle : roles.bg,
    toolSuccessBg: cards ? roles.successBg : roles.bg,
    toolErrorBg: cards ? roles.errorBg : roles.bg,
    userMessageBg: document ? roles.mantle : roles.userMessage,
    customMessageBg: roles.mantle,
    border: cards ? roles.surface2 : roles.surface1,
    mdCodeBlockBorder: document ? roles.surface1 : roles.code,
    mdListBullet: document ? roles.subtle : roles.accent,
    mdHr: document ? roles.surface2 : roles.surface1,
  })
  if (cards) colors.borderAccent = roles.accent
  if (mode === 'editorial') Object.assign(colors, {
    mdHeading: roles.accent, toolOutput: roles.subtle, thinkingText: roles.quietThinking,
  })
  theme.symbols = { overrides: {
    'boxRound.topLeft': open ? ' ' : '╭', 'boxRound.topRight': open ? ' ' : '╮',
    'boxRound.bottomLeft': open ? ' ' : '╰', 'boxRound.bottomRight': open ? ' ' : '╯',
    'boxRound.horizontal': '─', 'boxRound.vertical': open ? ' ' : '│',
    'md.bullet': { document: '•', 'split-cards': '•', gutter: '●', editorial: '◆' }[mode],
    'md.hrChar': '─', 'md.quoteBorder': mode === 'editorial' ? '▏' : '│',
  } }
  return theme
}
