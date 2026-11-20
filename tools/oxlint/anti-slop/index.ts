import { eslintCompatPlugin } from "@oxlint/plugins";

import { escapeHatchRules } from "./escape-hatch-rules.ts";
import { typeContractRules } from "./type-contract-rules.ts";

/** Generic Oxlint rules that reject low-evidence and low-signal implementation patterns. */
const antiSlopPlugin = eslintCompatPlugin({
	meta: { name: "anti-slop" },
	rules: { ...escapeHatchRules, ...typeContractRules },
});

export default antiSlopPlugin;
