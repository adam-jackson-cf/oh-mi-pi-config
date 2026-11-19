from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from guardrails.adapters.go.source import (
    TEST_NAME,
    has_errors,
    parse,
    test_functions,
    words,
)

SUFFIXES = (".go",)


class CollectionError(ValueError):
    pass


@dataclass(frozen=True)
class Case:
    selector: str
    fingerprint: str


@dataclass(frozen=True)
class Collected:
    cases: tuple[Case, ...]
    behaviours: Mapping[str, str | None]


def behaviour(name: str) -> str:
    match = TEST_NAME.match(name)
    prefix = match.group(1) if match else ""
    return " ".join(words(name[len(prefix) :]))


def fingerprint(body: str) -> str:
    return hashlib.sha256(" ".join(body.split()).encode("utf-8")).hexdigest()


def collect(path: str, text: str) -> Collected:
    root = parse(text)
    if has_errors(root):
        raise CollectionError(f"{path} does not parse as Go")
    cases = []
    behaviours: dict[str, str | None] = {}
    for function in test_functions(root):
        name_node = function.field("name")
        body = function.field("body")
        if name_node is None or body is None:
            continue
        selector = f"{path}::{name_node.text()}"
        if selector in behaviours:
            raise CollectionError(f"duplicate test function: {selector}")
        behaviours[selector] = behaviour(name_node.text())
        cases.append(Case(selector, fingerprint(body.text())))
    return Collected(tuple(cases), behaviours)
