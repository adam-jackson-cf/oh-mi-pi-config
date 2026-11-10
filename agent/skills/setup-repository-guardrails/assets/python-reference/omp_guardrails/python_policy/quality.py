"""PYQ rules: maintained Python structure, class shape, seams and collaborator contracts.

``omp-guardrails python-quality [path ...]`` checks every inventoried ``.py`` file, or the named
files and directories. Banned words and casing come from the shipped house style; everything
else comes from the profile's ``guardrails.python`` section (patterns as in ``scope``):

- ``maintained_roots``: every maintained Python root. A file outside them is PYQ100, so an
  empty list flags every file instead of approving it.
- ``excluded_paths``: documented non-maintained paths (vendored bytes, canary inputs).
- ``script_roots``, ``script_names``, ``script_name_exceptions``: command scripts must be
  approved kebab-case basenames (PYQ101); the exceptions are importable support modules.
- ``test_roots``, ``test_groups`` (``{"<group>": ["<glob>", ...]}``), ``test_root_files``:
  every test path belongs to a canonical group (PYQ102); root files skip structure rules.
- ``generic_name_allowlist``: exact approved paths for banned directory or module words
  (PYQ103, PYQ104).
- ``non_package_dirs``: directory names that are not import packages (skipped by PYQ105).
- ``data_label_dirs``: directory patterns whose names are data labels, not packages or
  modules (skipped by PYQ103 and PYQ105).
- ``skill_roots``: skill directories must be kebab-case (PYQ106).
- ``class_limits``: ``max_lines`` (700), ``max_init_args`` (8), ``max_instance_attributes``
  (12) and ``max_public_methods`` (unset: PYQ201 off) for PYQ200-PYQ203.
- ``analyzer_roots``, ``analyzer_bases``, ``analyzer_entrypoints`` (``{"<base>": "<method>"}``):
  ``*Analyzer`` classes inherit an approved base and implement its entrypoint (PYQ210, PYQ211).
- ``process_roots`` and ``subprocess_wrappers`` (``{"<path>": ["<function>", "<Class.method>"]}``):
  process calls live only in exact approved wrapper functions (PYQ220). Nested functions do
  not inherit approval.
- ``dynamic_import_roots`` and ``dynamic_import_adapters`` (exact paths): PYQ221.
- ``contract_files`` and ``contract_data_annotations``
  (``{"<path>": {"<Owner.binding>": "<annotation>"}}``): collaborator annotations use named
  callable aliases and no ``Any``, except pinned JSON-record bindings (PYQ230, PYQ231).

``process_roots`` and ``dynamic_import_roots`` default to ``maintained_roots``.
"""

from __future__ import annotations

import ast
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from omp_guardrails.config import house_style
from omp_guardrails.findings import Finding
from omp_guardrails.python_policy.call_names import (
    DYNAMIC_IMPORT_CALLS,
    PROCESS_CALLS,
    import_names,
    qualified_name,
)
from omp_guardrails.python_policy.scope import (
    PythonSettings,
    finding,
    in_roots,
    inventory_finding,
    load_settings,
    matched_root,
    parse_paths,
    python_files,
    read_source,
    root_depth,
    segments,
)
from omp_guardrails.structure.inventory import (
    GLOB_CHARACTERS,
    InventoryError,
    as_list,
    normalized_path,
)

CLASS_LIMIT_DEFAULTS = {
    "max_lines": 700,
    "max_public_methods": None,
    "max_init_args": 8,
    "max_instance_attributes": 12,
}
CALLABLE_NAMES = {"typing.Callable", "collections.abc.Callable", "Callable"}
ANY_NAMES = {"typing.Any", "Any"}


def casing(kind: str, ecosystem: str = "python") -> list[str]:
    return house_style.ecosystem(ecosystem)[kind]


def is_label(prefix: str, config: PythonSettings) -> bool:
    return any(
        len(segments(pattern)) == len(segments(prefix)) and root_depth(prefix, pattern)
        for pattern in config.paths("data_label_dirs")
    )


def below_root_dirs(relpath: str, depth: int) -> list[tuple[str, str]]:
    """``(segment, path up to it)`` for each directory below the matched maintained root."""
    parts = segments(relpath)
    return [(parts[index], "/".join(parts[: index + 1])) for index in range(depth, len(parts) - 1)]


# Structure rules (PYQ100-PYQ107) ------------------------------------------------------------


def structure_findings(relpath: str, config: PythonSettings) -> list[Finding]:
    depth = matched_root(relpath, config.paths("maintained_roots"))
    if depth is None:
        return [
            finding(
                "PYQ100",
                relpath,
                None,
                "Python file is outside the maintained roots",
                "move it into an owned root or add its root to guardrails.python.maintained_roots",
            )
        ]
    if relpath in config.paths("test_root_files"):
        return []
    return [
        *script_findings(relpath, config),
        *test_group_findings(relpath, config),
        *skill_findings(relpath, config),
        *package_dir_findings(relpath, depth, config),
        *module_name_findings(relpath, config),
        *generic_name_findings(relpath, depth, config),
    ]


