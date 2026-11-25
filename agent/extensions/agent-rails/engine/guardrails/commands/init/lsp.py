from __future__ import annotations

import json
import os
import queue
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Any

from guardrails.foundation.child_env import repo_environment
from guardrails.foundation.findings import Finding, config_label
from guardrails.host.process import start_piped

RULE = "CONFIG.complete"
SKIPPED_PARTS = frozenset({
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    "__pycache__",
})
DEFAULT_TIMEOUT_S = 60.0
PYTHON_CANARY = (
    ("agent_rails_lsp_canary.py", "def agent_rails_lsp_canary():\n    return 1\n"),
    (
        "agent_rails_lsp_caller.py",
        "from agent_rails_lsp_canary import agent_rails_lsp_canary\n\nagent_rails_lsp_canary()\n",
    ),
)
TYPESCRIPT_CANARY = (
    (
        "agent-rails-lsp-canary.ts",
        "export function agentRailsLspCanary(): number {\n  return 1;\n}\n",
    ),
    (
        "agent-rails-lsp-caller.ts",
        'import { agentRailsLspCanary } from "./agent-rails-lsp-canary";\n\n'
        "agentRailsLspCanary();\n",
    ),
)


@dataclass(frozen=True)
class Stack:
    name: str
    markers: tuple[str, ...]
    server: str
    install: str
    language_id: str
    extensions: tuple[str, ...]
    definition: str
    canary_files: tuple[tuple[str, str], ...]
    arguments: tuple[str, ...] = ("--stdio",)


STACKS = (
    Stack(
        "python",
        ("pyproject.toml",),
        "pyright-langserver",
        "install pyright (npm install -g pyright, or uv tool install pyright)",
        "python",
        (".py",),
        r"^(?:async\s+def|def|class)\s+(?P<name>[A-Za-z_]\w*)",
        PYTHON_CANARY,
    ),
    Stack(
        "typescript",
        ("package.json", "tsconfig.json"),
        "typescript-language-server",
        "npm install -g typescript typescript-language-server",
        "typescript",
        (".ts", ".tsx", ".mts", ".cts"),
        r"^export\s+(?:async\s+)?(?:function|class|const|let)\s+(?P<name>[A-Za-z_$][\w$]*)",
        TYPESCRIPT_CANARY,
    ),
)


@dataclass(frozen=True)
class Symbol:
    path: Path
    name: str
    line: int
    column: int


class CanaryError(Exception):
    pass


def detect(repo: Path) -> list[Stack]:
    stacks = [
        stack
        for stack in STACKS
        if any((repo / name).is_file() for name in stack.markers)
    ]
    for index, stack in enumerate(stacks):
        if stack.name != "typescript":
            continue
        package = repo / "node_modules/typescript/package.json"
        if not package.is_file():
            continue
        metadata = json.loads(package.read_text(encoding="utf-8"))
        if re.match(r"^(?:[7-9]|\d{2,})\.", str(metadata.get("version", ""))):
            stacks[index] = replace(
                stack,
                server=str((package.parent / "bin/tsc").resolve()),
                install="install the repository's pinned TypeScript with its package manager",
                arguments=("--lsp", "--stdio"),
            )
    return stacks


def notes(stacks: list[Stack]) -> list[dict[str, Any]]:
    return [
        {"stack": stack.name, "server": stack.server, "install": stack.install}
        for stack in stacks
    ]


def source_files(root: Path, stack: Stack) -> list[Path]:
    found = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).parts
        if path.suffix not in stack.extensions or SKIPPED_PARTS & set(relative):
            continue
        if any(part.startswith(".") for part in relative) or not path.is_file():
            continue
        found.append(path)
    return found


def find_symbol(root: Path, stack: Stack) -> Symbol | None:
    pattern = re.compile(stack.definition)
    for path in source_files(root, stack):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for number, text in enumerate(lines):
            match = pattern.match(text)
            if match:
                return Symbol(path, match.group("name"), number, match.start("name"))
    return None


def frame(message: dict[str, Any]) -> bytes:
    body = json.dumps(message).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def read_messages(stream: IO[bytes], inbox: queue.Queue[dict[str, Any] | None]) -> None:
    try:
        while True:
            length = None
            while True:
                line = stream.readline()
                if not line:
                    return
                text = line.decode("ascii", errors="replace").strip()
                if not text:
                    break
                name, _, value = text.partition(":")
                if name.lower() == "content-length":
                    length = int(value.strip())
            if length is None:
                return
            inbox.put(json.loads(stream.read(length)))
    except (OSError, ValueError):
        return
    finally:
        inbox.put(None)


