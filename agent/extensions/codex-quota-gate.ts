import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { z } from "zod";

const USAGE_POLL_INTERVAL_MS = 5 * 60 * 1000;
const USAGE_COMMAND_TIMEOUT_MS = 30 * 1000;
const DEFAULT_THRESHOLD_FRACTION = 0.85;
const QUOTA_GATE_COMMAND = "quota-gate";
const BLOCK_REASON =
  "Codex quota gate is paused. Use /quota-gate continue to allow more work, or /quota-gate off to disable it.";

type GateState = "disabled" | "open" | "latched";

export type QuotaSample = {
  usedFraction: number;
  resetsAt: number;
};

const CodexUsagePayloadSchema = z.object({
  reports: z.array(
    z.object({
      provider: z.string(),
      limits: z.array(
        z.object({
          id: z.string(),
          scope: z
            .object({
              windowId: z.string().optional(),
              tier: z.string().optional(),
            })
            .loose(),
          amount: z.object({ usedFraction: z.number().optional() }).loose(),
          window: z.object({ resetsAt: z.number().optional() }).loose(),
        }),
      ),
    }),
  ),
});

export type CodexUsagePayload = z.infer<typeof CodexUsagePayloadSchema>;

/** Returns the parsed `omp usage` payload; rejects when usage is unavailable or malformed. */
export type UsageReader = (ctx: ExtensionContext) => Promise<CodexUsagePayload>;

export function parseThreshold(value: string): number | undefined {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return undefined;
  }

  const fraction = parsed > 1 ? parsed / 100 : parsed;
  return fraction > 0 && fraction <= 1 ? fraction : undefined;
}

export function selectSevenDayQuotaSample(payload: CodexUsagePayload): QuotaSample | undefined {
  const samples = payload.reports.flatMap(report => {
    if (report.provider !== "openai-codex") {
      return [];
    }

    const limit = report.limits.find(candidate => {
      const usedFraction = candidate.amount.usedFraction;
      const resetsAt = candidate.window.resetsAt;
      return (
        candidate.scope.windowId === "7d" &&
        candidate.scope.tier !== "spark" &&
        !candidate.id.includes(":spark:") &&
        usedFraction !== undefined &&
        resetsAt !== undefined
      );
    });
    if (!limit || limit.amount.usedFraction === undefined || limit.window.resetsAt === undefined) {
      return [];
    }

    return [{ usedFraction: limit.amount.usedFraction, resetsAt: limit.window.resetsAt }];
  });
  return samples.length === 1 ? samples[0] : undefined;
}

function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

function formatRemaining(resetsAt: number): string {
  let seconds = Math.max(0, Math.ceil((resetsAt - Date.now()) / 1000));
  const days = Math.floor(seconds / 86_400);
  seconds -= days * 86_400;
  const hours = Math.floor(seconds / 3_600);
  seconds -= hours * 3_600;
  const minutes = Math.floor(seconds / 60);

  const parts = [
    days > 0 ? `${days}d` : undefined,
    hours > 0 ? `${hours}h` : undefined,
    `${minutes}m`,
  ].filter((part): part is string => part !== undefined);
  return parts.join(" ");
}

async function readUsageFromOmp(pi: ExtensionAPI, ctx: ExtensionContext): Promise<CodexUsagePayload> {
  const result = await pi.exec(
    "omp",
    ["usage", "--json", "--redact", "--provider", "openai-codex"],
    { cwd: ctx.cwd, timeout: USAGE_COMMAND_TIMEOUT_MS },
  );
  if (result.code !== 0 || result.killed) {
    throw new Error("OMP usage command failed");
  }

  return CodexUsagePayloadSchema.parse(JSON.parse(result.stdout));
}

