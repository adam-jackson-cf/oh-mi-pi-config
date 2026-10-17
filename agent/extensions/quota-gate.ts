import { createHash } from "node:crypto";
import { z, type ExtensionAPI, type ExtensionContext } from "@oh-my-pi/pi-coding-agent";

const USAGE_POLL_INTERVAL_MS = 5 * 60 * 1000;
const USAGE_COMMAND_TIMEOUT_MS = 30 * 1000;
const DEFAULT_THRESHOLD_FRACTION = 0.85;
const QUOTA_GATE_COMMAND = "quota-gate";
const BLOCK_REASON =
  "Quota gate is paused. Use /quota-gate continue to allow more work, or /quota-gate off to disable it.";

/**
 * SHA-256 of the trimmed, lower-cased email of the account whose subscription quotas are gated, on every
 * provider. Other accounts on the same provider (for example a team seat) are ignored. Kept as a hash so the
 * address is not published with this repository; compute it with `emailHash`.
 */
const GATED_ACCOUNT_EMAIL_SHA256 = "325ae643add8e6313280c1ef2789c6bd6370623d6ce3b47249cdc23dc6433f0f";

export type GatedProvider = "openai-codex" | "anthropic";
const GATED_PROVIDERS: GatedProvider[] = ["openai-codex", "anthropic"];
const PROVIDER_NAMES = { "openai-codex": "Codex", anthropic: "Claude" } satisfies Record<GatedProvider, string>;

type GateState = "disabled" | "open" | "latched";

export type QuotaSample = {
  usedFraction: number;
  resetsAt: number;
};

/** Latest seven-day sample per provider; a provider is absent while its gated account has no usable report. */
type ProviderSamples = Partial<Record<GatedProvider, QuotaSample>>;

const UsagePayloadSchema = z.object({
  reports: z.array(
    z.object({
      provider: z.string(),
      limits: z.array(
        z.object({
          id: z.string(),
          scope: z.object({ windowId: z.string().optional(), tier: z.string().optional() }),
          amount: z.object({ usedFraction: z.number().optional() }),
          window: z.object({ resetsAt: z.number().optional() }).optional(),
        }),
      ),
      metadata: z.object({ email: z.string().optional() }).optional(),
    }),
  ),
});

export type UsagePayload = z.infer<typeof UsagePayloadSchema>;

/** Returns the parsed, unredacted `omp usage` payload; rejects when usage is unavailable or malformed. */
export type UsageReader = (ctx: ExtensionContext) => Promise<UsagePayload>;

export function emailHash(email: string): string {
  return createHash("sha256").update(email.trim().toLowerCase()).digest("hex");
}

export function parseThreshold(value: string): number | undefined {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return undefined;
  }

  const fraction = parsed > 1 ? parsed / 100 : parsed;
  return fraction > 0 && fraction <= 1 ? fraction : undefined;
}

/**
 * The gated account's overall seven-day quota on `provider`. Model-tier sublimits (Codex Spark, Claude
 * tiers) are skipped. Undefined unless exactly one report for the provider belongs to the gated account.
 */
