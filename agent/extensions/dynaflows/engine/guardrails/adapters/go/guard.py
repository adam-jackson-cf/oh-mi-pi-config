from __future__ import annotations

import json
import re
from pathlib import Path

GUARDS_VARIABLE = "OMP_GUARDRAILS_GO_GUARDS"
PROTECTED_VARIABLE = "OMP_GUARDRAILS_GO_PROTECTED"
ALLOWED_VARIABLE = "OMP_GUARDRAILS_GO_ALLOWED"
JOURNAL_VARIABLE = "OMP_GUARDRAILS_GO_JOURNAL"
PATH_SEPARATOR = "\n"
GUARD_FILE = "zz_omp_guardrails.go"
SIGNATURE = re.compile(r"^func (?P<name>\w+)\((?P<params>[^()]*)\) [^\n]*\{$", re.M)
HOOKS = {
    "forkExec": ("ompGuardProcess({0})",),
    "Exec": ("ompGuardProcess({0})",),
    "Socket": ("ompGuardSocket({0})",),
    "Open": ("ompGuardOpen({0}, {1})",),
    "Mkdir": ("ompGuardWrite({0})",),
    "Rename": ("ompGuardWrite({0})", "ompGuardWrite({1})"),
    "Unlink": ("ompGuardWrite({0})",),
    "Rmdir": ("ompGuardWrite({0})",),
    "Symlink": ("ompGuardWrite({1})",),
    "Link": ("ompGuardWrite({1})",),
    "Truncate": ("ompGuardWrite({0})",),
    "Chmod": ("ompGuardWrite({0})",),
}
GUARD_SOURCE = """package syscall

var (
	ompGuards, _    = Getenv("${GUARDS}")
	ompProtected, _ = Getenv("${PROTECTED}")
	ompAllowed, _   = Getenv("${ALLOWED}")
	ompJournal, _   = Getenv("${JOURNAL}")
)

func ompGuardListed(wanted string) bool {
	for start := 0; start <= len(ompGuards); {
		end := start
		for end < len(ompGuards) && ompGuards[end] != ',' {
			end++
		}
		if ompGuards[start:end] == wanted {
			return true
		}
		start = end + 1
	}
	return false
}

func ompGuardBlock(message string) {
	if ompJournal != "" {
		if fd, err := Open(ompJournal, O_WRONLY|O_CREAT|O_APPEND, 0o600); err == nil {
			Write(fd, []byte(message+"\\n"))
			Close(fd)
		}
	}
	panic(message)
}

func ompGuardProcess(name string) {
	if ompGuardListed("fast-tier") {
		ompGuardBlock("TEST.fast-tier-isolation: blocked process start " + name)
	}
}

func ompGuardSocket(domain int) {
	if (domain == AF_INET || domain == AF_INET6) && ompGuardListed("fast-tier") {
		ompGuardBlock("TEST.fast-tier-isolation: blocked network socket")
	}
}

func ompGuardOpen(path string, mode int) {
	if mode&(O_WRONLY|O_RDWR|O_CREAT|O_TRUNC|O_APPEND) != 0 {
		ompGuardWrite(path)
	}
}

func ompGuardWrite(path string) {
	if !ompGuardListed("user-data") {
		return
	}
	target := ompGuardClean(ompGuardAbsolute(path))
	if target == ompJournal {
		return
	}
	if ompGuardUnder(target, ompProtected) && !ompGuardUnder(target, ompAllowed) {
		ompGuardBlock("TEST.user-data-guard: blocked write under the real home directory " + target)
	}
}

func ompGuardAbsolute(path string) string {
	if len(path) > 0 && path[0] == '/' {
		return path
	}
	directory, err := Getwd()
	if err != nil {
		return path
	}
	return directory + "/" + path
}

func ompGuardClean(path string) string {
	parts := []string{}
	start := 0
	for index := 0; index <= len(path); index++ {
		if index < len(path) && path[index] != '/' {
			continue
		}
		part := path[start:index]
		start = index + 1
		if part == "" || part == "." {
			continue
		}
		if part == ".." {
			if len(parts) > 0 {
				parts = parts[:len(parts)-1]
			}
			continue
		}
		parts = append(parts, part)
	}
	cleaned := ""
	for _, part := range parts {
		cleaned += "/" + part
	}
	if cleaned == "" {
		return "/"
	}
	return cleaned
}

func ompGuardUnder(target string, roots string) bool {
	start := 0
	for index := 0; index <= len(roots); index++ {
		if index < len(roots) && roots[index] != '\\n' {
			continue
		}
		root := roots[start:index]
		start = index + 1
		if root == "" {
			continue
		}
		if target == root || (len(target) > len(root) && target[:len(root)] == root && (root == "/" || target[len(root)] == '/')) {
			return true
		}
	}
	return false
}
"""


class GuardError(Exception):
    pass


def parameter_names(params: str) -> list[str]:
    return [part.split()[0] for part in params.split(",") if part.strip()]


def hooked(text: str) -> tuple[str, set[str]]:
    patched: set[str] = set()

    def insert(match: re.Match[str]) -> str:
        name = match.group("name")
        calls = HOOKS.get(name)
        if calls is None:
            return match.group(0)
        names = parameter_names(match.group("params"))
        patched.add(name)
        body = "".join(f"\n\t{call.format(*names)}" for call in calls)
        return match.group(0) + body

    return SIGNATURE.sub(insert, text), patched


def guard_source() -> str:
    return (
        GUARD_SOURCE
        .replace("${GUARDS}", GUARDS_VARIABLE)
        .replace("${PROTECTED}", PROTECTED_VARIABLE)
        .replace("${ALLOWED}", ALLOWED_VARIABLE)
        .replace("${JOURNAL}", JOURNAL_VARIABLE)
    )


def write_overlay(goroot: Path, target: Path) -> Path:
    source = goroot / "src" / "syscall"
    if not source.is_dir():
        raise GuardError(f"{source} is not a Go standard library syscall package")
    replaced: dict[str, str] = {}
    covered: set[str] = set()
    folder = target / "syscall"
    folder.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.glob("*.go")):
        if path.name.endswith("_test.go"):
            continue
        text, patched = hooked(path.read_text(encoding="utf-8"))
        if not patched:
            continue
        covered.update(patched)
        copy = folder / path.name
        copy.write_text(text, encoding="utf-8")
        replaced[str(path)] = str(copy)
    missing = sorted(set(HOOKS) - covered)
    if missing:
        raise GuardError(
            f"the Go standard library at {goroot} defines no syscall {', '.join(missing)}"
        )
    guard = folder / GUARD_FILE
    guard.write_text(guard_source(), encoding="utf-8")
    replaced[str(source / GUARD_FILE)] = str(guard)
    overlay = target / "overlay.json"
    overlay.write_text(
        json.dumps({"Replace": replaced}, indent=2) + "\n", encoding="utf-8"
    )
    return overlay
