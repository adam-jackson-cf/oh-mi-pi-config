"""Name resolution and the pair rules of the structure-naming name-pair proposer."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.commands.structure_naming.name_pair import propose

BOTH = ("python", "typescript")


def not_test(path: str) -> bool:
    return False


TREE = {
    "app/__init__.py": "",
    "app/models.py": (
        "class Store:\n    def get(self):\n        return 1\n\n"
        "MODE = 'fast'\n\ndef helper():\n    pass\n"
    ),
    "app/views.py": (
        "from app.models import Store\nloader = Store.get\nreader = Store.missing\n"
        "link = models.Store\nother = thing.value\nouter = os.path\n"
    ),
    "lib/store.py": "",
    "web/models.ts": "export class Store {}\n",
    "web/use.ts": (
        "import { Store as Shop } from './models';\n"
        "import { Missing as Gone } from './nowhere';\n"
    ),
}


def named(pairs: list[dict[str, Any]]) -> set[tuple[str, str, str, str, str]]:
    return {
        (
            "+".join(pair["rules"]),
            pair["a"]["name"],
            pair["a"]["kind"],
            pair["b"]["name"],
            pair["b"]["kind"],
        )
        for pair in pairs
    }


def body(name: str, path: str, line: int, **extra: Any) -> dict[str, Any]:
    return {
        "base": name,
        "lang": "python",
        "path": path,
        "line": line,
        "end": line + 9,
        "kind": "function",
        **extra,
    }


@pytest.mark.fast
class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = propose.NameIndex(TREE, not_test, BOTH)

    def test_resolve_prefers_definitions_in_the_named_module_and_the_same_language(
        self,
    ) -> None:
        assert self.index.resolve("nothing") is None
        found = self.index.resolve("store")
        assert found is not None
        assert (found["kind"], found["path"]) == ("module", "lib/store.py")
        near = self.index.resolve("Store", "web/use.ts")
        hinted = self.index.resolve("Store", None, "app.models")
        assert near is not None
        assert hinted is not None
        assert (near["path"], hinted["path"]) == ("web/models.ts", "app/models.py")

    def test_each_alias_resolves_to_a_definition_an_owner_or_nothing(self) -> None:
        resolved = {}
        for binding in self.index.aliases:
            target, owner = self.index.resolve_alias(binding)
            resolved[binding["alias"]] = (
                target and target["name"],
                owner and owner["name"],
            )
        assert resolved == {
            "loader": ("Store.get", None),
            "reader": (None, "Store"),
            "link": ("Store", None),
            "other": (None, None),
            "outer": (None, None),
            "Shop": ("Store", None),
            "Gone": (None, None),
        }


@pytest.mark.fast
class ProposerRuleTests(unittest.TestCase):
    def test_aliases_pair_with_their_definition_member_or_external_name(self) -> None:
        pairs = propose.Proposer(propose.NameIndex(TREE, not_test, BOTH)).propose()
        assert named(pairs) == {
            ("alias", "loader", "alias", "Store.get", "method"),
            ("alias", "reader", "alias", "Store.missing", "member"),
            ("alias", "link", "alias", "Store", "class"),
            ("alias", "other", "alias", "thing.value", "external"),
            ("alias", "Shop", "alias", "Store", "class"),
            ("const_value", "MODE", "constant", '"fast"', "string_value"),
        }

    def test_a_pair_found_twice_keeps_one_entry_with_both_rules(self) -> None:
        proposer = propose.Proposer(propose.NameIndex({}, not_test, BOTH))
        first = {"name": "load_user", "kind": "function", "path": "a.py"}
        second = {"name": "fetch_user", "kind": "function", "path": "b.py"}
        proposer.put(first, {**first, "path": "c.py"}, "clone")
        proposer.put(first, {**second, "name": "LoadUser"}, "clone")
        proposer.put(first, second, "synonym")
        proposer.put(second, first, "clone", {"how": "assign"})
        proposer.put(first, second, "clone", {"how": "import_as"})
        (pair,) = proposer.pairs.values()
        assert pair["rules"] == ["synonym", "clone"]
        assert pair["alias"] == {"how": "assign"}

    def test_head_nouns_pair_qualified_names_in_one_language(self) -> None:
        tree = {
            "app/a.py": "class UserStore:\n    pass\n",
            "app/b.py": "class CachedUserStore:\n    pass\n",
            "app/c.py": "class Store2:\n    pass\n",
            "web/d.ts": "export class RemoteUserStore {}\n",
        }
        pairs = propose.Proposer(propose.NameIndex(tree, not_test, BOTH)).propose()
        assert named(pairs) == {
            ("head_noun", "CachedUserStore", "class", "UserStore", "class"),
        }

    def test_an_unproposed_language_does_not_hide_a_pair_of_the_proposed_one(
        self,
    ) -> None:
        tree = {
            "cmd/main.ts": "export function parse() {}\n",
            "tools/read.py": "def parse():\n    pass\n\ndef decode():\n    pass\n",
        }
        pairs = propose.Proposer(
            propose.NameIndex(tree, not_test, ("python",))
        ).propose()
        assert named(pairs) == {("synonym", "decode", "function", "parse", "function")}

    def test_a_constant_pairs_with_its_value_only_when_the_value_is_a_new_name(
        self,
    ) -> None:
        tree = {
            "app/k.py": (
                "MODE = 'fast lane'\nKIND = 'json'\nPATH = 'a/b'\nLONG = 'one two three four five'\n"
                "SHORT = 'ab cd'\nLANE = 'fast lane'\nNUMBER = 'x 12 y'\n"
            ),
        }
        pairs = propose.Proposer(propose.NameIndex(tree, not_test, BOTH)).propose()
        assert named(pairs) == {
            ("const_value", "MODE", "constant", '"fast lane"', "string_value"),
        }

    def test_qualified_needs_a_longer_name_ending_in_a_two_word_name(self) -> None:
        assert propose.qualified(["user", "store"], ["cached", "user", "store"])
        assert not propose.qualified(["user", "store"], ["user", "store"])
        assert not propose.qualified(["store"], ["user", "store"])

    def test_clone_pairs_need_distinct_names_one_language_and_no_nesting(self) -> None:
        left = body("load", "a.py", 1)
        assert propose.clone_pair(left, body("fetch", "b.py", 1))
        assert not propose.clone_pair(left, body("Load", "b.py", 1))
        assert not propose.clone_pair(left, body("fetch", "b.ts", 1, lang="typescript"))
        assert not propose.clone_pair(left, body("fetch", "a.py", 5))
        assert not propose.clone_pair(body("fetch", "a.py", 5), left)
        assert propose.clone_pair(left, body("fetch", "a.py", 20))
        assert not propose.clone_pair(left, body("Fetch", "b.py", 1, kind="class"))

    def test_shared_grams_ignore_grams_common_to_many_bodies(self) -> None:
        common = ("x", "=", "1")
        bodies = [
            {"grams": frozenset({common, (str(position), "+", "1")})}
            for position in range(propose.MAX_DOCUMENT_FREQUENCY + 1)
        ]
        bodies[1] = {"grams": frozenset({common, ("0", "+", "1")})}
        assert propose.shared_grams(bodies) == {(0, 1): 1 / 3}


if __name__ == "__main__":
    unittest.main()
