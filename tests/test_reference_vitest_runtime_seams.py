from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)


import pytest
from test_reference_vitest_runtime import VitestRuntimeCase

pytestmark = pytest.mark.seam


class VitestRuntimeTests(VitestRuntimeCase):
    def test_native_process_and_network_violations_remain_sticky_when_caught(
        self,
    ) -> None:
        result = self.node("""
for (const [name, operation] of [
  ['process', () => spawnSync(process.execPath, ['-e', ''])],
  ['net', () => connect({ host: '127.0.0.1', port: 9 })],
  ['fetch', () => fetch('http://127.0.0.1:9')],
  ['udp', () => { const socket = createSocket('udp4'); try { socket.send('x', 9, '127.0.0.1'); } finally { socket.close(); } }],
]) {
  const before = violations.length;
  try { operation(); } catch (error) { if (!error.message.includes('TEST.fast-tier-isolation')) throw error; }
  if (violations.length !== before + 1) throw new Error(`${name} bypassed the native process/network guard`);
}
""")
        assert len(result["violations"]) == 4
        assert all("TEST.fast-tier-isolation" in item for item in result["violations"])
        assert sum(event["kind"] == "violation" for event in result["events"]) == 4
        assert result["events"][0]["guards"] == ["fast-tier", "user-data"]

    def test_process_guard_covers_captured_node_exports_and_native_bun_apis(
        self,
    ) -> None:
        result = self.node("""
const operations = [() => spawnSync(process.execPath, ['-e', ''])];
if (globalThis.Bun) {
  operations.push(
    () => Bun.spawnSync([process.execPath, '-e', '']),
    () => Bun.spawn([process.execPath, '-e', '']),
  );
}
for (const operation of operations) {
  const before = violations.length;
  try { operation(); } catch (error) { if (!error.message.includes('TEST.fast-tier-isolation')) throw error; }
  if (violations.length !== before + 1) throw new Error('native process creation bypassed the guard');
}
""")
        assert "TEST.fast-tier-isolation" in result["violations"][0]
        reasons = [
            event["reason"]
            for event in result["events"]
            if event["kind"] == "violation"
        ]
        assert reasons == result["violations"]

    def test_user_home_guard_covers_native_promises_and_symlink_destinations(
        self,
    ) -> None:
        result = self.node(
            """
fs.symlinkSync(home, path.join(allowed, 'alias'), 'dir');
for (const operation of [
  () => fs.writeFileSync(path.join(home, 'sync.txt'), 'x'),
  () => fsp.writeFile(path.join(home, 'async.txt'), 'x'),
  () => fs.writeFileSync(path.join(allowed, 'alias', 'linked.txt'), 'x'),
  () => fs.renameSync(path.join(allowed, 'missing.txt'), path.join(home, 'renamed.txt')),
]) { try { await operation(); } catch (error) { if (!error.message.includes('TEST.user-data-guard')) throw error; } }
if (fs.readdirSync(home).length !== 0) throw new Error('guard allowed a real-home mutation');
fs.writeFileSync(path.join(allowed, 'safe.txt'), 'safe');
if (fs.readFileSync(path.join(allowed, 'safe.txt'), 'utf8') !== 'safe') throw new Error('temporary write failed');
""",
            tier="t2",
        )
        assert len(result["violations"]) == 4
        assert result["events"][0]["guards"] == ["user-data"]

    def test_slow_tier_allows_native_subprocess_but_keeps_user_data_guard(self) -> None:
        result = self.node(
            """
const result = spawnSync(process.execPath, ['-e', 'process.exit(0)']);
if (result.status !== 0) throw new Error('t2 process unexpectedly blocked');
""",
            tier="t2",
        )
        assert result["violations"] == []


if __name__ == "__main__":
    unittest.main()
