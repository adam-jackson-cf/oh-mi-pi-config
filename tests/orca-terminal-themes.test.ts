import assert from "node:assert/strict";
import fs, { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, test } from "node:test";
import {
  SLOTS,
  TERMINAL_MODES,
  TERMINAL_PALETTES,
  VARIANTS,
  appThemeFor,
  buildOmpTheme,
  buildOrcaTerminalTheme,
  paletteVariant,
  resolveSlots,
  slotVariants,
  syncTerminalThemes,
  terminalPalette,
  terminalSlot,
} from "../orca-plugins/chat-section-style/terminal/build.ts";
import { applyMode, modeSymbols, terminalMode } from "../orca-plugins/chat-section-style/terminal/modes.ts";
import { paletteForPlugin, readThemes } from "../orca-plugins/chat-section-style/theme-palettes.ts";

const DECORATIVE = new Set([
  "border", "borderAccent", "borderMuted", "mdHr", "mdQuoteBorder", "mdCodeBlockBorder",
  "thinkingOff", "thinkingMinimal", "dim", "statusLineSep",
]);

function linear(channel: number): number {
  const value = channel / 255;
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function luminance(color: string): number {
  const [r, g, b] = [1, 3, 5].map(index => linear(parseInt(color.slice(index, index + 2), 16)));
  return 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0);
}

function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].toSorted((x, y) => y - x);
  return ((light ?? 0) + 0.05) / ((dark ?? 0) + 0.05);
}

function* everyTheme() {
  for (const palette of TERMINAL_PALETTES) {
    for (const slot of SLOTS) {
      for (const variant of VARIANTS[palette][slot]) {
        for (const mode of TERMINAL_MODES) yield { palette, slot, variant, mode };
      }
    }
  }
}

function themeTree(root: string, tree: Record<string, string>): void {
  for (const [path, text] of Object.entries(tree)) {
    mkdirSync(join(root, path, ".."), { recursive: true });
    writeFileSync(join(root, path), text);
  }
}

describe("terminal theme names", () => {
  test("palettes, slots and modes are recognised only by their exact names", () => {
    assert.equal(terminalPalette("ayu"), "ayu");
    assert.equal(terminalPalette("Ayu"), undefined);
    assert.equal(terminalSlot("light"), "light");
    assert.equal(terminalSlot("dim"), undefined);
    assert.equal(terminalMode("gutter"), "gutter");
    assert.equal(terminalMode("cards"), undefined);
    assert.deepEqual(slotVariants("github", "dark"), ["dark", "dimmed"]);
    assert.deepEqual(slotVariants("github", "dusk"), []);
    assert.deepEqual(slotVariants("solarized", "dark"), []);
  });

  test("every declared variant has palette data", () => {
    for (const { palette, variant } of everyTheme()) assert.ok(paletteVariant(palette, variant), `${palette}/${variant}`);
    assert.equal(paletteVariant("catppuccin", "frappe"), undefined);
  });
});

describe("slot resolution", () => {
  test("auto follows the selected Orca app theme, keeping its dark variant", () => {
    const slots = resolveSlots("auto", {
      pluginAppThemeDark: "plugin:stablyai.orca-ayu-theme/ayu-mirage",
      pluginAppThemeLight: "plugin:stablyai.orca-github-vscode-theme/github-light-default",
    });
    assert.deepEqual(slots, {
      dark: { palette: "ayu", variant: "mirage" },
      light: { palette: "github", variant: "light" },
    });
  });

  test("auto without settings or with an unknown app theme falls back to Catppuccin", () => {
    const fallback = { dark: { palette: "catppuccin", variant: "mocha" }, light: { palette: "catppuccin", variant: "latte" } };
    assert.deepEqual(resolveSlots("auto", null), fallback);
    assert.deepEqual(resolveSlots("auto", { pluginAppThemeDark: "builtin:dark", pluginAppThemeLight: "plugin:unknown/x" }), fallback);
  });

  test("a named palette ignores a dark variant chosen in another family", () => {
    const slots = resolveSlots("github", { pluginAppThemeDark: "plugin:stablyai.orca-ayu-theme/ayu-mirage" });
    assert.deepEqual(slots.dark, { palette: "github", variant: "dark" });
    assert.deepEqual(resolveSlots("github", { pluginAppThemeDark: "plugin:stablyai.orca-github-vscode-theme/github-dark-dimmed" }).dark, {
      palette: "github",
      variant: "dimmed",
    });
  });

  test("an unknown palette is an error", () => {
    assert.throws(() => resolveSlots("solarized", null), /Unknown terminal palette: solarized/);
  });
});

