import type { Task } from "./model";

export function serializeTask(task: Task): string {
  return JSON.stringify({ id: task.id, title: task.title });
}
