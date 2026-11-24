from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

SOURCE_SUFFIXES = (".py", ".go", ".rs", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".jsx")
EXCLUDED_FOLDER = re.compile(
    r"(^|/)(node_modules|vendor|third_party|__pycache__|dist|build|examples?|\.[^/]+)(/|$)"
)
TEST_PATH = re.compile(
    r"(^|/)(tests?|testing|__tests__|fixtures?|conftest\.py$)|(^|/)test_[^/]*\.py$"
    r"|_test\.(py|go)$|\.(test|spec)\.(ts|tsx|js|mjs)$"
)
SKIPPED_MODULES = frozenset({
    "__init__",
    "__main__",
    "conftest",
    "index",
    "lib",
    "main",
    "mod",
    "setup",
})
UPPER = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
CAPITALISED = re.compile(r"^_?[A-Z][a-z0-9]+[A-Za-z0-9]*$")
IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*$")
TOKEN = re.compile(r"[A-Za-z_$][\w$]*|\S")
WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")
CODE_LINES = 30
STRING_LITERAL = re.compile(r"^\s*([\"'`])(.*)\1\s*;?\s*(//.*)?$")

Gram = tuple[str, str, str]


def stem(word: str) -> str:
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words(name: str, keep_dots: bool = False) -> list[str]:
    if not keep_dots and "." in name and not name.startswith('"'):
        name = name.split(".")[-1]
    found = []
    for part in re.split(r"[_\-\s./]+", name):
        found.extend(WORD.findall(part))
    return [stem(word.lower()) for word in found if word]


def flat(name: str) -> str:
    return name.split(".")[-1].lstrip("_").lower().replace("_", "")


def without_comments(source: str, language: str) -> str:
    if language == "py":
        source = re.sub(r"(?s)(\"\"\"|''').*?\1", "", source)
        return re.sub(r"#[^\n]*", "", source)
    source = re.sub(r"(?s)/\*.*?\*/", "", source)
    return re.sub(r"//[^\n]*", "", source)


def grams(code: str, language: str) -> frozenset[Gram]:
    tokens = TOKEN.findall(without_comments(code, language))
    return frozenset(zip(tokens, tokens[1:], tokens[2:], strict=False))


def language_of(path: str) -> str:
    return {"py": "py", "go": "go", "rs": "rs"}.get(path.rsplit(".", 1)[-1], "ts")


def first_lines(lines: list[str], start: int, end: int) -> str:
    text = "\n".join(lines[start - 1 : min(end, start + CODE_LINES - 1)])
    return text + "\n# ... (truncated)" if end > start + CODE_LINES - 1 else text


@dataclass(frozen=True)
class Place:
    path: str
    language: str
    lines: list[str]


@dataclass(frozen=True)
class Detail:
    signature: str = ""
    doc: str = ""
    owner: str | None = None
    value: str | None = None
    top: bool = True
    bases: tuple[str, ...] = ()


@dataclass
class Found:
    definitions: list[dict[str, Any]] = field(default_factory=list)
    aliases: list[dict[str, Any]] = field(default_factory=list)


def definition(
    place: Place, name: str, kind: str, span: tuple[int, int], detail: Detail
) -> dict[str, Any]:
    line, end = span
    return {
        "name": name,
        "base": name.split(".")[-1],
        "kind": kind,
        "lang": place.language,
        "path": place.path,
        "line": line,
        "end": end,
        "sig": detail.signature.strip()[:300],
        "doc": (detail.doc or "").strip()[:600],
        "owner": detail.owner,
        "value": detail.value,
        "top": detail.top,
        "bases": list(detail.bases),
        "code": first_lines(place.lines, line, end),
        "nlines": end - line + 1,
        "grams": grams("\n".join(place.lines[line - 1 : end]), place.language),
    }


