from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.commands.structure_naming.import_direction import direction_findings
from guardrails.commands.structure_naming.import_graph import import_graph_findings
from guardrails.foundation.findings import Finding

pytestmark = pytest.mark.fast

DIRECTION = "STRUCT.dependency-direction"
TIER = "STRUCT.tier-readership"
OWNER = "STRUCT.single-owner-placement"
SIBLING = "STRUCT.sibling-isolation"

SCRIPT_CONFIG = {
    "structure": {
        "tiers": ["shared", "features"],
        "import_direction": {"source_root": "web/src"},
    }
}
TSCONFIG = """{
  // the alias the app uses
  "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}},
}
"""
SCRIPT_BASE = {
    "web/tsconfig.json": TSCONFIG,
    "web/src/shared/format.ts": "export const format = 1;\n",
    "web/src/shared/clock/index.ts": "export const now = 1;\n",
    "web/src/features/orders.ts": "export const orders = 1;\n",
    "web/src/features/billing/index.ts": "export const billing = 1;\n",
}
SCRIPT_FORMS = {
    "relative": 'import { format } from "{to}";\n',
    "type-only": 'import type { Format } from "{to}";\n',
    "export-from": 'export { format } from "{to}";\n',
    "require": 'const format = require("{to}");\n',
    "dynamic import": 'export const load = () => import("{to}");\n',
}

GO_CONFIG = {
    "structure": {
        "tiers": ["store", "api"],
        "import_direction": {"source_root": "svc/internal"},
    }
}
GO_MODULE = "example.com/svc"
GO_BASE = {
    "svc/go.mod": f"module {GO_MODULE}\n\ngo 1.22\n",
    "svc/internal/store/orders/orders.go": "package orders\n",
    "svc/internal/store/ledger/ledger.go": "package ledger\n",
    "svc/internal/api/http/http.go": "package http\n",
    "svc/internal/api/grpc/grpc.go": "package grpc\n",
}


def go_source(package: str, *imports: str) -> str:
    specs = "".join(f'\t"{path}"\n' for path in imports)
    return f"package {package}\n\nimport (\n{specs})\n"


def internal(folder: str) -> str:
    return f"{GO_MODULE}/internal/{folder}"


class RepoCase(unittest.TestCase):
    def repo(self, files: dict[str, str]) -> Path:
        scratch = tempfile.TemporaryDirectory(prefix="omp-import-languages-")
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        for path, text in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return root

    def graph(self, files: dict[str, str], config: dict[str, Any]) -> list[Finding]:
        return import_graph_findings(self.repo(files), config, inventory=sorted(files))

    def direction(
        self, files: dict[str, str], path: str, config: dict[str, Any]
    ) -> list[Finding]:
        return direction_findings(self.repo(files), path, config)

    def rule(
        self, files: dict[str, str], config: dict[str, Any], rule_id: str
    ) -> set[str]:
        return {
            finding.path
            for finding in self.graph(files, config)
            if finding.rule_id == rule_id
        }


