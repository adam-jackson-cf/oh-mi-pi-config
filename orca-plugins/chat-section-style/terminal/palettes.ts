// Static source data; no installed-plugin or generated-theme reads at runtime.
// PALETTES[palette][variant] = { appTheme: { pluginKey, file }, ansi, roles }.
// ansi contains the 16 named ANSI entries, verbatim (lowercase hex) from the sources below.
// roles: bg/fg = app background/foreground; mantle = card; surface0 = secondary;
// surface1 = border; surface2 = input (alpha tokens composited over bg in sRGB).
// Catppuccin keeps the reviewed surface steps and syntax inks instead.
// subtle = chat's secondary ink override, otherwise app muted-foreground;
// activity = app muted-foreground, accent = chat omp-accent otherwise app primary;
// userMessage = app chat-user-message, otherwise 16% accent / 84% bg in sRGB.
// Media and ID roles = palettes/<palette>.css embed-* and omp-id-*-ink values:
// dark uses .dark; light uses the non-editorial darkened embed overrides.
// Terminal area inks are tuned within these hue families for ≥0.05 OKLab separation,
// including the 25% decision-tinted reasoning ink and editorial's 15% fade.
// Syntax roles = paired app tokenColors' comment, keyword, entity.name.function,
// variable, string, constant.numeric, support.type, keyword.operator, punctuation.
// GitHub uses variable.other / constant / support; Ayu uses punctuation.separator.
// Unspecified operator/punctuation syntax inherits the app foreground, as in the app.
// table and diagram remain available as source roles but omp has no dedicated tokens.