def module_definition(place: Place, doc: str) -> list[dict[str, Any]]:
    name = PurePosixPath(place.path).name.rsplit(".", 1)[0]
    if place.language != "py":
        name = re.sub(r"\.(test|spec)$", "", name)
    if name in SKIPPED_MODULES:
        return []
    found = definition(
        place,
        name,
        "module",
        (1, len(place.lines)),
        Detail(signature="module " + place.path, doc=doc),
    )
    found["grams"] = frozenset()
    return [found]


def alias(
    place: Place, names: tuple[str, str], line: int, text: str, how: str
) -> dict[str, Any]:
    bound, target = names
    return {
        "alias": bound,
        "alias_base": bound.split(".")[-1],
        "target": target,
        "path": place.path,
        "line": line,
        "text": text,
        "how": how,
        "internal": None,
    }


def module_roots(paths: list[str]) -> set[str]:
    roots: set[str] = set()
    for path in paths:
        if not path.endswith(".py"):
            continue
        parts = path.split("/")
        roots.add(parts[0].rsplit(".", 1)[0] if len(parts) == 1 else parts[0])
        if parts[0] in {"src", "lib", "packages"} and len(parts) > 1:
            roots.add(parts[1].rsplit(".", 1)[0])
        roots.update(parts[:-1])
    return roots


