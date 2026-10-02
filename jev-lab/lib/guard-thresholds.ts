// Distance of a guard decision's answers to the thresholds in agent/extensions/jev-guard.ts, so the
// profile's confirm set (every non-allow, and every allow within CONFIRM_SET_MARGIN of a threshold)
// is generated mechanically instead of eyeballed.
import {
  BASH_BLOCK_DESTRUCTIVE, BASH_BLOCK_IRREVERSIBLE, BASH_BLOCK_SECRET_EXPOSURE, BASH_CONFIRM_DESTRUCTIVE,
  BASH_CONFIRM_IRREVERSIBLE, RESULT_FLAG_INJECTION, WRITE_BLOCK_SECRET,
} from "../../agent/extensions/jev-guard.ts";
import type { LabAnswer } from "./types";

export const CONFIRM_SET_MARGIN = 0.1;
/** Deterministic rules that record an observation, never a decision. */
export const INFORMATIONAL_RULES = new Set(["outside-workspace"]);

function noul(answers: LabAnswer[], ...ids: string[]): number | undefined {
  return answers.find((answer) => ids.includes(answer.id))?.noul;
}

/** Smallest distance from any answer to the threshold that acts on it; undefined without Jev answers. */
export function thresholdDistance(policy: string, answers: LabAnswer[]): number | undefined {
  const pairs: [number | undefined, number[]][] = [];
  if (policy === "guard.bash") {
    pairs.push([answers.find((answer) => answer.id === "effect")?.probabilities?.irreversible,
      [BASH_BLOCK_IRREVERSIBLE, BASH_CONFIRM_IRREVERSIBLE]]);
    pairs.push([noul(answers, "destructive", "broad_target", "destructive_intent"), [BASH_BLOCK_DESTRUCTIVE, BASH_CONFIRM_DESTRUCTIVE]]);
    pairs.push([noul(answers, "secret_exposure"), [BASH_BLOCK_SECRET_EXPOSURE]]);
  } else if (policy === "guard.write") {
    pairs.push([noul(answers, "contains_secret"), [WRITE_BLOCK_SECRET]]);
  } else if (policy === "guard.result") {
    pairs.push([noul(answers, "prompt_injection"), [RESULT_FLAG_INJECTION]]);
  }
  const distances = pairs.flatMap(([value, thresholds]) => (value === undefined ? [] : thresholds.map((t) => Math.abs(value - t))));
  return distances.length > 0 ? Math.min(...distances) : undefined;
}
