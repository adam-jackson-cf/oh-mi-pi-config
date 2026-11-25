from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from functools import cache
from importlib import resources
from pathlib import PurePosixPath
from typing import Any

from guardrails.commands.structure.name_extract import (
    SOURCE_SUFFIXES,
    Found,
    Gram,
    extract_tree,
    flat,
    stem,
    words,
)

TRIVIAL = re.compile(
    r"^(utf-?8|utf-?16|ascii|latin-?1|true|false|none|null|yes|no|on|off|json|yaml|text"
    r"|html|xml|csv|id|name|type|value|key|data|default|auto|main|master|dev|prod|test"
    r"|debug|info|warning|error|get|post|put|patch|delete|strict|ignore|replace|w|r|rb|wb|a)$",
    re.IGNORECASE,
)
BINDING = re.compile(r"^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*){0,2}$")
NOT_ALIAS_ROOTS = frozenset({
    "Array",
    "Date",
    "False",
    "JSON",
    "Math",
    "None",
    "Number",
    "Object",
    "Path",
    "String",
    "Symbol",
    "True",
    "__file__",
    "cls",
    "console",
    "document",
    "exports",
    "false",
    "globalThis",
    "import",
    "module",
    "os",
    "pathlib",
    "process",
    "self",
    "sys",
    "this",
    "true",
    "window",
})
KIND_GROUP = {
    "class": "class",
    "type": "class",
    "function": "function",
    "constant": "constant",
    "module": "module",
}
CLONE_SIMILARITY = 0.5
HEAD_GROUP_CAP = 20
MAX_DOCUMENT_FREQUENCY = 40
Pair = dict[str, Any]


@cache
def synonyms() -> dict[str, frozenset[str]]:
    data = json.loads(
        resources.files("guardrails.data").joinpath("name-synonyms.json").read_bytes()
    )
    found: dict[str, set[str]] = defaultdict(set)
    for group in data["groups"]:
        for first in group:
            found[stem(first)].update(stem(other) for other in group if other != first)
    for first, second in data["pairs"]:
        found[stem(first)].add(stem(second))
        found[stem(second)].add(stem(first))
    return {word: frozenset(others) for word, others in found.items()}


def jaccard(first: Any, second: Any) -> float:
    left, right = set(first), set(second)
    return len(left & right) / len(left | right) if left | right else 0.0


def trigrams(text: str) -> set[str]:
    text = re.sub(r"[^a-z0-9]", "", text.lower())
    return {text[index : index + 3] for index in range(max(1, len(text) - 2))}


def canonical(tokens: list[str]) -> list[str]:
    table = synonyms()
    return [sorted(table.get(token, frozenset()) | {token})[0] for token in tokens]


def name_similarity(first: str, second: str) -> float:
    return max(
        jaccard(canonical(words(first)), canonical(words(second))),
        jaccard(trigrams(first), trigrams(second)),
    )


def suffix(path: str | None) -> str:
    return PurePosixPath(path or "").suffix[:3]


def definition_entry(item: dict[str, Any]) -> Pair:
    return {
        "name": item["name"],
        "kind": item["kind"],
        "path": item["path"],
        "line": item["line"],
        "_def": item,
    }


