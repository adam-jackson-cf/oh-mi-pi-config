// Shared zod boundary for the E4 builders: parses dataset JSONL lines into BenchCase.
import { readFileSync } from "node:fs";
import { z } from "zod";
import type { BenchCase, JsonValue } from "../lib/types";

export const jsonValue: z.ZodType<JsonValue> = z.lazy(() =>
  z.union([z.string(), z.number(), z.boolean(), z.null(), z.array(jsonValue), z.record(z.string(), jsonValue)]));
export const jsonObject = z.record(z.string(), jsonValue);

const instructions = z.union([z.string(), z.object({ question: z.string(), focus: z.string().optional() })]);
const criterion = z.union([
  z.string(), z.object({ what: z.string(), not_for: z.string().optional(), examples: z.array(z.string()).optional() }),
]);
const question = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("noul"), instructions,
    criteria: z.object({ true: criterion.optional(), false: criterion.optional() }).optional(),
  }),
  z.object({ type: z.literal("choice"), instructions: z.string(), criteria: z.record(z.string(), z.string().nullable()) }),
  z.object({ type: z.literal("score"), instructions: z.string(), criteria: z.array(z.string()) }),
]);
export const jevQuestions = z.record(z.string(), question);

export const benchCase = z.object({
  id: z.string(),
  experiment: z.string(),
  dataset: z.string(),
  state: jsonValue,
  questions: jevQuestions,
  truth: z.record(z.string(), z.union([z.boolean(), z.string(), z.number()])),
  primary: z.string(),
  positive: z.string().optional(),
  meta: z.record(z.string(), jsonValue).optional(),
  act: z.object({
    truth: z.boolean(), risk: z.array(z.string()), mode: z.enum(["max", "product", "sum"]).optional(),
  }).optional(),
});

/** Reads a `.jsonl` file of BenchCase lines, validating each against the schema. */
export function readCases(file: string): BenchCase[] {
  return readFileSync(file, "utf8").split("\n").filter(Boolean).map(l => benchCase.parse(JSON.parse(l)));
}

/** Parses serialised JSON text (e.g. `JSON.stringify(state)`) into a JsonValue. */
export const parseJsonValue = (text: string): JsonValue => jsonValue.parse(JSON.parse(text));
