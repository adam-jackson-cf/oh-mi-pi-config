import type { Task, TaskInput } from "./model";
import { validateTaskPayload } from "./validate";

export function createTask(input: TaskInput): Task {
  if (!validateTaskPayload(input)) {
    throw new TypeError("invalid task payload");
  }
  return { id: input.id, title: input.title };
}
