"""Seeded-defect review cases built from the fixed-plan-effort hard fixtures.

Each case starts from a fixture's `repo/` (committed baseline) with its
`reference/` overlaid as the uncommitted change, then applies zero or more
mutations. Every mutation violates a stated plan step and is validated to fail
the fixture's hidden check on its own (`validate_cases.py`). `m1`/`m2` seed
logic defects; `m3` seeds subtler convention and contract defects.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
FIXTURES = HARNESS.parent / "fixed-plan-effort" / "fixtures"


@dataclass(frozen=True)
class Mutation:
    id: str
    file: str
    find: str
    replace: str
    plan_step: str

    @property
    def steps(self) -> frozenset[int]:
        """Plan step numbers the defect violates, parsed from `plan_step` ("3/4: ..." -> {3, 4})."""
        return frozenset(int(n) for n in self.plan_step.split(":", 1)[0].split("/"))


@dataclass(frozen=True)
class Case:
    id: str
    fixture: str
    mutations: tuple[Mutation, ...]


WEBHOOK = "hard-ts-webhook-retries"
REFUNDS = "hard-ts-order-refunds"
INVENTORY = "hard-py-inventory-reservations"

W_EXPONENT = Mutation(
    "W-exponent", "src/sender.ts",
    "config.baseDelayMs * 2 ** (attempts - 1)", "config.baseDelayMs * 2 ** attempts",
    "4: delay is baseDelayMs x 2^(attempts - 1)",
)
W_WORKER = Mutation(
    "W-worker", "src/worker.ts",
    '.filter(delivery => (delivery.status === "pending" || delivery.status === "retrying") && delivery.nextAttemptAt <= now)',
    '.filter(delivery => delivery.status === "pending" && delivery.nextAttemptAt <= now)',
    "6: worker picks up due retrying deliveries",
)
W_429 = Mutation(
    "W-429", "src/sender.ts",
    "return status >= 500 || status === 408 || status === 429;", "return status >= 500 || status === 408;",
    "3: 429 is retryable",
)
W_MAX = Mutation(
    "W-max-attempts", "src/sender.ts",
    "if (retryable && attempts < config.maxAttempts) {", "if (retryable && attempts <= config.maxAttempts) {",
    "4: retry only while attempts < maxAttempts",
)
W_CLEAR = Mutation(
    "W-clear-last-error", "src/sender.ts",
    'return { ...delivery, attempts, status: "delivered", lastError: null };',
    'return { ...delivery, attempts, status: "delivered" };',
    "3: a 2xx response clears lastError",
)
W_ORDER = Mutation(
    "W-status-order", "src/admin.ts",
    "const counts = { pending: 0, retrying: 0, delivered: 0, failed: 0 }",
    "const counts = { pending: 0, delivered: 0, failed: 0, retrying: 0 }",
    "7: statusCounts keys in declared status order",
)
R_BALANCE = Mutation(
    "R-balance", "src/handlers/refunds.ts",
    "const refundableCents = order.capturedCents - sumCents(order.refunds.map(refund => refund.amountCents));",
    "const refundableCents = order.capturedCents;",
    "4: refundable balance subtracts earlier refunds",
)
R_REVENUE = Mutation(
    "R-revenue", "src/reports.ts",
    ".map(order => order.capturedCents - sumCents(order.refunds.map(refund => refund.amountCents))),",
    ".map(order => order.capturedCents),",
    "8: revenue is net of refunds",
)
R_FULL = Mutation(
    "R-full-refund", "src/handlers/refunds.ts",
    "if (amountCents > refundableCents) {", "if (amountCents >= refundableCents) {",
    "3/4: a refund equal to the balance is allowed and yields `refunded`",
)
R_PAGINATE = Mutation(
    "R-paginate", "src/handlers/orders.ts",
    "  if (options.status) orders = orders.filter(order => order.status === options.status);\n"
    "  orders.sort((a, b) => b.createdAt.localeCompare(a.createdAt) || a.id.localeCompare(b.id));\n"
    "  return paginate(orders, options.limit ?? 20, options.cursor);",
    "  orders.sort((a, b) => b.createdAt.localeCompare(a.createdAt) || a.id.localeCompare(b.id));\n"
    "  const page = paginate(orders, options.limit ?? 20, options.cursor);\n"
    "  return options.status ? { ...page, items: page.items.filter(order => order.status === options.status) } : page;",
    "6: status filter applied like the customer filter; pagination unchanged",
)
R_PREFIX = Mutation(
    "R-id-prefix", "src/handlers/refunds.ts",
    'const refund = { id: store.nextId("rf"),', 'const refund = { id: store.nextId("ref"),',
    "4: refund ids use prefix `rf`",
)
R_MUTATE = Mutation(
    "R-mutates-stored", "src/handlers/refunds.ts",
    "  const refunded: Order = {\n    ...order,\n"
    '    status: amountCents === refundableCents ? "refunded" : "partially_refunded",\n'
    "    refunds: [...order.refunds, refund],\n  };",
    "  order.refunds.push(refund);\n  const refunded: Order = {\n    ...order,\n"
    '    status: amountCents === refundableCents ? "refunded" : "partially_refunded",\n  };',
    "3: never mutate the stored order",
)
I_BOUNDARY = Mutation(
    "I-expiry-boundary", "inventory/store.py",
    "if r.sku == sku and now < r.expires_at)", "if r.sku == sku and now <= r.expires_at)",
    "4: at expires_at a reservation no longer counts",
)
I_LOW_STOCK = Mutation(
    "I-low-stock", "inventory/reports.py",
    "if inventory.available(sku) <= inventory.get(sku).reorder_level]",
    "if inventory.get(sku).on_hand <= inventory.get(sku).reorder_level]",
    "8: low_stock uses available stock",
)
I_RESERVE = Mutation(
    "I-reserve-available", "inventory/store.py",
    "        available = self.available(sku)\n        if quantity > available:",
    "        available = self.get(sku).on_hand\n        if quantity > available:",
    "3: reserve checks available stock",
)
I_COMMIT = Mutation(
    "I-commit-expiry", "inventory/store.py",
    "if self.clock.now() >= reservation.expires_at:", "if self.clock.now() > reservation.expires_at:",
    "4/6: a reservation at expires_at is expired",
)
I_ISO = Mutation(
    "I-expired-at-iso", "inventory/store.py",
    "expired_at=reservation.expires_at.isoformat(),", "expired_at=str(reservation.expires_at),",
    "6: expired_at detail is an ISO 8601 string",
)
I_EVENT = Mutation(
    "I-event-name", "inventory/store.py",
    'emit(self.events, "stock.committed",', 'emit(self.events, "stock.commit",',
    "7: events follow the existing past-tense naming",
)

CASES: tuple[Case, ...] = (
    Case("webhook-clean", WEBHOOK, ()),
    Case("webhook-m1", WEBHOOK, (W_EXPONENT, W_WORKER)),
    Case("webhook-m2", WEBHOOK, (W_429, W_MAX)),
    Case("webhook-m3", WEBHOOK, (W_CLEAR, W_ORDER)),
    Case("refunds-clean", REFUNDS, ()),
    Case("refunds-m1", REFUNDS, (R_BALANCE, R_REVENUE)),
    Case("refunds-m2", REFUNDS, (R_FULL, R_MUTATE)),
    Case("refunds-m3", REFUNDS, (R_PREFIX, R_PAGINATE)),
    Case("inventory-clean", INVENTORY, ()),
    Case("inventory-m1", INVENTORY, (I_BOUNDARY, I_LOW_STOCK)),
    Case("inventory-m2", INVENTORY, (I_RESERVE, I_COMMIT)),
    Case("inventory-m3", INVENTORY, (I_ISO, I_EVENT)),
)


def _git(ws: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True, stdin=subprocess.DEVNULL)


def apply_mutation(ws: Path, mutation: Mutation) -> tuple[int, int]:
    """Apply one mutation; return the 1-indexed inclusive line span of the replacement."""
    path = ws / mutation.file
    text = path.read_text()
    if text.count(mutation.find) != 1:
        raise RuntimeError(f"{mutation.id}: anchor must match exactly once in {mutation.file}")
    if mutation.find.count("\n") != mutation.replace.count("\n"):
        raise RuntimeError(f"{mutation.id}: replacement must keep the line count so spans stay stable")
    start = text[: text.index(mutation.find)].count("\n") + 1
    path.write_text(text.replace(mutation.find, mutation.replace))
    return start, start + mutation.replace.count("\n")


def build_workspace(case: Case, ws: Path, mutations: tuple[Mutation, ...] | None = None) -> dict[str, dict]:
    """Baseline commit of repo/, then reference/ plus mutations left uncommitted.

    Returns defect id -> {file, start, end, steps, plan_step} in working-tree line numbers.
    """
    fixture = FIXTURES / case.fixture
    shutil.copytree(fixture / "repo", ws)
    _git(ws, "init", "-q")
    (ws / ".git" / "info" / "exclude").write_text("__pycache__/\n*.pyc\n.pytest_cache/\nnode_modules/\n.codegraph/\n")
    _git(ws, "add", "-A")
    _git(ws, "-c", "user.email=eval@local", "-c", "user.name=eval", "commit", "-qm", "baseline")
    shutil.copytree(fixture / "reference", ws, dirs_exist_ok=True)
    spans: dict[str, dict] = {}
    for mutation in case.mutations if mutations is None else mutations:
        start, end = apply_mutation(ws, mutation)
        spans[mutation.id] = {"file": mutation.file, "start": start, "end": end, "steps": sorted(mutation.steps),
                              "plan_step": mutation.plan_step}
    return spans