class NameIndex:
    def __init__(self, tree: dict[str, str]) -> None:
        found: Found = extract_tree(tree)
        self.tree = tree
        self.definitions = found.definitions
        self.aliases = found.aliases
        self.words: Counter[str] = Counter()
        for path, text in tree.items():
            if path.endswith(SOURCE_SUFFIXES):
                self.words.update(re.findall(r"[A-Za-z_$][\w$]*", text))
        self.by_base: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in self.definitions:
            self.by_base[item["base"]].append(item)

    def resolve(
        self, name: str, near: str | None = None, module: str | None = None
    ) -> dict[str, Any] | None:
        base = name.split(".")[-1]
        found = [d for d in self.by_base.get(base, []) if d["kind"] != "module"]
        found = found or self.by_base.get(base, [])
        if not found:
            return None

        def rank(item: dict[str, Any]) -> tuple[int, str, int]:
            score = 0
            folder = module.lstrip(".").replace(".", "/") if module else ""
            if folder and folder in item["path"]:
                score -= 2
            if (
                near
                and PurePosixPath(item["path"]).parent == PurePosixPath(near).parent
            ):
                score -= 1
            if near and suffix(item["path"]) != suffix(near):
                score += 5
            return score, item["path"], item["line"]

        return sorted(found, key=rank)[0]

    def resolve_alias(
        self, binding: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        parts = binding["target"].split(".")
        if len(parts) == 1:
            return self.resolve(
                binding["target"], binding["path"], binding.get("module")
            ), None
        language = suffix(binding["path"])
        wanted = ".".join(parts[-2:])
        exact = [
            d
            for d in self.definitions
            if d["name"] == wanted and suffix(d["path"]) == language
        ]
        if exact:
            return exact[0], None
        for root in (parts[-2], parts[0]):
            owners = [
                d
                for d in self.by_base.get(root, [])
                if d["top"]
                and d["kind"] in {"class", "type", "constant"}
                and suffix(d["path"]) == language
            ]
            if owners:
                return None, owners[0]
        tops = [
            d
            for d in self.by_base.get(parts[-1], [])
            if d["top"] and d["kind"] != "module" and suffix(d["path"]) == language
        ]
        if not tops:
            return None, None
        tops.sort(
            key=lambda d: (0 if parts[-2] in d["path"] else 1, d["path"], d["line"])
        )
        return tops[0], None


def mentions(code: str, name: str) -> bool:
    body = code.split("\n", 1)[-1]
    return bool(re.search(rf"\b{re.escape(name)}\b", body))


def body_similarity(first: Pair, second: Pair, bound: bool) -> float:
    left, right = first.get("_def"), second.get("_def")
    if bound:
        return 1.0
    if left is not None and right is not None and left["grams"] and right["grams"]:
        return jaccard(left["grams"], right["grams"])
    return 0.0


def features(
    index: NameIndex,
    first: Pair,
    second: Pair,
    rules: list[str],
    bound: dict[str, Any] | None,
) -> dict[str, Any]:
    value = second["kind"] == "string_value"
    if value:
        names = jaccard(trigrams(first["name"]), trigrams(second["value"]))
    else:
        names = name_similarity(
            first["name"].split(".")[-1], second["name"].split(".")[-1]
        )
    bodies = body_similarity(first, second, bound is not None)
    left, right = first.get("_def"), second.get("_def")
    calls = bool(
        left is not None
        and right is not None
        and "module" not in {left["kind"], right["kind"]}
        and (
            mentions(left["code"], right["base"])
            or mentions(right["code"], left["base"])
        )
    )
    return {
        "rules": sorted(rules),
        "alias_how": bound["how"] if bound else None,
        "name_sim": round(names, 4),
        "body_sim": round(bodies, 4),
        "one_refers_other": int(calls),
        "uses_a": max(0, index.words.get(first["name"].split(".")[-1], 0) - 1),
        "det_score": round(0.5 * round(names, 4) + 0.5 * round(bodies, 4), 4),
    }


class Proposer:
    def __init__(self, index: NameIndex) -> None:
        self.index = index
        self.pairs: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}

    def put(
        self, first: Pair, second: Pair, rule: str, bound: dict[str, Any] | None = None
    ) -> None:
        if first["name"] == second["name"]:
            return
        if second["kind"] != "string_value" and flat(first["name"]) == flat(
            second["name"]
        ):
            return
        key = tuple(
            sorted([
                (first["name"], first["path"] or ""),
                (second["name"], second.get("path") or ""),
            ])
        )
        if key in self.pairs:
            found = self.pairs[key]
            if rule not in found["rules"]:
                found["rules"].append(rule)
            if bound and not found.get("alias"):
                found["alias"] = bound
            return
        self.pairs[key] = {"a": first, "b": second, "rules": [rule], "alias": bound}

    def aliases(self) -> None:
        for binding in self.index.aliases:
            root = re.split(r"[.\[(]", binding["target"])[0]
            if not BINDING.match(binding["target"]) or root in NOT_ALIAS_ROOTS:
                continue
            first = {
                "name": binding["alias"],
                "kind": "alias",
                "path": binding["path"],
                "line": binding["line"],
                "binding": binding["text"],
                "_def": None,
            }
            target, owner = self.index.resolve_alias(binding)
            if owner is not None:
                second = {
                    "name": binding["target"],
                    "kind": "member",
                    "path": owner["path"],
                    "line": owner["line"],
                    "owner": owner["name"],
                    "_def": owner,
                }
            elif target is not None:
                second = definition_entry(target)
            elif binding.get("internal"):
                continue
            else:
                second = {
                    "name": binding["target"],
                    "kind": "external",
                    "path": None,
                    "line": None,
                    "_def": None,
                }
            self.put(first, second, "alias", binding)

    def representatives(self) -> dict[tuple[str, str], dict[str, Any]]:
        found: dict[tuple[str, str], dict[str, Any]] = {}
        for item in self.index.definitions:
            if item["top"] and item["kind"] in KIND_GROUP:
                found.setdefault((KIND_GROUP[item["kind"]], item["name"]), item)
        return found

    def head_nouns(
        self, representatives: dict[tuple[str, str], dict[str, Any]]
    ) -> None:
        groups: dict[tuple[str, str], list[tuple[list[str], dict[str, Any]]]] = (
            defaultdict(list)
        )
        for (group, name), item in representatives.items():
            tokens = words(name)
            if tokens and len(tokens[-1]) >= 3 and not tokens[-1].isdigit():
                groups[group, tokens[-1]].append((tokens, item))
        for members in groups.values():
            if not 2 <= len(members) <= HEAD_GROUP_CAP:
                continue
            for position, (left_tokens, left) in enumerate(members):
                for right_tokens, right in members[position + 1 :]:
                    if (
                        qualified(left_tokens, right_tokens)
                        and left["lang"] == right["lang"]
                    ):
                        self.put(
                            definition_entry(left), definition_entry(right), "head_noun"
                        )

    def synonym_swaps(
        self, representatives: dict[tuple[str, str], dict[str, Any]]
    ) -> None:
        table = synonyms()
        by_tokens: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = (
            defaultdict(list)
        )
        for (group, name), item in representatives.items():
            by_tokens[group, tuple(words(name))].append(item)
        for (group, tokens), items in list(by_tokens.items()):
            for position, token in enumerate(tokens):
                for other in table.get(token, frozenset()):
                    swapped = tokens[:position] + (other,) + tokens[position + 1 :]
                    for right in by_tokens.get((group, swapped), []):
                        for left in items:
                            if left["lang"] == right["lang"] and (
                                left["name"],
                                left["path"],
                            ) < (right["name"], right["path"]):
                                self.put(
                                    definition_entry(left),
                                    definition_entry(right),
                                    "synonym",
                                )

    def constant_values(self) -> None:
        for item in self.index.definitions:
            value = item.get("value")
            if item["kind"] != "constant" or not isinstance(value, str):
                continue
            if not re.match(
                r"^[A-Za-z][A-Za-z0-9_\-. ]{2,48}$", value
            ) or TRIVIAL.match(value):
                continue
            tokens = [
                token for token in words(value, keep_dots=True) if not token.isdigit()
            ]
            if (
                not tokens
                or len(tokens) > 4
                or not any(len(token) >= 3 for token in tokens)
            ):
                continue
            if set(words(item["name"])) & set(tokens):
                continue
            second = {
                "name": json.dumps(value),
                "kind": "string_value",
                "path": item["path"],
                "line": item["line"],
                "value": value,
                "_def": None,
            }
            self.put(definition_entry(item), second, "const_value")

    def clones(self) -> None:
        bodies = [
            d
            for d in self.index.definitions
            if d["kind"] in {"function", "method", "class"}
            and d["nlines"] >= 5
            and len(d["grams"]) >= 15
            and not d["base"].startswith("test")
        ]
        for (first, second), similarity in shared_grams(bodies).items():
            left, right = bodies[first], bodies[second]
            if similarity >= CLONE_SIMILARITY and clone_pair(left, right):
                self.put(definition_entry(left), definition_entry(right), "clone")

    def propose(self) -> list[dict[str, Any]]:
        self.aliases()
        representatives = self.representatives()
        self.head_nouns(representatives)
        self.synonym_swaps(representatives)
        self.constant_values()
        self.clones()
        found = []
        for pair in self.pairs.values():
            first, second = pair["a"], pair["b"]
            if (
                "alias" not in pair["rules"]
                and second["kind"] != "string_value"
                and (second["name"], second["path"] or "")
                < (first["name"], first["path"] or "")
            ):
                first, second = second, first
            pair["a"], pair["b"] = first, second
            pair["features"] = features(
                self.index, first, second, pair["rules"], pair.get("alias")
            )
            pair["det_score"] = pair["features"]["det_score"]
            found.append(pair)
        return found