export function selectSevenDayQuotaSample(
  payload: UsagePayload,
  provider: GatedProvider,
  accountHash: string = GATED_ACCOUNT_EMAIL_SHA256,
): QuotaSample | undefined {
  const samples = payload.reports.flatMap(report => {
    const email = report.metadata?.email;
    if (report.provider !== provider || email === undefined || emailHash(email) !== accountHash) {
      return [];
    }

    const limit = report.limits.find(
      candidate =>
        candidate.scope.windowId === "7d" &&
        candidate.scope.tier === undefined &&
        !candidate.id.includes(":spark:") &&
        candidate.amount.usedFraction !== undefined &&
        candidate.window?.resetsAt !== undefined,
    );
    if (!limit || limit.amount.usedFraction === undefined || limit.window?.resetsAt === undefined) {
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

/** Unredacted, because accounts are matched by email; the extension never displays or stores the address. */
async function readUsageFromOmp(pi: ExtensionAPI, ctx: ExtensionContext): Promise<UsagePayload> {
  const result = await pi.exec("omp", ["usage", "--json"], { cwd: ctx.cwd, timeout: USAGE_COMMAND_TIMEOUT_MS });
  if (result.code !== 0 || result.killed) {
    throw new Error("OMP usage command failed");
  }

  return UsagePayloadSchema.parse(JSON.parse(result.stdout));
}

export function registerQuotaGate(
  pi: ExtensionAPI,
  readUsage: UsageReader = ctx => readUsageFromOmp(pi, ctx),
  accountHash: string = GATED_ACCOUNT_EMAIL_SHA256,
): void {
  let gateState: GateState = "disabled";
  let thresholdFraction = DEFAULT_THRESHOLD_FRACTION;
  let lastSamples: ProviderSamples = {};
  let checking = false;
  let failureNotified = false;
  let pollTimer: Timer | undefined;

  const status = (): string => {
    if (gateState === "disabled") {
      return "Quota gate is disabled.";
    }

    const usage = GATED_PROVIDERS.map(provider => {
      const sample = lastSamples[provider];
      return sample
        ? `${PROVIDER_NAMES[provider]} ${formatPercent(sample.usedFraction)} used, reset in ${formatRemaining(sample.resetsAt)}`
        : `${PROVIDER_NAMES[provider]} no seven-day sample yet`;
    }).join("; ");
    const state = gateState === "latched" ? "paused" : "active";
    return `Quota gate is ${state} at threshold ${formatPercent(thresholdFraction)}: ${usage}.`;
  };

  const setOpen = (ctx: ExtensionContext, threshold: number): void => {
    thresholdFraction = threshold;
    gateState = "open";
    failureNotified = false;
    pollTimer ??= ctx.setInterval(() => poll(ctx), USAGE_POLL_INTERVAL_MS);
  };

  const disable = (ctx: ExtensionContext): void => {
    gateState = "disabled";
    if (pollTimer !== undefined) {
      ctx.clearTimer(pollTimer);
      pollTimer = undefined;
    }
  };

  const tripGate = async (ctx: ExtensionContext, provider: GatedProvider, sample: QuotaSample): Promise<void> => {
    if (gateState !== "open") {
      return;
    }

    gateState = "latched";
    const name = PROVIDER_NAMES[provider];
    const message = `${name} seven-day quota reached ${formatPercent(sample.usedFraction)} (threshold ${formatPercent(thresholdFraction)}; reset in ${formatRemaining(sample.resetsAt)}).`;
    const resumeCurrentTurn = !ctx.isIdle();
    ctx.abort();
    ctx.ui.notify(`${message} Work is paused.`, "warning");

    if (!ctx.hasUI) {
      return;
    }

    const continueWork = await ctx.ui.confirm("Quota gate", `${message} Continue work?`);
    if (!continueWork) {
      return;
    }

    gateState = "open";
    ctx.ui.notify("Quota gate reopened until the next usage check.", "warning");
    if (resumeCurrentTurn) {
      pi.sendUserMessage(
        `[quota gate] The user approved continuing despite the ${name} seven-day quota threshold. Resume the interrupted work.`,
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
      const payload = await readUsage(ctx);
      const readings = GATED_PROVIDERS.map(provider => ({
        provider,
        sample: selectSevenDayQuotaSample(payload, provider, accountHash),
      }));
      lastSamples = {};
      for (const { provider, sample } of readings) {
        if (sample) {
          lastSamples[provider] = sample;
        }
      }

      const missing = readings.filter(reading => !reading.sample).map(reading => PROVIDER_NAMES[reading.provider]);
      if (missing.length === 0) {
        failureNotified = false;
      } else if (!failureNotified) {
        ctx.ui.notify(
          `${missing.join(" and ")} quota usage is unavailable or ambiguous; the quota gate does not cover it until it is.`,
          "warning",
        );
        failureNotified = true;
      }

      const tripped = readings.find(reading => reading.sample !== undefined && reading.sample.usedFraction >= thresholdFraction);
      if (tripped?.sample) {
        await tripGate(ctx, tripped.provider, tripped.sample);
      }
    } catch {
      if (!failureNotified) {
        ctx.ui.notify("Quota usage is unavailable or ambiguous; the quota gate remains open.", "warning");
        failureNotified = true;
      }
    } finally {
      checking = false;
    }
  };

  pi.registerCommand(QUOTA_GATE_COMMAND, {
    description: "Control the seven-day Codex and Claude quota gate: on [threshold], off, continue, status",
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

        setOpen(ctx, threshold);
        await poll(ctx);
        ctx.ui.notify(status(), "info");
        return;
      }

      if (action === "off") {
        disable(ctx);
        ctx.ui.notify("Quota gate disabled.", "info");
        return;
      }

      if (action === "continue") {
        if (gateState !== "latched") {
          ctx.ui.notify("Quota gate is not paused.", "info");
          return;
        }

        gateState = "open";
        ctx.ui.notify("Quota gate reopened until the next usage check.", "warning");
        return;
      }

      ctx.ui.notify("Usage: /quota-gate [on <fraction-or-percent>|off|continue|status]", "error");
    },
  });

  // The gate is opt-in per session via `/quota-gate on`; a new session starts disabled and never polls.
  // OMP clears extension timers on session_shutdown, so only the handle needs forgetting here.
  pi.on("session_start", () => {
    gateState = "disabled";
    pollTimer = undefined;
    lastSamples = {};
  });

  pi.on("tool_call", () => {
    if (gateState === "latched") {
      return { block: true, reason: BLOCK_REASON };
    }
  });
}

export default registerQuotaGate;