class Session:
    def __init__(
        self, server: str, workspace: Path, timeout: float, arguments: tuple[str, ...]
    ) -> None:
        self.deadline = time.monotonic() + timeout
        self.next_id = 0
        try:
            self.process = start_piped(
                [server, *arguments], cwd=workspace, env=repo_environment()
            )
        except OSError as error:
            raise CanaryError(f"cannot start {server}: {error}") from error
        if self.process.stdin is None or self.process.stdout is None:
            raise CanaryError(f"{server} started without piped stdio")
        self.stdin: IO[bytes] = self.process.stdin
        self.inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        threading.Thread(
            target=read_messages, args=(self.process.stdout, self.inbox), daemon=True
        ).start()

    def send(self, message: dict[str, Any]) -> None:
        try:
            self.stdin.write(frame({"jsonrpc": "2.0", **message}))
            self.stdin.flush()
        except OSError as error:
            raise CanaryError(f"the server closed its input: {error}") from error

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.send({"method": method, "params": params})

    def request(self, method: str, params: dict[str, Any] | None) -> object:
        self.next_id += 1
        wanted = self.next_id
        self.send({"id": wanted, "method": method, "params": params})
        while True:
            message = self.receive(method)
            if "method" in message and "id" in message:
                self.answer(message)
            elif message.get("id") == wanted:
                if "error" in message:
                    raise CanaryError(f"{method} failed: {message['error']}")
                return message.get("result")

    def receive(self, method: str) -> dict[str, Any]:
        remaining = self.deadline - time.monotonic()
        try:
            message = self.inbox.get(timeout=max(remaining, 0))
        except queue.Empty as error:
            raise CanaryError(f"no answer to {method} before the time limit") from error
        if message is None:
            code = self.process.wait()
            raise CanaryError(
                f"the server exited (code {code}) before answering {method}"
            )
        return message

    def answer(self, message: dict[str, Any]) -> None:
        result = None
        if message["method"] == "workspace/configuration":
            items = (message.get("params") or {}).get("items") or []
            result = [None for _ in items]
        self.send({"id": message["id"], "result": result})

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                self.request("shutdown", None)
                self.notify("exit", {})
        except CanaryError:
            pass
        finally:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait()


def query(
    server: str, workspace: Path, symbol: Symbol, stack: Stack, timeout: float
) -> int:
    session = Session(server, workspace, timeout, stack.arguments)
    try:
        session.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": workspace.as_uri(),
                "workspaceFolders": [
                    {"uri": workspace.as_uri(), "name": workspace.name}
                ],
                "capabilities": {},
            },
        )
        session.notify("initialized", {})
        uri = symbol.path.as_uri()
        text = symbol.path.read_text(encoding="utf-8")
        session.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": stack.language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )
        result = session.request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": {"line": symbol.line, "character": symbol.column},
                "context": {"includeDeclaration": True},
            },
        )
    finally:
        session.close()
    if not isinstance(result, list) or not result:
        raise CanaryError(f"references for {symbol.name} returned no locations")
    return len(result)


def canary(repo: Path, stack: Stack, server: str, timeout: float) -> str:
    symbol = find_symbol(repo, stack)
    if symbol is not None:
        count = query(server, repo, symbol, stack, timeout)
        where = symbol.path.relative_to(repo).as_posix()
        return (
            f"references {where}:{symbol.line + 1} {symbol.name} -> {count} locations"
        )
    with tempfile.TemporaryDirectory(prefix="agent-rails-lsp-canary-") as scratch:
        workspace = Path(scratch).resolve()
        for marker in stack.markers:
            if (repo / marker).is_file():
                shutil.copyfile(repo / marker, workspace / marker)
        for name, text in stack.canary_files:
            (workspace / name).write_text(text, encoding="utf-8")
        symbol = find_symbol(workspace, stack)
        if symbol is None:
            raise CanaryError("the canary module defines no symbol")
        count = query(server, workspace, symbol, stack, timeout)
        return f"references in a canary module {symbol.name} -> {count} locations"


def readiness(
    repo: Path,
    stacks: list[Stack],
    *,
    allow_missing: bool,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    lines: list[str] = []
    for stack in stacks:
        found = shutil.which(stack.server)
        if not found:
            message = f"{stack.name} language server {stack.server} is not on PATH"
            if allow_missing:
                lines.append(
                    f"lsp: {message} (allowed by --allow-missing-lsp); {stack.install}"
                )
            else:
                findings.append(
                    Finding(
                        RULE,
                        config_label("tests"),
                        message,
                        f"{stack.install}, then run init again",
                    )
                )
            continue
        try:
            result = canary(repo, stack, found, timeout)
        except CanaryError as error:
            findings.append(
                Finding(
                    RULE,
                    config_label("tests"),
                    f"{stack.name} language server {found} failed the canary: {error}",
                    f"repair or reinstall it ({stack.install}), then run init again",
                )
            )
            continue
        lines.append(f"lsp: {stack.name} server {found} canary ok: {result}")
    if not stacks:
        lines.append(
            "lsp: no pyproject.toml, package.json or tsconfig.json in the repo root"
        )
    return findings, lines
