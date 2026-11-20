from __future__ import annotations

import re

STRING = r"""(?:"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`[^`\n]*`)"""
DOTTED = r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+"
ENVIRONMENT = r"process\.env|Bun\.env|import\.meta\.env"
BINDINGS = {
    "python": re.compile(
        rf"^[ \t]*([A-Za-z_]\w*)[ \t]*(?::[^=\n]+)?=[ \t]*({STRING}|{DOTTED})[ \t]*$",
        re.MULTILINE,
    ),
    "typescript": re.compile(
        rf"\b(?:const|let|var)[ \t]+([A-Za-z_$][\w$]*)[ \t]*(?::[^=\n]+)?=[ \t]*"
        rf"({STRING}|{DOTTED})(?=[ \t]*(?:;|$))",
        re.MULTILINE,
    ),
    "go": re.compile(
        rf"^[ \t]*(?:const[ \t]+|var[ \t]+)?([A-Za-z_]\w*)[ \t]*(?:[A-Za-z_][\w.]*[ \t]*)?"
        rf"(?::=|=)[ \t]*({STRING})[ \t]*$",
        re.MULTILINE,
    ),
}
DESTRUCTURED = re.compile(
    rf"\b(?:const|let|var)[ \t]*\{{([^}}]*)\}}[ \t]*=[ \t]*({ENVIRONMENT})\b"
)
PROPERTY = re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*(?::\s*([A-Za-z_$][\w$]*))?\s*$")
WRITE = re.compile(r"[ \t]*(?:[-+*/%|&^]?=(?!=)|:(?!:))")


def destructured(source: str) -> tuple[dict[str, str], list[tuple[int, int]]]:
    bound: dict[str, str] = {}
    spans = []
    for match in DESTRUCTURED.finditer(source):
        spans.append(match.span())
        for item in match[1].split(","):
            named = PROPERTY.match(item)
            if named:
                bound[named[2] or named[1]] = f"{match[2]}.{named[1]}"
    return bound, spans


def bindings(
    source: str, language: str
) -> tuple[dict[str, str], list[tuple[int, int]]]:
    bound, spans = destructured(source) if language == "typescript" else ({}, [])
    for match in BINDINGS[language].finditer(source):
        bound[match[1]] = match[2]
        spans.append(match.span(1))
    return bound, spans


def inline_bindings(source: str, language: str) -> str:
    bound, spans = bindings(source, language)
    if not bound:
        return source
    names = "|".join(sorted(map(re.escape, bound), key=len, reverse=True))
    pattern = re.compile(rf"(?<![\w$.])({names})(?![\w$])")

    def substitute(match: re.Match[str]) -> str:
        start = match.start()
        defining = any(low <= start < high for low, high in spans)
        if defining or WRITE.match(source, match.end()):
            return match[0]
        return bound[match[1]]

    return pattern.sub(substitute, source)