def qualified(left: list[str], right: list[str]) -> bool:
    if left == right:
        return False
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    return (
        len(short) != len(long)
        and long[len(long) - len(short) :] == short
        and len(short) >= 2
    )


def shared_grams(bodies: list[dict[str, Any]]) -> dict[tuple[int, int], float]:
    frequency = Counter(gram for item in bodies for gram in item["grams"])
    postings: dict[Gram, list[int]] = defaultdict(list)
    for position, item in enumerate(bodies):
        for gram in item["grams"]:
            if frequency[gram] <= MAX_DOCUMENT_FREQUENCY:
                postings[gram].append(position)
    shared: Counter[tuple[int, int]] = Counter()
    for positions in postings.values():
        for offset, first in enumerate(positions):
            for second in positions[offset + 1 :]:
                shared[first, second] += 1
    return {
        (first, second): count
        / (len(bodies[first]["grams"]) + len(bodies[second]["grams"]) - count)
        for (first, second), count in shared.items()
    }


def clone_pair(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if flat(left["base"]) == flat(right["base"]) or left["lang"] != right["lang"]:
        return False
    if left["path"] == right["path"] and (
        left["line"] <= right["line"] <= left["end"]
        or right["line"] <= left["line"] <= right["end"]
    ):
        return False
    return not (
        "class" in {left["kind"], right["kind"]} and left["kind"] != right["kind"]
    )