class TypeScriptImportTests(RepoCase):
    def test_every_import_form_resolves_for_dependency_direction(self) -> None:
        targets = {
            "relative": "../features/orders",
            "tsconfig paths alias": "@/features/orders",
            "index file": "../features/billing",
            ".js names the .ts source": "../features/orders.js",
        }
        cases = [
            (target_name, target, form_name, form.replace("{to}", target))
            for target_name, target in targets.items()
            for form_name, form in SCRIPT_FORMS.items()
        ]
        for target_name, target, form_name, source in cases:
            with self.subTest(target=target_name, form=form_name):
                path = "web/src/shared/leak.ts"
                files = {**SCRIPT_BASE, path: source}
                found = self.direction(files, path, SCRIPT_CONFIG)
                assert [(item.rule_id, item.message) for item in found] == [
                    (DIRECTION, f"unmapped or upward import {target}")
                ]

    def test_downward_and_own_tier_imports_are_allowed(self) -> None:
        path = "web/src/features/report.ts"
        source = (
            'import { format } from "../shared/format.js";\n'
            'import { now } from "@/shared/clock";\n'
            'import { orders } from "./orders";\n'
            'const billing = require("./billing");\n'
        )
        files = {**SCRIPT_BASE, path: source}
        assert self.direction(files, path, SCRIPT_CONFIG) == []

    def test_bare_packages_and_files_outside_the_source_root_are_ignored(
        self,
    ) -> None:
        path = "web/src/shared/view.tsx"
        source = (
            'import React from "react";\n'
            'import { z } from "@scope/zod";\n'
            'import config from "../../config";\n'
            'import { missing } from "./missing";\n'
        )
        files = {**SCRIPT_BASE, "web/config.ts": "export default 1;\n", path: source}
        assert self.direction(files, path, SCRIPT_CONFIG) == []

    def test_a_file_outside_any_tier_reports_every_import_inside_the_root(
        self,
    ) -> None:
        path = "web/src/main.js"
        files = {**SCRIPT_BASE, path: 'import { format } from "./shared/format";\n'}
        found = self.direction(files, path, SCRIPT_CONFIG)
        assert [item.message for item in found] == [
            "unmapped or upward import ./shared/format"
        ]

    def test_tier_readership(self) -> None:
        violating = {
            **SCRIPT_BASE,
            "web/src/features/orders.ts": 'import { format } from "../shared/format";\n',
            "web/src/features/billing/total.ts": (
                'import { now } from "@/shared/clock";\n'
                'import { format } from "../../shared/format.js";\n'
            ),
        }
        conforming = {
            **violating,
            "web/src/features/orders.ts": 'import { now } from "@/shared/clock";\n',
        }
        del violating["web/src/features/billing/total.ts"]
        assert self.rule(violating, SCRIPT_CONFIG, TIER) == {"web/src/shared"}
        assert self.rule(conforming, SCRIPT_CONFIG, TIER) == set()

    def test_single_owner_placement(self) -> None:
        conforming = {
            **SCRIPT_BASE,
            "web/src/features/orders.ts": (
                'import { now } from "../shared/clock";\n'
                'export const load = () => import("../shared/format");\n'
            ),
            "web/src/features/billing/index.ts": (
                'const clock = require("@/shared/clock/index.js");\n'
                'import type { Format } from "@/shared/format";\n'
            ),
        }
        violating = {
            **conforming,
            "web/src/features/orders.ts": 'import { now } from "../shared/clock";\n',
        }
        assert self.rule(violating, SCRIPT_CONFIG, OWNER) == {
            "web/src/shared/format.ts"
        }
        assert self.rule(conforming, SCRIPT_CONFIG, OWNER) == set()

    def test_sibling_isolation(self) -> None:
        violating = {
            **SCRIPT_BASE,
            "web/src/features/orders.ts": 'export { billing } from "./billing";\n',
        }
        conforming = {
            **SCRIPT_BASE,
            "web/src/features/billing/total.ts": 'import { b } from "./index";\n',
        }
        assert self.rule(violating, SCRIPT_CONFIG, SIBLING) == {
            "web/src/features/orders.ts"
        }
        assert self.rule(conforming, SCRIPT_CONFIG, SIBLING) == set()

    def test_dependency_direction_needs_only_the_source_root(self) -> None:
        path = "web/src/shared/leak.ts"
        files = {**SCRIPT_BASE, path: 'import "../features/orders";\n'}
        assert [
            item.rule_id for item in self.direction(files, path, SCRIPT_CONFIG)
        ] == [DIRECTION]
        unrooted = {"structure": {"tiers": ["shared", "features"]}}
        assert self.direction(files, path, unrooted) == []
        assert self.graph(files, unrooted) == []

    def test_parse_errors_and_unreadable_configs_are_findings(self) -> None:
        broken = "web/src/shared/broken.ts"
        files = {**SCRIPT_BASE, broken: "export const = ;\n"}
        for found in (
            self.direction(files, broken, SCRIPT_CONFIG),
            self.graph(files, SCRIPT_CONFIG),
        ):
            problems = [item for item in found if item.rule_id == DIRECTION]
            assert [(item.path, item.fix) for item in problems] == [
                (broken, "restore readable TypeScript imports")
            ]
            assert "cannot parse" in problems[0].message
        path = "web/src/shared/format.ts"
        files = {**SCRIPT_BASE, "web/tsconfig.json": "{"}
        found = self.direction(files, path, SCRIPT_CONFIG)
        assert [(item.path, item.message.split(":")[0]) for item in found] == [
            (path, "web/tsconfig.json")
        ]


