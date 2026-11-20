import { noChainedTypeAssertionsRule } from "./rules/no-chained-type-assertions.ts";
import { noConditionalEmptyObjectSpreadRule } from "./rules/no-conditional-empty-object-spread.ts";
import { noModuleMockingRule } from "./rules/no-module-mocking.ts";
import { noReflectApplyRule } from "./rules/no-reflect-apply.ts";
import { noReflectGetRule } from "./rules/no-reflect-get.ts";
import { noForbiddenTermInSymbolNamesRule } from "./rules/no-shape-in-symbol-names.ts";
import { noWidenThenAssertRule } from "./rules/no-widen-then-assert.ts";

export const escapeHatchRules = {
	"no-chained-type-assertions": noChainedTypeAssertionsRule,
	"no-conditional-empty-object-spread": noConditionalEmptyObjectSpreadRule,
	"no-module-mocking": noModuleMockingRule,
	"no-reflect-apply": noReflectApplyRule,
	"no-reflect-get": noReflectGetRule,
	"no-shape-in-symbol-names": noForbiddenTermInSymbolNamesRule,
	"no-widen-then-assert": noWidenThenAssertRule,
};