class PythonSource:
    def __init__(self, place: Place, roots: set[str]) -> None:
        self.place, self.roots, self.found = place, roots, Found()

    def segment(self, first: int, last: int) -> str:
        return "\n".join(self.place.lines[first - 1 : last])

    def signature(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> tuple[int, str]:
        start = min([item.lineno for item in node.decorator_list] + [node.lineno])
        body = node.body[0].lineno if node.body else node.lineno
        if body > node.lineno:
            return start, self.segment(node.lineno, body - 1)
        return start, self.place.lines[node.lineno - 1]

    def add(
        self,
        name: str,
        kind: str,
        node: ast.stmt,
        detail: Detail,
        start: int | None = None,
    ) -> None:
        span = (start or node.lineno, node.end_lineno or node.lineno)
        self.found.definitions.append(definition(self.place, name, kind, span, detail))

    def function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        start, signature = self.signature(node)
        doc = ast.get_docstring(node) or ""
        self.add(node.name, "function", node, Detail(signature, doc), start)

    def klass(self, node: ast.ClassDef) -> None:
        start, signature = self.signature(node)
        bases = tuple(
            ast.unparse(base).split(".")[-1].split("[")[0] for base in node.bases
        )
        doc = ast.get_docstring(node) or ""
        self.add(node.name, "class", node, Detail(signature, doc, bases=bases), start)
        for member in node.body:
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if member.name.startswith("__") and member.name.endswith("__"):
                    continue
                begin, text = self.signature(member)
                detail = Detail(
                    text, ast.get_docstring(member) or "", owner=node.name, top=False
                )
                self.add(f"{node.name}.{member.name}", "method", member, detail, begin)
            elif isinstance(member, ast.Assign):
                self.class_alias(node.name, member)

    def class_alias(self, owner: str, node: ast.Assign) -> None:
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return
        if not isinstance(node.value, (ast.Name, ast.Attribute)):
            return
        bound, target = node.targets[0].id, ast.unparse(node.value)
        if bound.startswith("__") or bound == target.split(".")[-1]:
            return
        text = self.place.lines[node.lineno - 1].strip()
        found = alias(
            self.place, (f"{owner}.{bound}", target), node.lineno, text, "class_assign"
        )
        found.update(alias_base=bound, internal=True, owner=owner)
        self.found.aliases.append(found)

    def assignment(self, node: ast.Assign | ast.AnnAssign) -> None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if (
            len(targets) != 1
            or not isinstance(targets[0], ast.Name)
            or node.value is None
        ):
            return
        bound, value = targets[0].id, node.value
        if bound.startswith("__"):
            return
        if isinstance(value, (ast.Name, ast.Attribute)):
            target = ast.unparse(value)
            if bound != target.split(".")[-1]:
                text = self.segment(node.lineno, node.end_lineno or node.lineno)[:300]
                self.found.aliases.append(
                    alias(self.place, (bound, target), node.lineno, text, "assign")
                )
            return
        signature = self.place.lines[node.lineno - 1]
        if UPPER.match(bound):
            held = (
                value.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
                else None
            )
            self.add(bound, "constant", node, Detail(signature, value=held))
        elif CAPITALISED.match(bound) and isinstance(value, (ast.Subscript, ast.BinOp)):
            self.add(bound, "type", node, Detail(signature))

    def internal(self, module: str | None, level: int) -> bool:
        return level > 0 or (
            bool(module) and (module or "").split(".")[0] in self.roots
        )

    def import_from(self, node: ast.ImportFrom) -> None:
        if not self.internal(node.module, node.level or 0):
            return
        text = self.place.lines[node.lineno - 1].strip()[:300]
        for name in node.names:
            if name.asname and name.asname != name.name and name.name != "*":
                found = alias(
                    self.place, (name.asname, name.name), node.lineno, text, "import_as"
                )
                found.update(
                    internal=True, module="." * (node.level or 0) + (node.module or "")
                )
                self.found.aliases.append(found)

    def import_module(self, node: ast.Import) -> None:
        text = self.place.lines[node.lineno - 1].strip()[:300]
        for name in node.names:
            last = name.name.split(".")[-1]
            if (
                name.asname
                and name.name.split(".")[0] in self.roots
                and name.asname != last
            ):
                found = alias(
                    self.place,
                    (name.asname, last),
                    node.lineno,
                    text,
                    "import_module_as",
                )
                found["internal"] = True
                self.found.aliases.append(found)

    def extract(self, source: str) -> Found:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return self.found
        self.found.definitions.extend(
            module_definition(self.place, ast.get_docstring(tree) or "")
        )
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.function(node)
            elif isinstance(node, ast.ClassDef):
                self.klass(node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                self.assignment(node)
            elif isinstance(node, ast.ImportFrom):
                self.import_from(node)
            elif isinstance(node, ast.Import):
                self.import_module(node)
        return self.found


def block_end(lines: list[str], start: int, cap: int = 300) -> int:
    depth, opened = 0, False
    for index in range(start, min(len(lines), start + cap)):
        text = re.sub(
            r'"(\\.|[^"\\])*"|\'(\\.|[^\'\\])*\'|`[^`]*`|//.*$', "", lines[index]
        )
        for character in text:
            if character == "{":
                depth, opened = depth + 1, True
            elif character == "}":
                depth -= 1
        if opened and depth <= 0:
            return index + 1
        if not opened and (
            text.rstrip().endswith(";") or (index > start and text.strip() == "")
        ):
            return index + 1
    return min(len(lines), start + cap)


def comment_above(lines: list[str], start: int) -> str:
    found: list[str] = []
    index = start - 1
    while index >= 0 and len(found) < 8:
        text = lines[index].strip()
        if not text.startswith(("//", "*", "/*", "#[", "@")):
            break
        if not text.startswith(("#[", "@")):
            found.append(re.sub(r"^(///?|/\*\*?|\*/?)\s?", "", text))
        index -= 1
    return " ".join(reversed(found)).strip()


def string_value(text: str) -> str | None:
    match = STRING_LITERAL.match(text)
    return match.group(2) if match else None


class BraceSource:
    def __init__(self, place: Place) -> None:
        self.place, self.found = place, Found()
        self.owner: str | None = None
        self.block: str | None = None

    def add(self, name: str, kind: str, index: int, detail: Detail = Detail()) -> None:
        lines = self.place.lines
        full = f"{detail.owner}.{name}" if detail.owner else name
        detail = Detail(
            lines[index],
            comment_above(lines, index),
            detail.owner,
            detail.value,
            detail.top,
            detail.bases,
        )
        span = (index + 1, block_end(lines, index))
        self.found.definitions.append(definition(self.place, full, kind, span, detail))

    def bind(
        self, names: tuple[str, str], index: int, how: str, internal: bool | None = None
    ) -> None:
        text = self.place.lines[index].strip()
        found = alias(
            self.place, names, index + 1, text[:300] if internal else text, how
        )
        found["internal"] = internal
        self.found.aliases.append(found)

    def go_line(self, index: int, line: str) -> None:
        match = re.match(
            r"^func\s+(\(\s*\w*\s*\*?(\w+)(\[[^\]]*\])?\s*\)\s*)?(\w+)\s*[\[(]", line
        )
        if match:
            owner = match.group(2)
            kind = "method" if owner else "function"
            self.add(match.group(4), kind, index, Detail(owner=owner, top=not owner))
            return
        match = re.match(r"^type\s+(\w+)\s*(\[[^\]]*\])?\s*(=)?\s*([\w.*]+)", line)
        if match:
            target = match.group(4).lstrip("*")
            if match.group(3) and IDENTIFIER.match(target):
                self.bind((match.group(1), target), index, "type_alias")
            else:
                self.add(match.group(1), "class", index)
            return
        self.go_value(index, line)

    def go_value(self, index: int, line: str) -> None:
        opened = re.match(r"^(const|var)\s*\(\s*$", line)
        if opened:
            self.block = opened.group(1)
            return
        if self.block and line.startswith(")"):
            self.block = None
            return
        match = re.match(
            r"^(const|var)\s+(\w+)\s*([\w.\[\]*]+)?\s*=\s*(.*)$", line
        ) or (
            self.block and re.match(r"^\s+()(\w+)\s*([\w.\[\]*]+)?\s*=\s*(.*)$", line)
        )
        if not match or match.group(2) == "_":
            return
        name, value = match.group(2), match.group(4).strip()
        if (
            IDENTIFIER.match(value)
            and not value[0].isdigit()
            and value not in {"true", "false", "nil", "iota"}
            and value.split(".")[-1] != name
        ):
            self.bind((name, value), index, "assign")
        else:
            self.add(name, "constant", index, Detail(value=string_value(value)))

    def rust_line(self, index: int, line: str) -> None:
        implementation = re.match(
            r"^impl(<[^>]*>)?\s+(?:[\w:<>, ]+\s+for\s+)?([\w:]+)", line
        )
        if implementation:
            self.owner = implementation.group(2).split("::")[-1]
            return
        if line.startswith("}"):
            self.owner = None
        function = re.match(
            r"^(\s*)(pub(\([^)]*\))?\s+)?(async\s+)?(unsafe\s+)?(const\s+)?(extern\s+\"C\"\s+)?fn\s+(\w+)",
            line,
        )
        if function:
            indent = len(function.group(1))
            if indent == 0:
                self.add(function.group(8), "function", index)
            elif self.owner and indent <= 4:
                self.add(
                    function.group(8),
                    "method",
                    index,
                    Detail(owner=self.owner, top=False),
                )
            return
        self.rust_item(index, line)

    def rust_item(self, index: int, line: str) -> None:
        item = re.match(
            r"^(pub(\([^)]*\))?\s+)?(struct|enum|trait|union)\s+(\w+)", line
        )
        if item:
            self.add(item.group(4), "class", index)
            return
        kind = re.match(
            r"^(pub(\([^)]*\))?\s+)?type\s+(\w+)(<[^>]*>)?\s*=\s*([\w:]+)\s*;", line
        )
        if kind:
            self.bind(
                (kind.group(3), kind.group(5).split("::")[-1]), index, "type_alias"
            )
            return
        constant = re.match(
            r"^(pub(\([^)]*\))?\s+)?(const|static)\s+(\w+)\s*:\s*[^=]+=\s*(.*)$", line
        )
        if constant:
            self.add(
                constant.group(4),
                "constant",
                index,
                Detail(value=string_value(constant.group(5))),
            )
            return
        if not re.match(r"^(pub(\([^)]*\))?\s+)?use\s+(crate|self|super)", line):
            return
        for match in re.finditer(r"(\w+)\s+as\s+(\w+)", line):
            if match.group(1) != match.group(2) and match.group(2) != "_":
                self.bind((match.group(2), match.group(1)), index, "use_as", True)

    def script_line(self, index: int, line: str) -> None:
        function = re.match(
            r"^(export\s+)?(default\s+)?(async\s+)?function\*?\s+([A-Za-z_$][\w$]*)",
            line,
        )
        if function:
            self.add(function.group(4), "function", index)
            return
        klass = re.match(
            r"^(export\s+)?(default\s+)?(abstract\s+)?class\s+(\w+)(\s+extends\s+([\w.]+))?",
            line,
        )
        if klass:
            bases = (klass.group(6).split(".")[-1],) if klass.group(6) else ()
            self.add(klass.group(4), "class", index, Detail(bases=bases))
            return
        declared = re.match(r"^(export\s+)?(declare\s+)?(interface|enum)\s+(\w+)", line)
        if declared:
            self.add(declared.group(4), "type", index)
            return
        kind = re.match(r"^(export\s+)?type\s+(\w+)(<[^>]*>)?\s*=\s*(.*)$", line)
        if kind:
            value = kind.group(4).strip().rstrip(";")
            if IDENTIFIER.match(value) and value.split(".")[-1] != kind.group(2):
                self.bind((kind.group(2), value), index, "type_alias")
            else:
                self.add(kind.group(2), "type", index)
            return
        self.script_binding(index, line)

    def script_binding(self, index: int, line: str) -> None:
        match = re.match(
            r"^(export\s+)?(const|let|var)\s+([A-Za-z_$][\w$]*)\s*(:[^=]+)?=\s*(.*)$",
            line,
        )
        if not match:
            self.script_export(index, line)
            return
        name, value = match.group(3), match.group(5).strip()
        bare = value.rstrip(";").strip()
        if re.match(
            r"^(async\s*)?(\([^)]*\)|[\w$]+)\s*(:[^=]+)?=>", value
        ) or value.startswith(("function", "async function", "async (")):
            self.add(name, "function", index)
        elif (
            IDENTIFIER.match(bare)
            and bare not in {"true", "false", "null", "undefined"}
            and bare.split(".")[-1] != name
        ):
            self.bind((name, bare), index, "assign")
        elif UPPER.match(name):
            self.add(name, "constant", index, Detail(value=string_value(value)))

    def script_export(self, index: int, line: str) -> None:
        match = re.match(
            r"^(export|import)\s*(type\s*)?\{([^}]*)\}\s*(from\s*['\"]([^'\"]+)['\"])?",
            line,
        )
        if not match or not (
            match.group(1) == "export" or (match.group(5) or "").startswith(".")
        ):
            return
        how = "reexport_as" if match.group(1) == "export" else "import_as"
        for name in re.finditer(r"([\w$]+)\s+as\s+([\w$]+)", match.group(3)):
            if name.group(1) != name.group(2) and name.group(1) != "default":
                self.bind((name.group(2), name.group(1)), index, how, True)

    def extract(self) -> Found:
        lines = self.place.lines
        self.found.definitions.extend(
            module_definition(self.place, comment_above(lines, 0))
        )
        handler = {"go": self.go_line, "rs": self.rust_line}.get(
            self.place.language, self.script_line
        )
        for index, line in enumerate(lines):
            handler(index, line)
        return self.found


def extract_tree(tree: dict[str, str]) -> Found:
    sources = {
        path: text for path, text in tree.items() if path.endswith(SOURCE_SUFFIXES)
    }
    roots = module_roots(list(sources))
    found = Found()
    for path in sorted(sources):
        if TEST_PATH.search(path):
            continue
        place = Place(path, language_of(path), sources[path].split("\n"))
        part = (
            PythonSource(place, roots).extract(sources[path])
            if place.language == "py"
            else BraceSource(place).extract()
        )
        found.definitions.extend(part.definitions)
        found.aliases.extend(part.aliases)
    return found