def script_findings(relpath: str, config: PythonSettings) -> list[Finding]:
    if not in_roots(relpath, config.paths("script_roots")):
        return []
    if relpath in config.paths("script_name_exceptions"):
        return []
    name = PurePosixPath(relpath).name
    approved = name in as_list(config.raw.get("script_names"))
    if approved and house_style.matches_casing(PurePosixPath(name).stem, casing("file", "asset_id")):
        return []
    return [
        finding(
            "PYQ101",
            relpath,
            None,
            f"command '{name}' is not an approved kebab-case command",
            "rename it to kebab-case and add its basename to guardrails.python.script_names",
        )
    ]


def test_group_findings(relpath: str, config: PythonSettings) -> list[Finding]:
    if not in_roots(relpath, config.paths("test_roots")):
        return []
    for patterns in config.mapping("test_groups").values():
        for pattern in as_list(patterns):
            # fnmatch's '*' crosses '/', so a pattern without '**' must match depth exactly.
            same_depth = "**" in pattern or relpath.count("/") == pattern.count("/")
            if same_depth and fnmatchcase(relpath, pattern):
                return []
    return [
        finding(
            "PYQ102",
            relpath,
            None,
            "test path belongs to no canonical test group",
            "move it into a group or map its pattern in guardrails.python.test_groups",
        )
    ]


def skill_name_finding(relpath: str, skill: str) -> list[Finding]:
    if house_style.matches_casing(skill, casing("directory", "asset_id")):
        return []
    return [
        finding(
            "PYQ106",
            relpath,
            None,
            f"skill directory '{skill}' is not kebab-case",
            "rename the skill directory to kebab-case",
        )
    ]


def skill_findings(relpath: str, config: PythonSettings) -> list[Finding]:
    for root in config.paths("skill_roots"):
        depth = root_depth(relpath, root)
        if depth is not None and len(segments(relpath)) > depth + 1:
            return skill_name_finding(relpath, segments(relpath)[depth])
    return []


def skill_directory_findings(repo: Path, config: PythonSettings, selected: list[str]) -> list[Finding]:
    """Skill directories without any Python file still need kebab-case names."""
    found: list[Finding] = []
    for root in config.paths("skill_roots"):
        if GLOB_CHARACTERS & set(root):
            continue
        skill_root = repo / root
        if not skill_root.is_dir():
            continue
        for directory in sorted(child for child in skill_root.iterdir() if child.is_dir()):
            relpath = f"{root}/{directory.name}"
            if not selected or any(
                root_depth(relpath, path) is not None or root_depth(path, relpath) is not None
                for path in selected
            ):
                found.extend(skill_name_finding(relpath, directory.name))
    return found


def package_dir_findings(relpath: str, depth: int, config: PythonSettings) -> list[Finding]:
    if in_roots(relpath, config.paths("skill_roots")):
        return []
    ignored = set(as_list(config.raw.get("non_package_dirs")))
    return [
        finding(
            "PYQ105",
            relpath,
            None,
            f"Python package directory '{part}' is not snake_case",
            "rename the directory to snake_case",
        )
        for part, prefix in below_root_dirs(relpath, depth)
        if part not in ignored
        and not is_label(prefix, config)
        and not house_style.matches_casing(part, casing("directory"))
    ]


def module_name_findings(relpath: str, config: PythonSettings) -> list[Finding]:
    if in_roots(relpath, config.paths("script_roots")):
        return []
    stem = PurePosixPath(relpath).stem
    if house_style.matches_casing(stem, casing("file")):
        return []
    return [
        finding(
            "PYQ107",
            relpath,
            None,
            f"Python module '{PurePosixPath(relpath).name}' is not snake_case",
            "rename the module to snake_case",
        )
    ]


def generic_name_findings(relpath: str, depth: int, config: PythonSettings) -> list[Finding]:
    allowed = set(config.paths("generic_name_allowlist"))
    found = [
        finding(
            "PYQ103",
            relpath,
            None,
            f"catch-all directory '{part}' is a house-style banned word",
            "rename it for its owner or approve the exact path in "
            "guardrails.python.generic_name_allowlist",
        )
        for part, prefix in below_root_dirs(relpath, depth)
        if house_style.is_banned_word(part) and prefix not in allowed and not is_label(prefix, config)
    ]
    stem = PurePosixPath(relpath).stem
    if house_style.is_banned_word(stem) and relpath not in allowed:
        found.append(
            finding(
                "PYQ104",
                relpath,
                None,
                f"catch-all module '{stem}' is a house-style banned word",
                "rename it for its responsibility or approve the exact path in "
                "guardrails.python.generic_name_allowlist",
            )
        )
    return found


