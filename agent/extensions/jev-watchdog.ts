import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
  type SimpleStreamOptions,
} from "@oh-my-pi/pi-ai";
import { discoverAuthStorage, type ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { z } from "zod";

const MODEL = "~typesafe/jev-latest";
const API = "jev-decisions";
const ENDPOINT = "https://openrouter.ai/api/alpha/decisions";
// Experimental operating point, not a calibrated accuracy claim.
const THRESHOLD = 0.9;
const probability = z.number().min(0).max(1);
const choiceAnswer = z.object({
  type: z.literal("choice"),
  choice: z.string(),
  probabilities: z.record(z.string(), probability),
  confidence: probability,
});
const responseSchema = z.object({
  id: z.string(),
  model: z.string(),
  provider: z.string(),
  answers: z.object({
    drift: choiceAnswer,
  }),
  usage: z.object({
    input_tokens: z.number().nonnegative(),
    output_tokens: z.number().nonnegative(),
    cost: z.number().nonnegative(),
  }),
});

function textOf(content: Context["messages"][number]["content"]): string {
  if (!Array.isArray(content)) return content;
  return content.flatMap((part) => part.type === "text" ? [part.text] : []).join("\n");
}

/** A decisions-only transport for the native advisor, not a general chat model. */
export function streamJev(model: Model, context: Context, options?: SimpleStreamOptions) {
  const stream = createAssistantMessageEventStream();
  const message: AssistantMessage = {
    role: "assistant", api: API, provider: model.provider, model: model.id,
    content: [], stopReason: "stop", timestamp: Date.now(),
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
  };
  stream.push({ type: "start", partial: message });
  void (async () => {
    try {
      if (!context.tools?.some((tool) => tool.name === "advise")) {
        throw new Error("Jev watchdog is advisor-only: the native advise tool is required.");
      }
      // The native agent requests another turn after executing advise. Finish it
      // locally rather than charging for another classification of the same update.
      const last = context.messages.at(-1);
      if (last?.role === "toolResult" && last.toolName === "advise") {
        stream.push({ type: "done", reason: "stop", message });
        return;
      }
      if (!options?.apiKey) throw new Error("Jev watchdog requires OpenRouter login.");
      // One native update can contain multiple consecutive user-message chunks.
      let updateStart = context.messages.length;
      while (updateStart > 0 && context.messages[updateStart - 1].role === "user") updateStart--;
      const earlier = context.messages.slice(0, updateStart).filter((item) => item.role === "user").map((item) => textOf(item.content));
      const current = context.messages.slice(updateStart).map((item) => textOf(item.content)).join("\n\n");
      if (!current) throw new Error("Jev watchdog received no primary transcript update.");
      const questions = {
        drift: {
          type: "choice",
          instructions: "Apply the review policy supplied in project_context to current_update, using earlier_transcript as context. Does the update meet that policy's positive classification criteria?",
          criteria: {
            yes: "The supplied review policy's positive criteria are met.",
            no: "The supplied review policy's positive criteria are not met.",
            unknown: "Insufficient evidence to apply the supplied review policy.",
          },
        },
      };
      const response = await fetch(ENDPOINT, {
        method: "POST",
        headers: { Authorization: `Bearer ${options.apiKey}`, "Content-Type": "application/json" },
        signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(20_000)]) : AbortSignal.timeout(20_000),
        body: JSON.stringify({ model: MODEL, state: {
          project_context: context.systemPrompt,
          earlier_transcript: earlier, current_update: current,
        }, questions }),
      });
      if (!response.ok) {
        // Do not log provider response bodies: credentials or input can be echoed.
        message.errorStatus = response.status;
        if (response.status === 402) {
          throw new Error("Jev watchdog: OpenRouter is out of credits or has reached a spending limit (HTTP 402). Review was not performed. Check https://openrouter.ai/settings/credits and the API key budget; then use /advisor off followed by /advisor on to resume.");
        }
        throw new Error(`Jev Decisions API returned HTTP ${response.status}; review was not performed.`);
      }
      const result = responseSchema.parse(await response.json());
      const answer = result.answers.drift;
      const allowed = questions.drift.criteria;
      if (!Object.hasOwn(allowed, answer.choice) || Object.keys(allowed).some((key) => answer.probabilities[key] === undefined)) {
        throw new Error("Jev returned invalid drift choices; review was not performed.");
      }
      message.responseId = result.id;
      message.upstreamProvider = result.provider;
      message.duration = Date.now() - message.timestamp;
      message.usage = {
        input: result.usage.input_tokens, output: result.usage.output_tokens,
        cacheRead: 0, cacheWrite: 0, totalTokens: result.usage.input_tokens + result.usage.output_tokens,
        cost: { input: result.usage.cost, output: 0, cacheRead: 0, cacheWrite: 0, total: result.usage.cost },
      };
      const blocker = result.answers.drift.probabilities.yes >= THRESHOLD;
      const verdict = blocker ? "blocker" : "continue";
      const report = verdict;
      message.content.push({ type: "text", text: report });
      stream.push({ type: "text_start", contentIndex: 0, partial: message });
      stream.push({ type: "text_delta", contentIndex: 0, delta: report, partial: message });
      stream.push({ type: "text_end", contentIndex: 0, content: report, partial: message });
      if (blocker) {
        const note = "Blocker found in current task. Stop and initiate KISS agent review.";
        const call = { type: "toolCall" as const, id: `jev_${crypto.randomUUID()}`, name: "advise",
          arguments: { note, severity: verdict } };
        message.content.push(call);
        message.stopReason = "toolUse";
        stream.push({ type: "toolcall_start", contentIndex: 1, partial: message });
        stream.push({ type: "toolcall_delta", contentIndex: 1, delta: JSON.stringify(call.arguments), partial: message });
        stream.push({ type: "toolcall_end", contentIndex: 1, toolCall: call, partial: message });
      }
      stream.push({ type: "done", reason: blocker ? "toolUse" : "stop", message });
    } catch (error) {
      message.stopReason = options?.signal?.aborted ? "aborted" : "error";
      message.errorMessage = error instanceof z.ZodError ? "Jev returned an invalid decision response; review was not performed." :
        error instanceof Error ? error.message : "Jev watchdog request failed.";
      stream.push({ type: "error", reason: message.stopReason, error: message });
    }
  })();
  return stream;
}

export default async function jevWatchdog(pi: ExtensionAPI) {
  // Reuse native OpenRouter auth; no new credential file or secret in YAML.
  const auth = await discoverAuthStorage();
  let apiKey: string | undefined;
  try { apiKey = await auth.getApiKey("openrouter"); } finally { auth.close(); }
  if (!apiKey) throw new Error("Jev watchdog: run /login openrouter before enabling this extension.");
  pi.registerProvider("jev-watchdog", {
    baseUrl: "https://openrouter.ai/api/alpha", apiKey, api: API, streamSimple: streamJev,
    models: [{ id: MODEL, name: "Jev watchdog (advisor only)", reasoning: false, input: ["text"],
      contextWindow: 32_000, maxTokens: 2_000,
      cost: { input: 0.042, output: 0, cacheRead: 0, cacheWrite: 0 } }],
  });
}
