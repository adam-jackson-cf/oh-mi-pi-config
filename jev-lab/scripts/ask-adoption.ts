#!/usr/bin/env bun
// Count jev_ask adoption per day from session transcripts: direct `jev_ask` tool calls, writes to
// the `xd://jev_ask` device (the real call route) and reads of its docs.
// Usage: bun jev-lab/scripts/ask-adoption.ts [--sessions <dir>] [--since YYYY-MM-DD] [--json]
import { readdir, readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { z } from "zod";

const DEVICE = "xd://jev_ask";
const toolCall = z.object({ type: z.literal("toolCall"), name: z.string(), arguments: z.looseObject({ path: z.string().optional() }) });
const line = z.object({
  type: z.literal("message"), timestamp: z.string(),
  message: z.object({ role: z.literal("assistant"), content: z.array(z.looseObject({ type: z.string() })) }),
});

type Day = { calls: number; deviceWrites: number; docReads: number };
const args = new Map<string, string>();
for (let i = 2; i < process.argv.length; i++) {
  const key = process.argv[i]!.replace(/^--/, "");
  const next = process.argv[i + 1];
  if (next === undefined || next.startsWith("--")) args.set(key, "");
  else { args.set(key, next); i++; }
}
const sessionsDir = args.get("sessions") || join(homedir(), ".omp", "agent", "sessions");
const since = args.get("since") ?? "";

const days = new Map<string, Day>();
const files = (await readdir(sessionsDir, { recursive: true })).filter((rel) => rel.endsWith(".jsonl"));
for (const rel of files) {
  for (const raw of (await readFile(join(sessionsDir, rel), "utf8")).split("\n")) {
    if (!raw.includes("jev_ask")) continue;
    let json: unknown;
    try { json = JSON.parse(raw); } catch { continue; }
    const row = line.safeParse(json);
    if (!row.success) continue;
    const date = row.data.timestamp.slice(0, 10);
    if (date < since) continue;
    for (const part of row.data.message.content) {
      const call = toolCall.safeParse(part);
      if (!call.success) continue;
      const day = days.get(date) ?? { calls: 0, deviceWrites: 0, docReads: 0 };
      const onDevice = call.data.arguments.path === DEVICE;
      if (call.data.name === "jev_ask") day.calls++;
      else if (call.data.name === "write" && onDevice) day.deviceWrites++;
      else if (call.data.name === "read" && onDevice) day.docReads++;
      else continue;
      days.set(date, day);
    }
  }
}

const rows = [...days.entries()].sort(([a], [b]) => (a < b ? -1 : 1)).map(([date, day]) => ({ date, ...day }));
if (args.has("json")) {
  console.log(JSON.stringify(rows, null, 2));
} else {
  console.log("date        calls  device_writes  doc_reads");
  for (const r of rows) console.log(`${r.date}  ${String(r.calls).padStart(5)}  ${String(r.deviceWrites).padStart(13)}  ${String(r.docReads).padStart(9)}`);
  const total = rows.reduce((s, r) => ({ calls: s.calls + r.calls, w: s.w + r.deviceWrites, d: s.d + r.docReads }), { calls: 0, w: 0, d: 0 });
  console.log(`total       ${String(total.calls).padStart(5)}  ${String(total.w).padStart(13)}  ${String(total.d).padStart(9)}`);
}