export const PALETTES = {
  // Catppuccin ANSI: Orca 1.4.248 bundle, extracted at $TMPDIR/orca-harness-1.4.248/
  // out/shared/terminal-themes/popular-dark-core.js (Catppuccin Mocha) and
  // out/shared/terminal-themes/popular-light.js (Catppuccin Latte).
  // Roles: ../palettes/catppuccin.css and ../../catppuccin-sections/themes/{mocha,latte}.json;
  // Surface steps and syntax inks retain the former omp Catppuccin theme data.
  catppuccin: {
    "mocha": {
      "appTheme": {
        "pluginKey": "local.catppuccin-sections",
        "file": "mocha.json"
      },
      "ansi": {
        "black": "#45475a",
        "red": "#f38ba8",
        "green": "#a6e3a1",
        "yellow": "#f9e2af",
        "blue": "#89b4fa",
        "magenta": "#f5c2e7",
        "cyan": "#94e2d5",
        "white": "#bac2de",
        "brightBlack": "#585b70",
        "brightRed": "#f38ba8",
        "brightGreen": "#a6e3a1",
        "brightYellow": "#f9e2af",
        "brightBlue": "#89b4fa",
        "brightMagenta": "#f5c2e7",
        "brightCyan": "#94e2d5",
        "brightWhite": "#a6adc8"
      },
      "roles": {
        "bg": "#1e1e2e",
        "fg": "#cdd6f4",
        "mantle": "#181825",
        "surface0": "#313244",
        "surface1": "#45475a",
        "surface2": "#585b70",
        "subtle": "#9a9a9d",
        "activity": "#80b5d5",
        "accent": "#a5addc",
        "userMessage": "#40446a",
        "code": "#74c7ec",
        "command": "#a6e3a1",
        "table": "#f9e2af",
        "diagram": "#f5c2e7",
        "key": "#98bffb",
        "gap": "#f598b1",
        "suggestion": "#a6e3a1",
        "related": "#94e2d5",
        "decision": "#db70ff",
        "review": "#fab387",
        "syntaxComment": "#9399b2",
        "syntaxKeyword": "#cba6f7",
        "syntaxFunction": "#89b4fa",
        "syntaxVariable": "#cdd6f4",
        "syntaxString": "#a6e3a1",
        "syntaxNumber": "#fab387",
        "syntaxType": "#f9e2af",
        "syntaxOperator": "#89dceb",
        "syntaxPunctuation": "#9399b2"
      }
    },
    "latte": {
      "appTheme": {
        "pluginKey": "local.catppuccin-sections",
        "file": "latte.json"
      },
      "ansi": {
        "black": "#5c5f77",
        "red": "#d20f39",
        "green": "#40a02b",
        "yellow": "#df8e1d",
        "blue": "#1e66f5",
        "magenta": "#ea76cb",
        "cyan": "#179299",
        "white": "#acb0be",
        "brightBlack": "#6c6f85",
        "brightRed": "#d20f39",
        "brightGreen": "#40a02b",
        "brightYellow": "#df8e1d",
        "brightBlue": "#1e66f5",
        "brightMagenta": "#ea76cb",
        "brightCyan": "#179299",
        "brightWhite": "#bcc0cc"
      },
      "roles": {
        "bg": "#eff1f5",
        "fg": "#4c4f69",
        "mantle": "#e6e9ef",
        "surface0": "#ccd0da",
        "surface1": "#bcc0cc",
        "surface2": "#acb0be",
        "subtle": "#59595c",
        "activity": "#3a6f87",
        "accent": "#58619a",
        "userMessage": "#d0d8f9",
        "code": "#124c62",
        "command": "#2d701e",
        "table": "#885712",
        "diagram": "#924a7f",
        "key": "#1954c9",
        "gap": "#aa2445",
        "suggestion": "#2b6c1d",
        "related": "#179299",
        "decision": "#8839ef",
        "review": "#93491f",
        "syntaxComment": "#5c5f77",
        "syntaxKeyword": "#8839ef",
        "syntaxFunction": "#1954c9",
        "syntaxVariable": "#4c4f69",
        "syntaxString": "#2d701e",
        "syntaxNumber": "#93491f",
        "syntaxType": "#885712",
        "syntaxOperator": "#156a78",
        "syntaxPunctuation": "#5c5f77"
      }
    }
  },

  // Ayu ANSI/app/syntax: installed stablyai.orca-ayu-theme version
  // d6905874d692a152d91b9adcb507bcf6f82bf9b941e3e70ab7fca7a009099dc7,
  // terminal/ayu-{dark,mirage,light}.json and themes/ayu-{dark,mirage,light}.json
  // under ~/Library/Application Support/orca/plugins/. Roles: ../palettes/ayu.css.
  ayu: {
    "dark": {
      "appTheme": {
        "pluginKey": "stablyai.orca-ayu-theme",
        "file": "ayu-dark.json"
      },
      "ansi": {
        "black": "#1b1f29",
        "red": "#f06b73",
        "green": "#70bf56",
        "yellow": "#fdb04c",
        "blue": "#4fbfff",
        "magenta": "#d0a1ff",
        "cyan": "#93e2c8",
        "white": "#c7c7c7",
        "brightBlack": "#686868",
        "brightRed": "#f07178",
        "brightGreen": "#aad94c",
        "brightYellow": "#ffb454",
        "brightBlue": "#59c2ff",
        "brightMagenta": "#d2a6ff",
        "brightCyan": "#95e6cb",
        "brightWhite": "#ffffff"
      },
      "roles": {
        "bg": "#10141c",
        "fg": "#bfbdb6",
        "mantle": "#0b0e14",
        "surface0": "#1f242e",
        "surface1": "#1b1f29",
        "surface2": "#1f242e",
        "subtle": "#bebec1",
        "activity": "#767e8f",
        "accent": "#e6b450",
        "userMessage": "#322e24",
        "code": "#5ccfe6",
        "command": "#80cd62",
        "table": "#e6b450",
        "diagram": "#b7b5f7",
        "key": "#74cdff",
        "gap": "#fda3ab",
        "suggestion": "#92d977",
        "related": "#93e2c8",
        "decision": "#db70ff",
        "review": "#f98948",
        "syntaxComment": "#5a6673",
        "syntaxKeyword": "#ff8f40",
        "syntaxFunction": "#ffb454",
        "syntaxVariable": "#bfbdb6",
        "syntaxString": "#aad94c",
        "syntaxNumber": "#d2a6ff",
        "syntaxType": "#39bae6",
        "syntaxOperator": "#f29668",
        "syntaxPunctuation": "#8b8b88"
      }
    },
    "mirage": {
      "appTheme": {
        "pluginKey": "stablyai.orca-ayu-theme",
        "file": "ayu-mirage.json"
      },
      "ansi": {
        "black": "#171b24",
        "red": "#f28273",
        "green": "#87d96c",
        "yellow": "#fcca60",
        "blue": "#6acdff",
        "magenta": "#ddbbff",
        "cyan": "#93e2c8",
        "white": "#c7c7c7",
        "brightBlack": "#686868",
        "brightRed": "#f28779",
        "brightGreen": "#d5ff80",
        "brightYellow": "#ffcd66",
        "brightBlue": "#73d0ff",
        "brightMagenta": "#dfbfff",
        "brightCyan": "#95e6cb",
        "brightWhite": "#ffffff"
      },
      "roles": {
        "bg": "#242936",
        "fg": "#cccac2",
        "mantle": "#1d222d",
        "surface0": "#333947",
        "surface1": "#171b24",
        "surface2": "#333947",
        "subtle": "#bebec1",
        "activity": "#8891a0",
        "accent": "#e6b450",
        "userMessage": "#433f3a",
        "code": "#5ccfe6",
        "command": "#80cd62",
        "table": "#e6b450",
        "diagram": "#b7b5f7",
        "key": "#74cdff",
        "gap": "#fda3ab",
        "suggestion": "#92d977",
        "related": "#93e2c8",
        "decision": "#db70ff",
        "review": "#f98948",
        "syntaxComment": "#6e7c8f",
        "syntaxKeyword": "#ffa659",
        "syntaxFunction": "#ffcd66",
        "syntaxVariable": "#cccac2",
        "syntaxString": "#d5ff80",
        "syntaxNumber": "#dfbfff",
        "syntaxType": "#5ccfe6",
        "syntaxOperator": "#f29e74",
        "syntaxPunctuation": "#9a9a98"
      }
    },
    "light": {
      "appTheme": {
        "pluginKey": "stablyai.orca-ayu-theme",
        "file": "ayu-light.json"
      },
      "ansi": {
        "black": "#000000",
        "red": "#f06b6c",
        "green": "#6cbf43",
        "yellow": "#e7a100",
        "blue": "#21a1e2",
        "magenta": "#a176cb",
        "cyan": "#4abc96",
        "white": "#c7c7c7",
        "brightBlack": "#686868",
        "brightRed": "#f07171",
        "brightGreen": "#86b300",
        "brightYellow": "#eba400",
        "brightBlue": "#22a4e6",
        "brightMagenta": "#a37acc",
        "brightCyan": "#4cbf99",
        "brightWhite": "#d1d1d1"
      },
      "roles": {
        "bg": "#fcfcfc",
        "fg": "#5c6166",
        "mantle": "#f4f6f7",
        "surface0": "#e4e6e9",
        "surface1": "#eaedef",
        "surface2": "#e4e6e9",
        "subtle": "#5b5b5e",
        "activity": "#6b7482",
        "accent": "#9c5c00",
        "userMessage": "#ede2d4",
        "code": "#16699c",
        "command": "#237530",
        "table": "#a64e00",
        "diagram": "#9c4579",
        "key": "#16699c",
        "gap": "#b0283a",
        "suggestion": "#3d6b0c",
        "related": "#0f6c58",
        "decision": "#7500a0",
        "review": "#a34000",
        "syntaxComment": "#909296",
        "syntaxKeyword": "#d47c3e",
        "syntaxFunction": "#b88c25",
        "syntaxVariable": "#5c6166",
        "syntaxString": "#7b9e1b",
        "syntaxNumber": "#a37acc",
        "syntaxType": "#579cb4",
        "syntaxOperator": "#b68780",
        "syntaxPunctuation": "#8c8f93"
      }
    }
  },

  // GitHub ANSI/app/syntax: installed stablyai.orca-github-vscode-theme version
  // 7e17af8557dd0163ee0bc74aca3a6eb227f00bdd4b2ca04a68d78d891a24afb3,
  // terminal/github-{dark-default,dark-dimmed,light-default}.json and matching themes/
  // under ~/Library/Application Support/orca/plugins/. Roles: ../palettes/github.css.
  github: {
    "dark": {
      "appTheme": {
        "pluginKey": "stablyai.orca-github-vscode-theme",
        "file": "github-dark-default.json"
      },
      "ansi": {
        "black": "#484f58",
        "red": "#ff7b72",
        "green": "#3fb950",
        "yellow": "#d29922",
        "blue": "#58a6ff",
        "magenta": "#bc8cff",
        "cyan": "#39c5cf",
        "white": "#b1bac4",
        "brightBlack": "#6e7681",
        "brightRed": "#ffa198",
        "brightGreen": "#56d364",
        "brightYellow": "#e3b341",
        "brightBlue": "#79c0ff",
        "brightMagenta": "#d2a8ff",
        "brightCyan": "#56d4dd",
        "brightWhite": "#ffffff"
      },
      "roles": {
        "bg": "#0d1117",
        "fg": "#e6edf3",
        "mantle": "#010409",
        "surface0": "#282e33",
        "surface1": "#30363d",
        "surface2": "#30363d",
        "subtle": "#bebec1",
        "activity": "#7d8590",
        "accent": "#68a9e4",
        "userMessage": "#1c2938",
        "code": "#5fb8c4",
        "command": "#62bd70",
        "table": "#c9a45c",
        "diagram": "#cf93c3",
        "key": "#79c0ff",
        "gap": "#ffa198",
        "suggestion": "#86d98f",
        "related": "#56d4dd",
        "decision": "#db70ff",
        "review": "#ffa657",
        "syntaxComment": "#8b949e",
        "syntaxKeyword": "#ff7b72",
        "syntaxFunction": "#d2a8ff",
        "syntaxVariable": "#e6edf3",
        "syntaxString": "#a5d6ff",
        "syntaxNumber": "#79c0ff",
        "syntaxType": "#79c0ff",
        "syntaxOperator": "#e6edf3",
        "syntaxPunctuation": "#e6edf3"
      }
    },
    "dimmed": {
      "appTheme": {
        "pluginKey": "stablyai.orca-github-vscode-theme",
        "file": "github-dark-dimmed.json"
      },
      "ansi": {
        "black": "#545d68",
        "red": "#f47067",
        "green": "#57ab5a",
        "yellow": "#c69026",
        "blue": "#539bf5",
        "magenta": "#b083f0",
        "cyan": "#39c5cf",
        "white": "#909dab",
        "brightBlack": "#636e7b",
        "brightRed": "#ff938a",
        "brightGreen": "#6bc46d",
        "brightYellow": "#daaa3f",
        "brightBlue": "#6cb6ff",
        "brightMagenta": "#dcbdfb",
        "brightCyan": "#56d4dd",
        "brightWhite": "#cdd9e5"
      },
      "roles": {
        "bg": "#22272e",
        "fg": "#adbac7",
        "mantle": "#1c2128",
        "surface0": "#3d444d",
        "surface1": "#444c56",
        "surface2": "#444c56",
        "subtle": "#bebec1",
        "activity": "#848f9b",
        "accent": "#68a9e4",
        "userMessage": "#2d3c4b",
        "code": "#5fb8c4",
        "command": "#62bd70",
        "table": "#c9a45c",
        "diagram": "#cf93c3",
        "key": "#79c0ff",
        "gap": "#ffa198",
        "suggestion": "#86d98f",
        "related": "#56d4dd",
        "decision": "#db70ff",
        "review": "#ffa657",
        "syntaxComment": "#768390",
        "syntaxKeyword": "#f47067",
        "syntaxFunction": "#dcbdfb",
        "syntaxVariable": "#adbac7",
        "syntaxString": "#96d0ff",
        "syntaxNumber": "#6cb6ff",
        "syntaxType": "#6cb6ff",
        "syntaxOperator": "#adbac7",
        "syntaxPunctuation": "#adbac7"
      }
    },
    "light": {
      "appTheme": {
        "pluginKey": "stablyai.orca-github-vscode-theme",
        "file": "github-light-default.json"
      },
      "ansi": {
        "black": "#24292f",
        "red": "#cf222e",
        "green": "#116329",
        "yellow": "#4d2d00",
        "blue": "#0969da",
        "magenta": "#8250df",
        "cyan": "#1b7c83",
        "white": "#6e7781",
        "brightBlack": "#57606a",
        "brightRed": "#a40e26",
        "brightGreen": "#1a7f37",
        "brightYellow": "#633c01",
        "brightBlue": "#218bff",
        "brightMagenta": "#a475f9",
        "brightCyan": "#3192aa",
        "brightWhite": "#8c959f"
      },
      "roles": {
        "bg": "#ffffff",
        "fg": "#1f2328",
        "mantle": "#f6f8fa",
        "surface0": "#ebecf0",
        "surface1": "#d0d7de",
        "surface2": "#d0d7de",
        "subtle": "#5f5f62",
        "activity": "#656d76",
        "accent": "#0550ae",
        "userMessage": "#d7e3f2",
        "code": "#0f7380",
        "command": "#16702f",
        "table": "#875a00",
        "diagram": "#a5407f",
        "key": "#0550ae",
        "gap": "#a40e26",
        "suggestion": "#116329",
        "related": "#006452",
        "decision": "#7800bf",
        "review": "#953800",
        "syntaxComment": "#6e7781",
        "syntaxKeyword": "#cf222e",
        "syntaxFunction": "#8250df",
        "syntaxVariable": "#1f2328",
        "syntaxString": "#0a3069",
        "syntaxNumber": "#0550ae",
        "syntaxType": "#0550ae",
        "syntaxOperator": "#1f2328",
        "syntaxPunctuation": "#1f2328"
      }
    }
  },

  // VS Code ANSI: ansiColorMap defaults (dark/light, not high contrast), retrieved 2026-11-08:
  // https://github.com/microsoft/vscode/blob/main/src/vs/workbench/contrib/terminal/common/terminalColorRegistry.ts
  // App/syntax: installed stablyai.orca-vscode-default-themes version
  // ce11b29f4c21bedeaa3613e7ce9c4a6863f800e6945754ad1e36d04e24991139,
  // themes/vs-code-{dark,light}-modern.json under ~/Library/Application Support/orca/plugins/.
  // Roles: ../palettes/vscode.css. Modern app themes pair with the shared default ANSI table.
  vscode: {
    "dark": {
      "appTheme": {
        "pluginKey": "stablyai.orca-vscode-default-themes",
        "file": "vs-code-dark-modern.json"
      },
      "ansi": {
        "black": "#000000",
        "red": "#cd3131",
        "green": "#0dbc79",
        "yellow": "#e5e510",
        "blue": "#2472c8",
        "magenta": "#bc3fbc",
        "cyan": "#11a8cd",
        "white": "#e5e5e5",
        "brightBlack": "#666666",
        "brightRed": "#f14c4c",
        "brightGreen": "#23d18b",
        "brightYellow": "#f5f543",
        "brightBlue": "#3b8eea",
        "brightMagenta": "#d670d6",
        "brightCyan": "#29b8db",
        "brightWhite": "#e5e5e5"
      },
      "roles": {
        "bg": "#1f1f1f",
        "fg": "#cccccc",
        "mantle": "#181818",
        "surface0": "#1f1f1f",
        "surface1": "#2b2b2b",
        "surface2": "#3c3c3c",
        "subtle": "#bebec1",
        "activity": "#9d9d9d",
        "accent": "#78aada",
        "userMessage": "#2d353d",
        "code": "#61bfec",
        "command": "#7cc4a0",
        "table": "#e2cd88",
        "diagram": "#d092cb",
        "key": "#a3aef6",
        "gap": "#ec9494",
        "suggestion": "#9fcf8c",
        "related": "#4ec9b0",
        "decision": "#db70ff",
        "review": "#e6b484",
        "syntaxComment": "#6a9955",
        "syntaxKeyword": "#569cd6",
        "syntaxFunction": "#dcdcaa",
        "syntaxVariable": "#9cdcfe",
        "syntaxString": "#ce9178",
        "syntaxNumber": "#b5cea8",
        "syntaxType": "#4ec9b0",
        "syntaxOperator": "#d4d4d4",
        "syntaxPunctuation": "#cccccc"
      }
    },
    "light": {
      "appTheme": {
        "pluginKey": "stablyai.orca-vscode-default-themes",
        "file": "vs-code-light-modern.json"
      },
      "ansi": {
        "black": "#000000",
        "red": "#cd3131",
        "green": "#107c10",
        "yellow": "#949800",
        "blue": "#0451a5",
        "magenta": "#bc05bc",
        "cyan": "#0598bc",
        "white": "#555555",
        "brightBlack": "#666666",
        "brightRed": "#f14c4c",
        "brightGreen": "#14ce14",
        "brightYellow": "#b5ba00",
        "brightBlue": "#3b8eea",
        "brightMagenta": "#d670d6",
        "brightCyan": "#29b8db",
        "brightWhite": "#a5a5a5"
      },
      "roles": {
        "bg": "#ffffff",
        "fg": "#3b3b3b",
        "mantle": "#f8f8f8",
        "surface0": "#e5e5e5",
        "surface1": "#e5e5e5",
        "surface2": "#cecece",
        "subtle": "#3b3b3b",
        "activity": "#3b3b3b",
        "accent": "#005fb8",
        "userMessage": "#d6e5f4",
        "code": "#123c7b",
        "command": "#1f7a55",
        "table": "#8a5f0a",
        "diagram": "#9a2fc0",
        "key": "#2a4592",
        "gap": "#a31515",
        "suggestion": "#2f6f12",
        "related": "#1c6a80",
        "decision": "#7c1fa0",
        "review": "#8f4600",
        "syntaxComment": "#008000",
        "syntaxKeyword": "#0000ff",
        "syntaxFunction": "#795e26",
        "syntaxVariable": "#001080",
        "syntaxString": "#a31515",
        "syntaxNumber": "#098658",
        "syntaxType": "#267f99",
        "syntaxOperator": "#000000",
        "syntaxPunctuation": "#3b3b3b"
      }
    }
  },
}
