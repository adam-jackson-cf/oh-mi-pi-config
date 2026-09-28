import { ConfigError } from "./errors.ts";

export interface Config {
  timeoutMs: number;
  batchSize: number;
  maxAttempts: number;
  baseDelayMs: number;
  maxDelayMs: number;
}

export const DEFAULTS: Config = {
  timeoutMs: 5_000,
  batchSize: 10,
  maxAttempts: 5,
  baseDelayMs: 1_000,
  maxDelayMs: 60_000,
};

/** Unset or empty means "use the default"; anything else must be a positive integer. */
function positiveIntEnv(env: Record<string, string | undefined>, name: string, fallback: number): number {
  const raw = env[name];
  if (raw === undefined || raw === "") return fallback;
  if (!/^[0-9]+$/.test(raw) || Number(raw) <= 0) {
    throw new ConfigError(name, `${name} must be a positive integer, got ${JSON.stringify(raw)}`);
  }
  return Number(raw);
}

export function loadConfig(env: Record<string, string | undefined>): Config {
  return {
    timeoutMs: positiveIntEnv(env, "WEBHOOK_TIMEOUT_MS", DEFAULTS.timeoutMs),
    batchSize: positiveIntEnv(env, "WEBHOOK_BATCH_SIZE", DEFAULTS.batchSize),
    maxAttempts: positiveIntEnv(env, "WEBHOOK_MAX_ATTEMPTS", DEFAULTS.maxAttempts),
    baseDelayMs: positiveIntEnv(env, "WEBHOOK_BASE_DELAY_MS", DEFAULTS.baseDelayMs),
    maxDelayMs: positiveIntEnv(env, "WEBHOOK_MAX_DELAY_MS", DEFAULTS.maxDelayMs),
  };
}
