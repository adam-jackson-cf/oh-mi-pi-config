/**
 * Operating-system and protected folders for the `system-only` bash scope of the Jev guard: places where
 * deleting, overwriting, moving or re-permissioning something damages the machine or the user's account
 * rather than a project. macOS and Windows only; other platforms have no list (the guard stays strict).
 *
 * Paths are compared case-insensitively on both platforms (APFS and NTFS are case-insensitive by default).
 * macOS callers should pass canonical paths (symlinked ancestors resolved), so `/etc` arrives as `/private/etc`;
 * both spellings are listed anyway. Windows paths may arrive as `C:\x`, `C:/x`, `/c/x` or `/cygdrive/c/x`.
 */
import { posix, win32 } from "node:path";

export type SystemPlatform = "darwin" | "win32";

/**
 * `subtree`: the folder and everything under it, except the `except` subtrees. `self`: only the path itself
 * (deleting a home folder or drive root), not what is inside. `children`: the path and its direct children (mount points).
 */
export type ProtectedRoot = { path: string; reach: "subtree" | "self" | "children"; except: string[] };

type Env = Readonly<Record<string, string | undefined>>;

export function isSystemPlatform(platform: string): platform is SystemPlatform {
  return platform === "darwin" || platform === "win32";
}

const subtree = (path: string, except: string[] = []): ProtectedRoot => ({ path, reach: "subtree", except });
const itself = (path: string): ProtectedRoot => ({ path, reach: "self", except: [] });

/** The protected folders of `platform` for a user whose home folder is `home`. */
export function protectedRoots(platform: SystemPlatform, home: string, env: Env = process.env): ProtectedRoot[] {
  if (platform === "darwin") {
    return [
      itself("/"), itself("/private"), itself("/Users"),
      subtree("/System"), subtree("/bin"), subtree("/sbin"), subtree("/Library"), subtree("/Applications"), subtree("/cores"),
      subtree("/usr", ["/usr/local"]),
      subtree("/etc"), subtree("/private/etc"),
      subtree("/var", ["/var/folders", "/var/tmp"]), subtree("/private/var", ["/private/var/folders", "/private/var/tmp"]),
      { path: "/Volumes", reach: "children", except: [] },
      itself(home),
      subtree(posix.join(home, "Library")), subtree(posix.join(home, ".ssh")), subtree(posix.join(home, ".gnupg")),
    ];
  }
  const drive = (env.SystemDrive ?? "C:").replace(/[\\/]+$/, "");
  const profile = env.USERPROFILE ?? home;
  const programFiles = [env.ProgramFiles ?? `${drive}\\Program Files`, env["ProgramFiles(x86)"] ?? `${drive}\\Program Files (x86)`,
    env.ProgramW6432 ?? `${drive}\\Program Files`];
  return [
    itself(`${drive}\\`),
    subtree(env.SystemRoot ?? env.windir ?? `${drive}\\Windows`),
    ...[...new Set(programFiles)].map((path) => subtree(path)),
    subtree(env.ProgramData ?? `${drive}\\ProgramData`),
    ...["System Volume Information", "$Recycle.Bin", "Recovery", "Boot", "bootmgr", "pagefile.sys", "hiberfil.sys", "swapfile.sys"]
      .map((name) => subtree(`${drive}\\${name}`)),
    itself(`${drive}\\Users`),
    itself(profile),
    subtree(win32.join(profile, "AppData")), subtree(win32.join(profile, ".ssh")), subtree(win32.join(profile, "NTUSER.DAT")),
    ...[env.APPDATA, env.LOCALAPPDATA].filter((path): path is string => path !== undefined).map((path) => subtree(path)),
  ];
}