class GoImportTests(RepoCase):
    def test_upward_imports_inside_the_module_are_dependency_direction(self) -> None:
        path = "svc/internal/store/orders/orders.go"
        files = {
            **GO_BASE,
            path: go_source(
                "orders",
                "fmt",
                "github.com/acme/kit",
                "example.com/svcx/internal/api/http",
                internal("store/ledger"),
                internal("api/http"),
            ),
        }
        found = self.direction(files, path, GO_CONFIG)
        assert [item.message for item in found] == [
            f"unmapped or upward import {internal('api/http')}"
        ]

    def test_the_module_path_comes_from_go_mod(self) -> None:
        path = "svc/internal/store/orders/orders.go"
        source = go_source("orders", "example.com/other/internal/api/http")
        files = {**GO_BASE, path: source}
        assert self.direction(files, path, GO_CONFIG) == []
        files["svc/go.mod"] = "module example.com/other\n"
        assert [item.rule_id for item in self.direction(files, path, GO_CONFIG)] == [
            DIRECTION
        ]

    def test_test_files_are_not_part_of_the_graph(self) -> None:
        path = "svc/internal/store/orders/orders_test.go"
        files = {
            **GO_BASE,
            "svc/internal/api/http/http.go": go_source(
                "http", internal("store/orders")
            ),
            "svc/internal/api/grpc/grpc_test.go": go_source(
                "grpc", internal("store/orders")
            ),
            path: go_source("orders", internal("api/http"), "broken\n"),
        }
        assert self.direction(files, path, GO_CONFIG) == []
        assert self.rule(files, GO_CONFIG, TIER) == {"svc/internal/store"}
        assert self.rule(files, GO_CONFIG, DIRECTION) == set()

    def test_tier_readership(self) -> None:
        violating = {
            **GO_BASE,
            "svc/internal/api/http/http.go": go_source(
                "http", internal("store/orders")
            ),
        }
        conforming = {
            **violating,
            "svc/internal/api/grpc/grpc.go": go_source(
                "grpc", internal("store/ledger")
            ),
        }
        assert self.rule(violating, GO_CONFIG, TIER) == {"svc/internal/store"}
        assert self.rule(conforming, GO_CONFIG, TIER) == set()

    def test_single_owner_placement(self) -> None:
        violating = {
            **GO_BASE,
            "svc/internal/api/http/http.go": go_source(
                "http", internal("store/orders")
            ),
            "svc/internal/api/grpc/grpc.go": go_source(
                "grpc", internal("store/orders"), internal("store/ledger")
            ),
            "svc/internal/api/grpc/server.go": go_source(
                "grpc", internal("store/ledger")
            ),
        }
        conforming = {
            **violating,
            "svc/internal/api/http/routes.go": go_source(
                "http", internal("store/ledger")
            ),
        }
        assert self.rule(violating, GO_CONFIG, OWNER) == {"svc/internal/store/ledger"}
        assert self.rule(conforming, GO_CONFIG, OWNER) == set()

    def test_sibling_isolation(self) -> None:
        violating = {
            **GO_BASE,
            "svc/internal/api/http/http.go": go_source("http", internal("api/grpc")),
        }
        conforming = {
            **GO_BASE,
            "svc/internal/api/http/http.go": go_source(
                "http", internal("api/http/routes")
            ),
            "svc/internal/api/http/routes/routes.go": "package routes\n",
        }
        assert self.rule(violating, GO_CONFIG, SIBLING) == {
            "svc/internal/api/http/http.go"
        }
        assert self.rule(conforming, GO_CONFIG, SIBLING) == set()

    def test_parse_errors_and_a_missing_go_mod_are_findings(self) -> None:
        broken = "svc/internal/store/orders/broken.go"
        files = {**GO_BASE, broken: "package orders\n\nfunc (\n"}
        for found in (
            self.direction(files, broken, GO_CONFIG),
            self.graph(files, GO_CONFIG),
        ):
            problems = [item for item in found if item.rule_id == DIRECTION]
            assert [(item.path, item.fix) for item in problems] == [
                (broken, "restore readable Go imports")
            ]
            assert "cannot parse" in problems[0].message
        files = {**GO_BASE, "svc/go.mod": "go 1.22\n"}
        path = "svc/internal/store/orders/orders.go"
        assert [item.message for item in self.direction(files, path, GO_CONFIG)] == [
            "svc/go.mod: go.mod has no module directive"
        ]
        del files["svc/go.mod"]
        assert [item.message for item in self.direction(files, path, GO_CONFIG)] == [
            f"no go.mod at or above {path}"
        ]


class PythonImportTests(RepoCase):
    CONFIG = {
        "structure": {
            "tiers": ["shared", "commands"],
            "import_direction": {"source_root": "src/orders"},
        }
    }

    def test_python_needs_the_package_name(self) -> None:
        path = "src/orders/commands/export.py"
        files = {path: "from acme.orders.shared import model\n"}
        expected = [(DIRECTION, path, "structure.import_direction.package is not set")]
        for found in (
            self.direction(files, path, self.CONFIG),
            self.graph(files, self.CONFIG),
        ):
            assert [(item.rule_id, item.path, item.message) for item in found] == (
                expected
            )

    def test_parse_errors_are_findings(self) -> None:
        config = {
            "structure": {
                "tiers": ["shared", "commands"],
                "import_direction": {
                    "package": "acme.orders",
                    "source_root": "src/orders",
                },
            }
        }
        path = "src/orders/shared/broken.py"
        files = {path: "def broken(:\n"}
        for found in (
            self.direction(files, path, config),
            self.graph(files, config),
        ):
            problems = [item for item in found if item.rule_id == DIRECTION]
            assert [(item.path, item.fix) for item in problems] == [
                (path, "restore readable Python imports")
            ]
            assert "cannot parse" in problems[0].message


if __name__ == "__main__":
    unittest.main()
