/**
 * OpenRouter credential lookup for the Jev policy extensions. Kept apart from
 * `jev.ts` so the standalone Jev lab workbench can import the client without
 * loading the OMP runtime package.
 */
import { discoverAuthStorage } from "@oh-my-pi/pi-coding-agent";
import type { JevCredential } from "./jev.ts";

/** The OpenRouter key `/login openrouter` stores (or `OPENROUTER_API_KEY`); undefined when there is none. */
export async function loadJevCredential(): Promise<JevCredential | undefined> {
  const auth = await discoverAuthStorage();
  try {
    const key = await auth.getApiKey("openrouter");
    return key ? { kind: "key", key } : undefined;
  } finally {
    auth.close();
  }
}