/** Lower-cased comparable form of a path, without a trailing separator (roots keep theirs). */
export function comparablePath(platform: SystemPlatform, path: string): string {
  if (platform === "darwin") {
    const normal = posix.normalize(path);
    return (normal.length > 1 ? normal.replace(/\/+$/, "") : normal).toLowerCase();
  }
  const drive = /^\/(?:cygdrive\/)?([a-zA-Z])(?=\/|$)/.exec(path);
  const native = drive ? `${drive[1]}:${path.slice(drive[0].length) || "\\"}` : path;
  const normal = win32.normalize(native.replace(/\//g, "\\"));
  return (/^[a-zA-Z]:\\$/.test(normal) ? normal : normal.replace(/\\+$/, "")).toLowerCase();
}

function contains(platform: SystemPlatform, root: string, path: string): boolean {
  const sep = platform === "darwin" ? "/" : "\\";
  return path === root || path.startsWith(root.endsWith(sep) ? root : `${root}${sep}`);
}

/** The protected root `path` falls under, or undefined. `path` must be absolute (and canonical on macOS). */
export function protectedRootOf(platform: SystemPlatform, roots: readonly ProtectedRoot[], path: string): ProtectedRoot | undefined {
  const target = comparablePath(platform, path);
  const sep = platform === "darwin" ? "/" : "\\";
  return roots.find((root) => {
    const base = comparablePath(platform, root.path);
    if (root.reach === "self") return target === base;
    if (!contains(platform, base, target)) return false;
    if (root.reach === "children") return target === base || !target.slice(base.length + 1).includes(sep);
    return !root.except.some((except) => contains(platform, comparablePath(platform, except), target));
  });
}

const DARWIN_PATH = /(?:^|(?<=[\s"'=(:,]))((?:~|\$\{?HOME\}?)?\/[^\s"'`;&|()<>]*)/g;
const WIN_PATH = /(?:^|(?<=[\s"'=(:,]))((?:%\w+(?:\(x86\))?%|\$\{?env:\w+(?:\(x86\))?\}?|[a-zA-Z]:(?=[\\/])|\/cygdrive\/[a-zA-Z](?=\/|$)|\/[a-zA-Z](?=\/))[^\s"'`;&|<>]*)/g;

/** Value of a Windows environment variable for path expansion, with the stock defaults when it is unset. */
function windowsVariable(name: string, home: string, env: Env): string | undefined {
  const drive = (env.SystemDrive ?? "C:").replace(/[\\/]+$/, "");
  const defaults = new Map([
    ["systemdrive", drive], ["systemroot", `${drive}\\Windows`], ["windir", `${drive}\\Windows`], ["programfiles", `${drive}\\Program Files`],
    ["programfiles(x86)", `${drive}\\Program Files (x86)`], ["programw6432", `${drive}\\Program Files`], ["programdata", `${drive}\\ProgramData`],
    ["userprofile", home], ["appdata", win32.join(home, "AppData", "Roaming")], ["localappdata", win32.join(home, "AppData", "Local")],
  ]);
  const key = Object.keys(env).find((candidate) => candidate.toLowerCase() === name.toLowerCase());
  return (key === undefined ? undefined : env[key]) ?? defaults.get(name.toLowerCase());
}

/**
 * Absolute paths written literally in command text, expanded from the spellings a shell command uses: `/…`, `~/…` and
 * `$HOME/…` on macOS; `C:\…`, `C:/…`, `/c/…`, `/cygdrive/c/…`, `%VAR%…` and `$env:VAR…` on Windows. Used where
 * the code cannot tell which arguments a command writes (unknown programs), so the caller checks every one.
 */
export function literalPaths(platform: SystemPlatform, text: string, home: string, env: Env = process.env): string[] {
  const found: string[] = [];
  if (platform === "darwin") {
    for (const match of text.matchAll(DARWIN_PATH)) {
      const token = match[1] ?? "";
      found.push(token.replace(/^(?:~|\$\{?HOME\}?)(?=\/)/, home));
    }
    return found;
  }
  for (const match of text.matchAll(WIN_PATH)) {
    const token = match[1] ?? "";
    const variable = /^(?:%(\w+(?:\(x86\))?)%|\$\{?env:(\w+(?:\(x86\))?)\}?)/i.exec(token);
    if (!variable) {
      found.push(token);
      continue;
    }
    const value = windowsVariable(variable[1] ?? variable[2] ?? "", home, env);
    if (value !== undefined) found.push(`${value}${token.slice(variable[0].length)}`);
  }
  return found;
}