# Class shape (PYQ200-PYQ203) and analyzer boundaries (PYQ210, PYQ211) -------------------------


def class_limits(config: PythonSettings) -> dict:
    return {key: config.number("class_limits", key, value) for key, value in CLASS_LIMIT_DEFAULTS.items()}


def init_arg_count(method: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = [*method.args.posonlyargs, *method.args.args]
    if args and args[0].arg == "self":
        args = args[1:]
    return len(args) + len(method.args.kwonlyargs)


def self_attributes(class_node: ast.ClassDef) -> set[str]:
    return {
        node.attr
        for node in ast.walk(class_node)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }


def methods(class_node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [node for node in class_node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def class_shape_findings(node: ast.ClassDef, relpath: str, limits: dict) -> list[Finding]:
    measures = [
        (
            "PYQ200",
            "max_lines",
            (node.end_lineno or node.lineno) - node.lineno + 1,
            "lines",
            "split the class by responsibility",
            node.lineno,
        ),
        (
            "PYQ201",
            "max_public_methods",
            sum(1 for method in methods(node) if not method.name.startswith("_")),
            "public methods",
            "split the interface into smaller collaborators",
            node.lineno,
        ),
        (
            "PYQ203",
            "max_instance_attributes",
            len(self_attributes(node)),
            "instance attributes",
            "group related state into value objects",
            node.lineno,
        ),
    ]
    for method in methods(node):
        if method.name == "__init__":
            measures.append((
                "PYQ202",
                "max_init_args",
                init_arg_count(method),
                "constructor parameters",
                "pass a parameter object or split the class",
                method.lineno,
            ))
    return [
        finding(rule, relpath, line, f"class '{node.name}' has {count} {label} > {limits[key]}", fix)
        for rule, key, count, label, fix, line in measures
        if limits[key] is not None and count > limits[key]
    ]


def base_names(class_node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in class_node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        dotted = ast.unparse(target)
        names.update({dotted, dotted.rsplit(".", 1)[-1]})
    return names


def analyzer_findings(node: ast.ClassDef, relpath: str, config: PythonSettings) -> list[Finding]:
    found: list[Finding] = []
    bases = base_names(node)
    approved = set(as_list(config.raw.get("analyzer_bases")))
    if (
        in_roots(relpath, config.paths("analyzer_roots"))
        and node.name.endswith("Analyzer")
        and not approved & bases
    ):
        found.append(
            finding(
                "PYQ210",
                relpath,
                node.lineno,
                f"analyzer class '{node.name}' inherits no approved analyzer base",
                "inherit a base listed in guardrails.python.analyzer_bases",
            )
        )
    defined = {method.name for method in methods(node)}
    for base, entrypoint in config.mapping("analyzer_entrypoints").items():
        if base in bases and entrypoint not in defined:
            found.append(
                finding(
                    "PYQ211",
                    relpath,
                    node.lineno,
                    f"project analyzer class '{node.name}' does not define {entrypoint}",
                    f"implement {entrypoint} on the class",
                )
            )
    return found


# Seams (PYQ220, PYQ221) ----------------------------------------------------------------------


def seam_findings(tree: ast.Module, relpath: str, config: PythonSettings) -> list[Finding]:
    process_scope = in_roots(relpath, config.scope("process_roots"))
    dynamic_scope = in_roots(relpath, config.scope("dynamic_import_roots")) and relpath not in set(
        config.paths("dynamic_import_adapters")
    )
    if not process_scope and not dynamic_scope:
        return []
    allowed = set(as_list(config.mapping("subprocess_wrappers").get(relpath)))
    found: list[Finding] = []

    def visit(node: ast.AST, names: dict[str, str], owner: str) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = f"{owner}.{node.name}" if owner else node.name
            names = names | import_names(node)
        elif isinstance(node, ast.Lambda):
            owner = f"{owner}.<lambda>"
        if isinstance(node, ast.Call):
            name = qualified_name(node.func, names)
            if process_scope and name in PROCESS_CALLS and owner not in allowed:
                found.append(
                    finding(
                        "PYQ220",
                        relpath,
                        node.lineno,
                        f"{name} runs outside an approved process wrapper",
                        "move the call into a wrapper listed in guardrails.python.subprocess_wrappers",
                    )
                )
            if dynamic_scope and name in DYNAMIC_IMPORT_CALLS:
                found.append(
                    finding(
                        "PYQ221",
                        relpath,
                        node.lineno,
                        f"{name} is a dynamic import outside an approved adapter",
                        "use a static import or list the adapter in "
                        "guardrails.python.dynamic_import_adapters",
                    )
                )
        for child in ast.iter_child_nodes(node):
            visit(child, names, owner)

    visit(tree, import_names(tree), "")
    return found


# Collaborator contracts (PYQ230, PYQ231) -----------------------------------------------------


def annotation_tree(annotation: ast.AST) -> ast.AST:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            return ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return annotation
    return annotation


def module_aliases(tree: ast.Module) -> dict[str, ast.AST]:
    aliases: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            aliases[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            aliases[node.target.id] = node.value
        elif isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
            aliases[node.name.id] = node.value
    return aliases


def annotation_contains(
    annotation: ast.AST,
    names: dict[str, str],
    aliases: dict[str, ast.AST],
    wanted: set[str],
    seen: frozenset[str] = frozenset(),
) -> bool:
    for part in ast.walk(annotation_tree(annotation)):
        if qualified_name(part, names) in wanted:
            return True
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            parsed = annotation_tree(part)
            if parsed is not part and annotation_contains(parsed, names, aliases, wanted, seen):
                return True
        if (
            isinstance(part, ast.Name)
            and part.id in aliases
            and part.id not in seen
            and annotation_contains(aliases[part.id], names, aliases, wanted, seen | {part.id})
        ):
            return True
    return False


def callable_arguments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    return [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
        *([node.args.vararg] if node.args.vararg else []),
        *([node.args.kwarg] if node.args.kwarg else []),
    ]


def collaborator_findings(
    annotation: ast.AST,
    binding: str,
    relpath: str,
    context: tuple[dict[str, str], dict[str, ast.AST], dict],
) -> list[Finding]:
    names, aliases, pinned = context
    line = getattr(annotation, "lineno", 1)
    found: list[Finding] = []
    if annotation_contains(annotation, names, {}, CALLABLE_NAMES):
        found.append(
            finding(
                "PYQ230",
                relpath,
                line,
                f"collaborator '{binding}' uses an inline Callable",
                "define and use a named callable alias",
            )
        )
    is_json_record = pinned.get(binding) == ast.unparse(annotation_tree(annotation))
    if not is_json_record and annotation_contains(annotation, names, aliases, ANY_NAMES):
        found.append(
            finding(
                "PYQ231",
                relpath,
                line,
                f"collaborator '{binding}' annotation contains Any",
                "use a concrete type or a capability protocol, or pin a JSON-record binding in "
                "guardrails.python.contract_data_annotations",
            )
        )
    return found


def contract_findings(tree: ast.Module, relpath: str, config: PythonSettings) -> list[Finding]:
    if relpath not in set(config.paths("contract_files")):
        return []
    pinned = config.mapping("contract_data_annotations").get(relpath)
    context = (import_names(tree), module_aliases(tree), pinned if isinstance(pinned, dict) else {})
    found: list[Finding] = []

    def visit(node: ast.AST, owner: str) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = f"{owner}.{node.name}" if owner else node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in callable_arguments(node):
                if arg.annotation:
                    found.extend(
                        collaborator_findings(arg.annotation, f"{owner}.{arg.arg}", relpath, context)
                    )
            return
        if isinstance(node, ast.AnnAssign) and owner:
            binding = f"{owner}.{ast.unparse(node.target)}"
            found.extend(collaborator_findings(node.annotation, binding, relpath, context))
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(tree, "")
    return found


# Entry point --------------------------------------------------------------------------------


def code_findings(repo: Path, relpath: str, config: PythonSettings) -> list[Finding]:
    source = read_source(repo, relpath)
    if source.tree is None:
        return [finding("PYQ299", relpath, None, source.error, "restore readable UTF-8 Python")]
    limits = class_limits(config)
    found: list[Finding] = []
    for node in ast.walk(source.tree):
        if isinstance(node, ast.ClassDef):
            found.extend(class_shape_findings(node, relpath, limits))
            found.extend(analyzer_findings(node, relpath, config))
    found.extend(seam_findings(source.tree, relpath, config))
    found.extend(contract_findings(source.tree, relpath, config))
    return found


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    selected = parse_paths("python-quality", argv)
    config = load_settings(profile)
    try:
        files = python_files(repo, config, selected)
    except InventoryError as error:
        return [inventory_finding("PYQ100", error)]
    roots = config.paths("maintained_roots")
    found: list[Finding] = []
    for relpath in files:
        found.extend(structure_findings(relpath, config))
        if in_roots(relpath, roots):
            found.extend(code_findings(repo, relpath, config))
    scope = [path for raw in selected if (path := normalized_path(repo, raw)) is not None]
    found.extend(skill_directory_findings(repo, config, scope))
    return house_style.apply_exceptions(found, profile)
