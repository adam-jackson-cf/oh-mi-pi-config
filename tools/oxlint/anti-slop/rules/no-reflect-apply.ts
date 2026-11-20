import { defineRule } from "@oxlint/plugins";

import { isGlobalReflectMethodCall } from "../shared/reflect-method.ts";

/** Ban Reflect.apply, which bypasses ordinary typed function calls. */
export const noReflectApplyRule = defineRule({
  meta: {
    type: "problem",
    docs: {
      description:
        "Disallow Reflect.apply; call typed functions directly or model dynamic dispatch behind an interface.",
    },
    messages: {
      reflectApply:
        "Root cause: `Reflect.apply` calls a function the types cannot see, usually because dispatch has no declared contract. Fix: declare the dispatch as a named interface or lookup table of typed functions and call them directly.",
    },
  },
  createOnce(context) {
    return {
      CallExpression(node) {
        if (node.callee.type === "Super" || node.callee.type === "V8IntrinsicExpression") return;
        if (isGlobalReflectMethodCall(context.sourceCode, node.callee, "apply")) {
          context.report({ node, messageId: "reflectApply" });
        }
      },
    };
  },
});
