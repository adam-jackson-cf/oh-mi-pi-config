/**
 * OpenRouter credential lookup for the Jev policy extensions. Kept apart from
 * `jev.ts` so the standalone Jev lab workbench can import the client without
 * loading the OMP runtime package.
 */
import { discoverAuthStorage } from "@oh-my-pi/pi-coding-agent";
import { vaultCredential, type JevCredential } from "./jev.ts";

/**
 * Agent Vault when `agent-vault-launch` started this process (no key on this machine), else the key
 * `/login openrouter` stores; undefined when neither is available.
 */
export async function loadJevCredential(): Promise<JevCredential | undefined> {
  const vault = vaultCredential();
  if (vault) return vault;
  const auth = await discoverAuthStorage();
  try {
    const key = await auth.getApiKey("openrouter");
    return key ? { kind: "key", key } : undefined;
  } finally {
    auth.close();
  }
}
