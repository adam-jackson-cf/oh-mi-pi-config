from __future__ import annotations

import os
import socket
import sys
import threading
from dataclasses import dataclass, field
from typing import Any

from guardrails.commands.adapters.contract import FAST_TIER, GUARD_RULES, USER_DATA

PROCESS_EVENTS = frozenset({
    "subprocess.Popen",
    "os.system",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.fork",
    "os.forkpty",
})
NETWORK_EVENTS = frozenset({"socket.connect", "socket.sendto", "socket.sendmsg"})
RESOLVER_EVENTS = frozenset({
    "socket.getaddrinfo",
    "socket.gethostbyname",
    "socket.gethostbyaddr",
    "socket.getnameinfo",
})
IP_FAMILIES = frozenset({socket.AF_INET, socket.AF_INET6})
WRITE_EVENTS = {
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.mkdir": (0,),
    "os.rename": (0, 1),
    "os.link": (0, 1),
    "os.symlink": (1,),
    "os.truncate": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.chflags": (0,),
    "os.utime": (0,),
    "os.setxattr": (0,),
    "os.removexattr": (0,),
    "shutil.rmtree": (0,),
    "shutil.copyfile": (1,),
    "shutil.copymode": (1,),
    "shutil.copystat": (1,),
    "shutil.move": (1,),
}
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


class GuardViolationError(RuntimeError):
    pass


@dataclass
class GuardState:
    armed: frozenset[str] = frozenset()
    protected_root: str | None = None
    allowed_roots: tuple[str, ...] = ()
    violations: list[str] = field(default_factory=list)


STATE = GuardState()
LOCAL = threading.local()
HOOK_LOCK = threading.Lock()
HOOK_INSTALLED: list[bool] = []


def install() -> None:
    with HOOK_LOCK:
        if not HOOK_INSTALLED:
            sys.addaudithook(audit)
            HOOK_INSTALLED.append(True)


def configure(protected_root: str | None, allowed_roots: list[str]) -> None:
    STATE.protected_root = canonical(protected_root) if protected_root else None
    STATE.allowed_roots = tuple(canonical(root) for root in allowed_roots if root)


def arm(guards: frozenset[str]) -> None:
    STATE.violations = []
    STATE.armed = guards


def disarm() -> list[str]:
    STATE.armed = frozenset()
    violations, STATE.violations = STATE.violations, []
    return violations


def canonical(path: str) -> str:
    return os.path.realpath(os.path.abspath(path))


def audit(event: str, args: tuple[Any, ...]) -> None:
    if not STATE.armed or getattr(LOCAL, "busy", False):
        return
    LOCAL.busy = True
    try:
        reason = violation(event, args)
    finally:
        LOCAL.busy = False
    if reason:
        STATE.violations.append(reason)
        raise GuardViolationError(reason)


def violation(event: str, args: tuple[Any, ...]) -> str | None:
    if FAST_TIER in STATE.armed:
        reason = fast_tier_violation(event, args)
        if reason:
            return reason
    if USER_DATA in STATE.armed:
        return user_data_violation(event, args)
    return None


def fast_tier_violation(event: str, args: tuple[Any, ...]) -> str | None:
    rule = GUARD_RULES[FAST_TIER]
    if event in PROCESS_EVENTS:
        return (
            f"{rule}: the fast tier may not start a process ({event} {describe(args)})"
        )
    if (
        event in NETWORK_EVENTS
        and args
        and getattr(args[0], "family", None) in IP_FAMILIES
    ):
        address = args[1] if len(args) > 1 else ""
        return f"{rule}: the fast tier may not use the network ({event} {address!r})"
    if event in RESOLVER_EVENTS:
        name = args[0] if args else ""
        return f"{rule}: the fast tier may not resolve host names ({event} {name!r})"
    return None


def describe(args: tuple[Any, ...]) -> str:
    for item in args:
        if isinstance(item, (str, bytes, os.PathLike)):
            return repr(os.fsdecode(item))
        if isinstance(item, (list, tuple)) and item:
            return repr([
                os.fsdecode(part) for part in item if isinstance(part, (str, bytes))
            ])
    return ""


def user_data_violation(event: str, args: tuple[Any, ...]) -> str | None:
    for path in written_paths(event, args):
        if is_protected(path):
            return (
                f"{GUARD_RULES[USER_DATA]}: a test may not change user data under the real home "
                f"directory ({event} {path!r}); write under the test's temporary directory"
            )
    return None


def written_paths(event: str, args: tuple[Any, ...]) -> list[str]:
    if event == "open":
        return [os.fsdecode(args[0])] if opens_for_write(args) else []
    positions = WRITE_EVENTS.get(event, ())
    return [
        os.fsdecode(args[index])
        for index in positions
        if index < len(args) and isinstance(args[index], (str, bytes, os.PathLike))
    ]


def opens_for_write(args: tuple[Any, ...]) -> bool:
    path, mode, flags = (list(args) + [None, None, None])[:3]
    if not isinstance(path, (str, bytes, os.PathLike)):
        return False
    if isinstance(mode, str):
        return any(letter in mode for letter in "wax+")
    return isinstance(flags, int) and bool(flags & WRITE_FLAGS)


def is_protected(path: str) -> bool:
    root = STATE.protected_root
    if root is None:
        return False
    target = canonical(path)
    if not within(target, root):
        return False
    return not any(within(target, allowed) for allowed in STATE.allowed_roots)


def within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)
