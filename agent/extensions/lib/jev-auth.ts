/**
 * OpenRouter credential lookup for the Jev policy extensions. Kept apart from
 * `jev.ts` so the standalone Jev lab workbench can import the client without
 * loading the OMP runtime package.
 */
import { discoverAuthStorage } from "@oh-my-pi/pi-coding-agent";

/** The same OpenRouter credential `/login openrouter` stores; undefined when absent. */
export async function loadJevApiKey(): Promise<string | undefined> {
  const auth = await discoverAuthStorage();
  try {
    return (await auth.getApiKey("openrouter")) ?? undefined;
  } finally {
    auth.close();
  }
}