export function registerCodexQuotaGate(
  pi: ExtensionAPI,
  readUsage: UsageReader = ctx => readUsageFromOmp(pi, ctx),
): void {
  let gateState: GateState = "disabled";
  let thresholdFraction = DEFAULT_THRESHOLD_FRACTION;
  let lastSample: QuotaSample | undefined;
  let checking = false;
  let failureNotified = false;

  const status = (): string => {
    if (gateState === "disabled") {
      return "Codex quota gate is disabled.";
    }

    const threshold = formatPercent(thresholdFraction);
    if (!lastSample) {
      return `Codex quota gate is active at ${threshold}; no seven-day usage sample is available yet.`;
    }

    const sample = `${formatPercent(lastSample.usedFraction)} used; reset in ${formatRemaining(lastSample.resetsAt)}`;
    return gateState === "latched"
      ? `Codex quota gate is paused at ${sample}; threshold ${threshold}.`
      : `Codex quota gate is active at ${sample}; threshold ${threshold}.`;
  };

  const setOpen = (threshold: number): void => {
    thresholdFraction = threshold;
    gateState = "open";
    failureNotified = false;
  };

  const tripGate = async (ctx: ExtensionContext, sample: QuotaSample): Promise<void> => {
    if (gateState !== "open") {
      return;
    }

    gateState = "latched";
    const message = `Codex seven-day quota reached ${formatPercent(sample.usedFraction)} (threshold ${formatPercent(thresholdFraction)}; reset in ${formatRemaining(sample.resetsAt)}).`;
    const resumeCurrentTurn = !ctx.isIdle();
    ctx.abort();
    ctx.ui.notify(`${message} Work is paused.`, "warning");

    if (!ctx.hasUI) {
      return;
    }

    const continueWork = await ctx.ui.confirm("Codex quota gate", `${message} Continue work?`);
    if (!continueWork) {
      return;
    }

    gateState = "open";
    ctx.ui.notify("Codex quota gate reopened until the next usage check.", "warning");
    if (resumeCurrentTurn) {
      pi.sendUserMessage(
        "[quota gate] The user approved continuing despite the Codex seven-day quota threshold. Resume the interrupted work.",
        { deliverAs: "followUp" },
      );
    }
  };

  const poll = async (ctx: ExtensionContext): Promise<void> => {
    if (gateState !== "open" || checking) {
      return;
    }

    checking = true;
    try {
      const sample = selectSevenDayQuotaSample(await readUsage(ctx));
      if (!sample) {
        throw new Error("OMP usage did not include a seven-day non-Spark Codex quota");
      }

      lastSample = sample;
      failureNotified = false;
      if (sample.usedFraction >= thresholdFraction) {
        await tripGate(ctx, sample);
      }
    } catch {
      if (!failureNotified) {
        ctx.ui.notify("Codex quota usage is unavailable or ambiguous; the quota gate remains open.", "warning");
        failureNotified = true;
      }
    } finally {
      checking = false;
    }
  };


  pi.registerCommand(QUOTA_GATE_COMMAND, {
    description: "Control the seven-day Codex quota gate: on [threshold], off, continue, status",
    handler: async (args, ctx) => {
      const [action = "status", value] = args.trim().toLowerCase().split(/\s+/, 2);

      if (action === "status") {
        ctx.ui.notify(status(), "info");
        return;
      }

      if (action === "on") {
        const threshold = parseThreshold(value ?? String(DEFAULT_THRESHOLD_FRACTION));
        if (threshold === undefined) {
          ctx.ui.notify("Usage: /quota-gate on <fraction-or-percent>", "error");
          return;
        }

        setOpen(threshold);
        await poll(ctx);
        ctx.ui.notify(status(), "info");
        return;
      }

      if (action === "off") {
        gateState = "disabled";
        ctx.ui.notify("Codex quota gate disabled.", "info");
        return;
      }

      if (action === "continue") {
        if (gateState !== "latched") {
          ctx.ui.notify("Codex quota gate is not paused.", "info");
          return;
        }

        gateState = "open";
        ctx.ui.notify("Codex quota gate reopened until the next usage check.", "warning");
        return;
      }

      ctx.ui.notify("Usage: /quota-gate [on <fraction-or-percent>|off|continue|status]", "error");
    },
  });

  pi.on("session_start", (_event, ctx) => {
    gateState = "open";
    ctx.setInterval(() => poll(ctx), USAGE_POLL_INTERVAL_MS);
    // `omp usage` takes ~25 s; awaiting it would hold session start up to the 30 s handler timeout.
    // poll() handles its own failures, and a trip later aborts the turn in progress.
    void poll(ctx);
  });


  pi.on("tool_call", () => {
    if (gateState === "latched") {
      return { block: true, reason: BLOCK_REASON };
    }
  });
}

export default registerCodexQuotaGate;
