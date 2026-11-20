import type { ESTree, SourceCode } from "@oxlint/plugins";

function inferredNames(type: ESTree.TSType, sourceCode: SourceCode): string[] {
	const tokens = sourceCode.getTokens(type);
	return tokens.flatMap((token, index) => {
		const next = tokens[index + 1];
		return token.value === "infer" && next?.type === "Identifier" ? [next.value] : [];
	});
}

function binderNames(
	current: ESTree.Node,
	descendant: ESTree.Node,
	sourceCode: SourceCode,
): string[] {
	const names =
		"typeParameters" in current
			? (current.typeParameters?.params ?? []).map((parameter) => parameter.name.name)
			: [];
	if (
		current.type === "TSMappedType" &&
		(descendant === current.nameType || descendant === current.typeAnnotation)
	) {
		names.push(current.key.name);
	}
	if (current.type === "TSConditionalType" && descendant === current.trueType) {
		names.push(...inferredNames(current.extendsType, sourceCode));
	}
	return names;
}

/** Collect type binders that are in scope at a node and can shadow module aliases. */
export function lexicalTypeParameterNames(
	node: ESTree.Node,
	sourceCode: SourceCode,
): ReadonlySet<string> {
	const names = new Set<string>();
	let descendant: ESTree.Node = node;
	let current: ESTree.Node | null = node;
	while (current !== null && current.type !== "Program") {
		for (const name of binderNames(current, descendant, sourceCode)) names.add(name);
		descendant = current;
		current = current.parent;
	}
	return names;
}
