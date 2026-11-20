import { defineRule } from "@oxlint/plugins";
import { z } from "zod";

import type { ESTree } from "@oxlint/plugins";

type RuntimeFunction = ESTree.ArrowFunctionExpression | ESTree.Function;

const ruleOptions = z.array(z.object({ allowInTypeGuards: z.boolean() }).strict());

function isRuntimeFunction(node: ESTree.Node): node is RuntimeFunction {
	return (
		node.type === "ArrowFunctionExpression" ||
		node.type === "FunctionDeclaration" ||
		node.type === "FunctionExpression"
	);
}

function isInsideTypeGuard(node: ESTree.Node): boolean {
	let current: ESTree.Node | null = node.parent;
	while (current !== null && current.type !== "Program") {
		if (isRuntimeFunction(current)) {
			return current.returnType?.typeAnnotation.type === "TSTypePredicate";
		}
		current = current.parent;
	}
	return false;
}

/** Disallow runtime typeof checks that narrow unparsed values instead of decoding them. */
export const noRuntimeTypeofRule = defineRule({
	meta: {
		type: "problem",
		docs: {
			description:
				"Disallow runtime typeof checks; external values must be decoded into meaningful types at their I/O boundary.",
		},
		messages: {
			runtimeTypeof:
				"Root cause: a `typeof` check inspects the representation because the value reached this code unparsed. Fix: parse input into a domain type at its I/O boundary, then branch on that type's meaningful values.",
		},
		schema: [
			{
				type: "object",
				properties: {
					allowInTypeGuards: { type: "boolean" },
				},
				additionalProperties: false,
			},
		],
		defaultOptions: [{ allowInTypeGuards: false }],
	},
	createOnce(context) {
		let allowInTypeGuards = false;
		return {
			before() {
				allowInTypeGuards = ruleOptions.parse(context.options)[0]?.allowInTypeGuards === true;
			},
			UnaryExpression(node) {
				if (
					node.operator === "typeof" &&
					(!allowInTypeGuards || !isInsideTypeGuard(node))
				) {
					context.report({ node, messageId: "runtimeTypeof" });
				}
			},
		};
	},
});
