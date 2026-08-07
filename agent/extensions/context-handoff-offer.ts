import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";

const HANDOFF_THRESHOLD_PERCENT = 40;
const OFFER_ENTRY_TYPE = "context-handoff-offered";
const HANDOFF_SUMMARY_REQUEST_TYPE = "context-handoff-summary-request";
const HANDOFF_SUMMARY_REQUEST = `Summarize the handoff context for the user.
Output only a concise high-level bullet list.
Cover the objective, completed work, material decisions or constraints, remaining work, and the immediate next action.
Omit categories that do not apply. Use 3-7 bullets.`;

export default function contextHandoffOffer(pi: ExtensionAPI): void {
  let offered = false;

  const restoreOfferState = (_event: unknown, ctx: ExtensionContext): void => {
    offered = ctx.sessionManager
      .getEntries()
      .some((entry) => entry.type === "custom" && entry.customType === OFFER_ENTRY_TYPE);
  };

  pi.on("session_start", restoreOfferState);
  pi.on("session_switch", restoreOfferState);

  pi.on("session_switch", (event) => {
    if (event.reason !== "handoff") return;

    pi.sendMessage(
      {
        customType: HANDOFF_SUMMARY_REQUEST_TYPE,
        content: HANDOFF_SUMMARY_REQUEST,
        display: false,
        attribution: "agent",
      },
      { deliverAs: "nextTurn", triggerTurn: true },
    );
  });

  pi.on("session_stop", (_event, ctx) => {
    if (offered || !ctx.hasUI || ctx.hasPendingMessages()) return;

    const usage = ctx.getContextUsage();
    if (!usage || usage.percent < HANDOFF_THRESHOLD_PERCENT) return;

    offered = true;
    pi.appendEntry(OFFER_ENTRY_TYPE, {
      thresholdPercent: HANDOFF_THRESHOLD_PERCENT,
      offeredAtPercent: usage.percent,
    });
    ctx.ui.notify(
      `Context usage reached ${usage.percent.toFixed(1)}%. Run /handoff for a clean session, or continue normally to stay in this session.`,
      "warning",
    );
  });
}