describe("generated themes", () => {
  test("every omp theme keeps its inks readable on the background", () => {
    for (const choice of everyTheme()) {
      const theme = buildOmpTheme(choice);
      const background = theme.vars.base;
      for (const [token, value] of Object.entries(theme.colors)) {
        if (!value || token.endsWith("Bg") || DECORATIVE.has(token) || token.startsWith("statusLine")) continue;
        const color = value === "text" ? theme.vars.text : value;
        assert.ok(contrast(color, background) >= 4.49, `${choice.palette}/${choice.variant}/${choice.mode}/${choice.slot} ${token}`);
      }
      assert.ok(contrast(theme.vars.text, theme.colors["userMessageBg"] ?? "") >= 4.49);
      assert.equal(theme.name, `orca-sections-${choice.slot}`);
    }
  });

  test("mode symbols open the frame for gutter and editorial only", () => {
    assert.equal(modeSymbols("document").overrides["boxRound.topLeft"], "╭");
    assert.equal(modeSymbols("gutter").overrides["boxRound.topLeft"], " ");
    assert.equal(modeSymbols("editorial").overrides["md.quoteBorder"], "▏");
    assert.equal(modeSymbols("split-cards").overrides["md.bullet"], "•");
  });

  test("modes paint their own surfaces", () => {
    const roles = {
      bg: "#000001", mantle: "#000002", successBg: "#000003", errorBg: "#000004", customBg: "#000005",
      userMessage: "#000006", surface1: "#000007", surface2: "#000008", code: "#000009", accent: "#00000a",
      decision: "#00000b", quietSubtle: "#00000c", quietThinking: "#00000d",
    };
    const painted = (mode: "document" | "split-cards" | "gutter" | "editorial") => {
      const colors = new Map<string, string>();
      applyMode(colors, roles, mode);
      return Object.fromEntries(colors);
    };
    assert.equal(painted("document")["userMessageBg"], roles.mantle);
    assert.equal(painted("split-cards")["toolSuccessBg"], roles.successBg);
    assert.equal(painted("gutter")["toolSuccessBg"], roles.bg);
    assert.equal(painted("editorial")["thinkingText"], roles.quietThinking);
  });

  test("the Orca terminal theme carries the palette's roles and ANSI colours", () => {
    const theme = buildOrcaTerminalTheme({ palette: "catppuccin", variant: "mocha", slot: "dark" });
    assert.equal(theme.mode, "dark");
    assert.equal(theme.terminal.background, "#1e1e2e");
    assert.equal(theme.terminal.red, "#f38ba8");
    assert.throws(() => buildOrcaTerminalTheme({ palette: "catppuccin", variant: "mocha", slot: "light" }), /Invalid terminal theme/);
    assert.throws(() => buildOmpTheme({ palette: "catppuccin", variant: "mocha", slot: "dark", mode: "cards" }), /Unknown terminal mode/);
  });

  test("the app theme of a variant is a copy", () => {
    const first = appThemeFor("ayu", "mirage");
    first.file = "changed.json";
    assert.notEqual(appThemeFor("ayu", "mirage").file, "changed.json");
    assert.throws(() => appThemeFor("ayu", "night"), /Unknown terminal palette variant/);
  });

  test("syncing writes both slots once and leaves unchanged files alone", () => {
    const root = mkdtempSync(join(tmpdir(), "orca-terminal-themes-"));
    try {
      const options = { mode: "gutter", palette: "catppuccin", settings: null, ompThemesDir: join(root, "omp"), pluginDir: join(root, "plugin") };
      const first = syncTerminalThemes(options);
      assert.equal(first.changed.omp.length, 2);
      assert.equal(first.changed.orca.length, 2);
      const dark = JSON.parse(readFileSync(join(root, "plugin", "terminal", "dark.json"), "utf8"));
      assert.equal(dark.terminal.background, "#1e1e2e");
      const again = syncTerminalThemes(options);
      assert.deepEqual(again.changed, { omp: [], orca: [] });
      assert.equal(syncTerminalThemes({ ...options, mode: "editorial" }).changed.omp.length, 2);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});

describe("installed theme discovery", () => {
  test("themes come from local dirs and every installed version of a known plugin", () => {
    const root = mkdtempSync(join(tmpdir(), "orca-installed-themes-"));
    themeTree(root, {
      "/local/mocha.json": JSON.stringify({ tokens: { "--background": "#1e1e2e" } }),
      "/local/readme.md": "not a theme",
      "/plugins/stablyai.orca-ayu-theme/1.0.0/themes/ayu-dark.json": JSON.stringify({ tokens: { "--background": "#10141c" } }),
      "/plugins/stablyai.orca-ayu-theme/1.0.0/themes/broken.json": "{",
      "/plugins/someone.else/1.0.0/themes/other.json": JSON.stringify({ tokens: {} }),
    });
    const themes = readThemes(fs, join(root, "plugins"), { "local.catppuccin-sections": join(root, "local") }, {
      "local.catppuccin-sections": "catppuccin",
      "stablyai.orca-ayu-theme": "ayu",
    });
    assert.deepEqual(themes, [
      { palette: "catppuccin", tokens: { "--background": "#1e1e2e" }, source: "local.catppuccin-sections/mocha.json" },
      { palette: "ayu", tokens: { "--background": "#10141c" }, source: "stablyai.orca-ayu-theme/ayu-dark.json" },
    ]);
    assert.deepEqual(readThemes(fs, join(root, "missing"), {}, {}), []);
    rmSync(root, { recursive: true, force: true });
    assert.equal(paletteForPlugin("stablyai.orca-github-vscode-theme"), "github");
    assert.equal(paletteForPlugin("someone.else"), undefined);
  });
});
