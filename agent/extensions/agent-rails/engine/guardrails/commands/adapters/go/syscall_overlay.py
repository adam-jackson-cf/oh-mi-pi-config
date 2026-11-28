from __future__ import annotations

import json
import re
from pathlib import Path

from guardrails.foundation.source import go

GUARDS_VARIABLE = "GUARDRAILS_GO_GUARDS"
PROTECTED_VARIABLE = "GUARDRAILS_GO_PROTECTED"
ALLOWED_VARIABLE = "GUARDRAILS_GO_ALLOWED"
JOURNAL_VARIABLE = "GUARDRAILS_GO_JOURNAL"
PATH_SEPARATOR = "\n"
GUARD_FILE = "zz_guardrails.go"
SIGNATURE = re.compile(r"^func (?P<name>\w+)\((?P<params>[^()]*)\) [^\n]*\{$", re.M)
HOOKS = {
    "forkExec": ("guardrailsGuardProcess({0})",),
    "Exec": ("guardrailsGuardProcess({0})",),
    "Socket": ("guardrailsGuardSocket({0})",),
    "Open": ("guardrailsGuardOpen({0}, {1})",),
    "Mkdir": ("guardrailsGuardWrite({0})",),
    "Rename": ("guardrailsGuardWrite({0})", "guardrailsGuardWrite({1})"),
    "Unlink": ("guardrailsGuardWrite({0})",),
    "Rmdir": ("guardrailsGuardWrite({0})",),
    "Symlink": ("guardrailsGuardWrite({1})",),
    "Link": ("guardrailsGuardWrite({1})",),
    "Truncate": ("guardrailsGuardWrite({0})",),
    "Chmod": ("guardrailsGuardWrite({0})",),
}
GUARD_SOURCE = """package syscall

var (
	guardrailsGuards, _    = Getenv("${GUARDS}")
	guardrailsProtected, _ = Getenv("${PROTECTED}")
	guardrailsAllowed, _   = Getenv("${ALLOWED}")
	guardrailsJournal, _   = Getenv("${JOURNAL}")
)

func guardrailsGuardListed(wanted string) bool {
	for start := 0; start <= len(guardrailsGuards); {
		end := start
		for end < len(guardrailsGuards) && guardrailsGuards[end] != ',' {
			end++
		}
		if guardrailsGuards[start:end] == wanted {
			return true
		}
		start = end + 1
	}
	return false
}

func guardrailsGuardBlock(message string) {
	if guardrailsJournal != "" {
		if fd, err := Open(guardrailsJournal, O_WRONLY|O_CREAT|O_APPEND, 0o600); err == nil {
			Write(fd, []byte(message+"\\n"))
			Close(fd)
		}
	}
	panic(message)
}

func guardrailsGuardProcess(name string) {
	if guardrailsGuardListed("fast-tier") {
		guardrailsGuardBlock("TEST.fast-tier-isolation: blocked process start " + name)
	}
}

func guardrailsGuardSocket(domain int) {
	if (domain == AF_INET || domain == AF_INET6) && guardrailsGuardListed("fast-tier") {
		guardrailsGuardBlock("TEST.fast-tier-isolation: blocked network socket")
	}
}

func guardrailsGuardOpen(path string, mode int) {
	if mode&(O_WRONLY|O_RDWR|O_CREAT|O_TRUNC|O_APPEND) != 0 {
		guardrailsGuardWrite(path)
	}
}

func guardrailsGuardWrite(path string) {
	if !guardrailsGuardListed("user-data") {
		return
	}
	target := guardrailsGuardClean(guardrailsGuardAbsolute(path))
	if target == guardrailsJournal {
		return
	}
	if guardrailsGuardUnder(target, guardrailsProtected) && !guardrailsGuardUnder(target, guardrailsAllowed) {
		guardrailsGuardBlock("TEST.user-data-guard: blocked write under the real home directory " + target)
	}
}

func guardrailsGuardAbsolute(path string) string {
	if len(path) > 0 && path[0] == '/' {
		return path
	}
	directory, err := Getwd()
	if err != nil {
		return path
	}
	return directory + "/" + path
}

func guardrailsGuardClean(path string) string {
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

func guardrailsGuardUnder(target string, roots string) bool {
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
    for path in sorted(source.iterdir()):
        if not go.built_path(path.name) or go.is_test_file(path.name):
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
