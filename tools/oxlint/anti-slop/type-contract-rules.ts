import { noKnownValueWideningRule } from "./rules/no-known-value-widening.ts";
import { noObjectParametersRule } from "./rules/no-object-parameters.ts";
import { noRuntimeTypeofRule } from "./rules/no-runtime-typeof.ts";
import { noUnknownParametersRule } from "./rules/no-unknown-parameters.ts";
import { noUnknownReturnsRule } from "./rules/no-unknown-returns.ts";
import { noUnknownTypeAliasesRule } from "./rules/no-unknown-type-aliases.ts";
import { noUnsafeDictionaryTypeRule } from "./rules/no-unsafe-dictionary-type.ts";

export const typeContractRules = {
	"no-known-value-widening": noKnownValueWideningRule,
	"no-object-parameters": noObjectParametersRule,
	"no-runtime-typeof": noRuntimeTypeofRule,
	"no-unknown-parameters": noUnknownParametersRule,
	"no-unknown-returns": noUnknownReturnsRule,
	"no-unknown-type-aliases": noUnknownTypeAliasesRule,
	"no-unsafe-dictionary-type": noUnsafeDictionaryTypeRule,
};
