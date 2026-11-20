import type { ESTree } from "@oxlint/plugins";

const BUILT_INS = new Set([
	"Record",
	"Readonly",
	"Partial",
	"Required",
	"Pick",
	"Omit",
	"PropertyKey",
	"NonNullable",
]);
const TRANSPARENT_WRAPPERS = new Set(["Readonly", "Partial", "Required", "NonNullable"]);
const KNOWN_EVIDENCE_TYPES = new Set([
	"ArrayExpression",
	"ArrowFunctionExpression",
	"ClassExpression",
	"FunctionExpression",
	"Literal",
	"NewExpression",
	"ObjectExpression",
	"TemplateLiteral",
	"UnaryExpression",
]);

type TypeAliasEnvironment = ReadonlyMap<string, ESTree.TSType>;

type UnsafeValue = "any" | "empty-object" | "object" | "union" | "unknown";

type ResolvedType = {
	readonly type: ESTree.TSType;
	readonly substitutions: TypeAliasEnvironment;
};

type Lookup = {
	readonly environment: TypeEnvironment;
	readonly substitutions: TypeAliasEnvironment;
	readonly resolvingAliases: ReadonlySet<string>;
};

type Expansion = {
	readonly type: ESTree.TSType;
	readonly lookup: Lookup;
};

type EnvironmentBuilder = {
	readonly aliases: Map<string, ESTree.TSTypeAliasDeclaration>;
	readonly interfaces: Map<string, readonly ESTree.TSInterfaceDeclaration[]>;
	readonly shadowedBuiltIns: Set<string>;
};

export type UnsafeDictionary = {
	readonly kind: "unsafe-dictionary";
	readonly unsafeValue: UnsafeValue;
};

export type WideningTargetKind =
	| "anonymous object"
	| "generic container"
	| "object"
	| "open dictionary"
	| "unknown";

export type WideningTarget = {
	readonly kind: WideningTargetKind;
};

export type TypeEnvironment = {
	readonly aliases: ReadonlyMap<string, ESTree.TSTypeAliasDeclaration>;
	readonly interfaces: ReadonlyMap<string, readonly ESTree.TSInterfaceDeclaration[]>;
	readonly shadowedBuiltIns: ReadonlySet<string>;
};

const UNSAFE_KEYWORDS = new Map<string, UnsafeValue>([
	["TSAnyKeyword", "any"],
	["TSObjectKeyword", "object"],
	["TSUnknownKeyword", "unknown"],
]);
const OPEN_DICTIONARY: WideningTarget = { kind: "open dictionary" };
const WIDENING_KEYWORDS = new Map<string, WideningTarget>([
	["TSObjectKeyword", { kind: "object" }],
	["TSUnknownKeyword", { kind: "unknown" }],
]);

function declaredStatement(statement: ESTree.Statement): ESTree.Node | null {
	return statement.type === "ExportNamedDeclaration" ||
		statement.type === "ExportDefaultDeclaration"
		? (statement.declaration ?? null)
		: statement;
}

function boundNames(declaration: ESTree.Node | null): string[] {
	if (declaration === null) return [];
	if (declaration.type === "ImportDeclaration") {
		return declaration.specifiers.map((specifier) => specifier.local.name);
	}
	if (declaration.type === "ClassDeclaration" || declaration.type === "FunctionDeclaration") {
		return declaration.id === null ? [] : [declaration.id.name];
	}
	return declaration.type === "TSEnumDeclaration" ||
		declaration.type === "TSInterfaceDeclaration" ||
		declaration.type === "TSTypeAliasDeclaration"
		? [declaration.id.name]
		: [];
}

function recordTypeDeclaration(declaration: ESTree.Node | null, builder: EnvironmentBuilder): void {
	if (declaration?.type === "TSTypeAliasDeclaration") {
		const name = declaration.id.name;
		if (builder.aliases.has(name)) builder.shadowedBuiltIns.add(name);
		else builder.aliases.set(name, declaration);
	}
	if (declaration?.type === "TSInterfaceDeclaration") {
		const name = declaration.id.name;
		builder.interfaces.set(name, [...(builder.interfaces.get(name) ?? []), declaration]);
	}
}

export function createTypeEnvironment(program: ESTree.Program): TypeEnvironment {
	const builder: EnvironmentBuilder = {
		aliases: new Map(),
		interfaces: new Map(),
		shadowedBuiltIns: new Set(),
	};
	for (const statement of program.body) {
		const declaration = declaredStatement(statement);
		recordTypeDeclaration(declaration, builder);
		for (const name of boundNames(declaration)) {
			if (BUILT_INS.has(name)) builder.shadowedBuiltIns.add(name);
		}
	}
	return builder;
}

function typeReferenceName(type: ESTree.TSTypeReference): string | null {
	return type.typeName.type === "Identifier" ? type.typeName.name : null;
}

function isBuiltIn(name: string, environment: TypeEnvironment): boolean {
	return BUILT_INS.has(name) && !environment.shadowedBuiltIns.has(name);
}

function isTransparentWrapper(name: string, environment: TypeEnvironment): boolean {
	return TRANSPARENT_WRAPPERS.has(name) && isBuiltIn(name, environment);
}

function typeArgument(type: ESTree.TSTypeReference, index: number): ESTree.TSType | null {
	return type.typeArguments?.params[index] ?? null;
}

function isUnappliedReferenceTo(type: ESTree.TSType, name: string): boolean {
	const unwrapped = unwrapTransparentType(type);
	return (
		unwrapped.type === "TSTypeReference" &&
		typeReferenceName(unwrapped) === name &&
		(unwrapped.typeArguments?.params.length ?? 0) === 0
	);
}

function unwrapTransparentType(type: ESTree.TSType): ESTree.TSType {
	let current = type;
	while (
		current.type === "TSParenthesizedType" ||
		(current.type === "TSTypeOperator" && current.operator === "readonly")
	) {
		current = current.typeAnnotation;
	}
	return current;
}

function isNeverType(type: ESTree.TSType): boolean {
	return unwrapTransparentType(type).type === "TSNeverKeyword";
}

function isEffectivelyEmptyMember(member: ESTree.TSSignature): boolean {
	return (
		member.type === "TSPropertySignature" &&
		member.optional === true &&
		member.typeAnnotation !== null &&
		member.typeAnnotation !== undefined &&
		isNeverType(member.typeAnnotation.typeAnnotation)
	);
}

function isEffectivelyEmptyTypeLiteral(type: ESTree.TSTypeLiteral): boolean {
	return type.members.length === 0 || type.members.every(isEffectivelyEmptyMember);
}

function isEffectivelyEmptyInterface(
	declarations: readonly ESTree.TSInterfaceDeclaration[],
): boolean {
	if (declarations.length !== 1) return false;
	const [type] = declarations;
	return (
		type !== undefined &&
		type.extends.length === 0 &&
		(type.body.body.length === 0 || type.body.body.every(isEffectivelyEmptyMember))
	);
}

function resolvedSubstitutionArgument(
	type: ESTree.TSType,
	base: TypeAliasEnvironment,
	resolving: ReadonlySet<string> = new Set(),
): ESTree.TSType {
	const unwrapped = unwrapTransparentType(type);
	if (unwrapped.type !== "TSTypeReference") return type;
	const name = typeReferenceName(unwrapped);
	if (name === null || resolving.has(name)) return type;
	const substitution = base.get(name);
	if (substitution === undefined) return type;
	return resolvedSubstitutionArgument(substitution, base, new Set([...resolving, name]));
}

function aliasSubstitution(
	alias: ESTree.TSTypeAliasDeclaration,
	type: ESTree.TSTypeReference,
	base: TypeAliasEnvironment,
): TypeAliasEnvironment | null {
	const parameters = alias.typeParameters?.params ?? [];
	const argumentTypes = type.typeArguments?.params ?? [];
	const next = new Map(base);
	for (const [index, parameter] of parameters.entries()) {
		const argument = argumentTypes[index] ?? parameter.default;
		if (argument === null || argument === undefined) return null;
		next.set(parameter.name.name, resolvedSubstitutionArgument(argument, next));
	}
	return next;
}

function expandAlias(name: string, type: ESTree.TSTypeReference, lookup: Lookup): Expansion | null {
	const alias = lookup.environment.aliases.get(name);
	if (alias === undefined || lookup.resolvingAliases.has(name)) return null;
	const substitutions = aliasSubstitution(alias, type, lookup.substitutions);
	if (substitutions === null) return null;
	return {
		type: alias.typeAnnotation,
		lookup: {
			environment: lookup.environment,
			substitutions,
			resolvingAliases: new Set([...lookup.resolvingAliases, name]),
		},
	};
}

function unsafeIntersection(type: ESTree.TSIntersectionType, lookup: Lookup): UnsafeValue | null {
	const members = type.types.map((member) => unsafeDirectValue(member, lookup));
	if (members.includes("any")) return "any";
	const [first] = members;
	return first !== undefined && members.every((member) => member !== null) ? first : null;
}

function unsafeReference(type: ESTree.TSTypeReference, lookup: Lookup): UnsafeValue | null {
	const name = typeReferenceName(type);
	if (name === null) return null;
	if (isTransparentWrapper(name, lookup.environment)) {
		const wrapped = typeArgument(type, 0);
		return wrapped === null ? null : unsafeDirectValue(wrapped, lookup);
	}
	const substitution = lookup.substitutions.get(name);
	if (substitution !== undefined) {
		return isUnappliedReferenceTo(substitution, name) ? null : unsafeDirectValue(substitution, lookup);
	}
	const interfaceDeclarations = lookup.environment.interfaces.get(name);
	if (interfaceDeclarations !== undefined) {
		return isEffectivelyEmptyInterface(interfaceDeclarations) ? "empty-object" : null;
	}
	const expansion = expandAlias(name, type, lookup);
	return expansion === null ? null : unsafeDirectValue(expansion.type, expansion.lookup);
}

function unsafeDirectValue(type: ESTree.TSType, lookup: Lookup): UnsafeValue | null {
	const unwrapped = unwrapTransparentType(type);
	const keyword = UNSAFE_KEYWORDS.get(unwrapped.type);
	if (keyword !== undefined) return keyword;
	if (unwrapped.type === "TSTypeLiteral") {
		return isEffectivelyEmptyTypeLiteral(unwrapped) ? "empty-object" : null;
	}
	if (unwrapped.type === "TSUnionType") {
		return unwrapped.types.some((member) => unsafeDirectValue(member, lookup) !== null)
			? "union"
			: null;
	}
	if (unwrapped.type === "TSIntersectionType") return unsafeIntersection(unwrapped, lookup);
	return unwrapped.type === "TSTypeReference" ? unsafeReference(unwrapped, lookup) : null;
}

function indexSignatureValues(
	type: ESTree.TSTypeLiteral,
	substitutions: TypeAliasEnvironment,
): readonly ResolvedType[] {
	return type.members.flatMap((member): readonly ResolvedType[] =>
		member.type === "TSIndexSignature" && member.typeAnnotation !== null
			? [{ type: member.typeAnnotation.typeAnnotation, substitutions }]
			: [],
	);
}

function builtInValueTypes(
	type: ESTree.TSTypeReference,
	name: string,
	lookup: Lookup,
): readonly ResolvedType[] | null {
	if (!isBuiltIn(name, lookup.environment)) return null;
	if (TRANSPARENT_WRAPPERS.has(name) || name === "Pick" || name === "Omit") {
		const source = typeArgument(type, 0);
		return source === null ? [] : dictionaryValueTypes(source, lookup);
	}
	if (name !== "Record") return null;
	const value = typeArgument(type, 1);
	return value === null ? [] : [{ type: value, substitutions: lookup.substitutions }];
}

function referenceValueTypes(
	type: ESTree.TSTypeReference,
	lookup: Lookup,
): readonly ResolvedType[] {
	const name = typeReferenceName(type);
	if (name === null) return [];
	const substitution = lookup.substitutions.get(name);
	if (substitution !== undefined) {
		return isUnappliedReferenceTo(substitution, name)
			? []
			: dictionaryValueTypes(substitution, lookup);
	}
	const builtIn = builtInValueTypes(type, name, lookup);
	if (builtIn !== null) return builtIn;
	const expansion = expandAlias(name, type, lookup);
	return expansion === null ? [] : dictionaryValueTypes(expansion.type, expansion.lookup);
}

function dictionaryValueTypes(type: ESTree.TSType, lookup: Lookup): readonly ResolvedType[] {
	const unwrapped = unwrapTransparentType(type);
	if (unwrapped.type === "TSTypeLiteral") {
		return indexSignatureValues(unwrapped, lookup.substitutions);
	}
	if (unwrapped.type === "TSMappedType") {
		return unwrapped.typeAnnotation === null
			? []
			: [{ type: unwrapped.typeAnnotation, substitutions: lookup.substitutions }];
	}
	return unwrapped.type === "TSTypeReference" ? referenceValueTypes(unwrapped, lookup) : [];
}

function rootLookup(environment: TypeEnvironment): Lookup {
	return { environment, substitutions: new Map(), resolvingAliases: new Set() };
}

export function classifyUnsafeDictionaryValue(
	valueType: ESTree.TSType,
	environment: TypeEnvironment,
): UnsafeDictionary | null {
	const unsafeValue = unsafeDirectValue(valueType, rootLookup(environment));
	return unsafeValue === null ? null : { kind: "unsafe-dictionary", unsafeValue };
}

export function classifyUnsafeDictionary(
	type: ESTree.TSType,
	environment: TypeEnvironment,
): UnsafeDictionary | null {
	for (const valueType of dictionaryValueTypes(type, rootLookup(environment))) {
		const unsafeValue = unsafeDirectValue(valueType.type, {
			environment,
			substitutions: valueType.substitutions,
			resolvingAliases: new Set(),
		});
		if (unsafeValue !== null) return { kind: "unsafe-dictionary", unsafeValue };
	}
	return null;
}

function literalWideningTarget(type: ESTree.TSTypeLiteral): WideningTarget | null {
	if (type.members.some((member) => member.type === "TSIndexSignature")) return OPEN_DICTIONARY;
	return type.members.length > 0 ? { kind: "anonymous object" } : null;
}

function aliasWideningTarget(
	alias: ESTree.TSTypeAliasDeclaration,
	type: ESTree.TSTypeReference,
	environment: TypeEnvironment,
): WideningTarget | null {
	const substitutions = aliasSubstitution(alias, type, new Map());
	if (substitutions === null) return null;
	const lookup = { environment, substitutions, resolvingAliases: new Set([alias.id.name]) };
	if ((alias.typeParameters?.params.length ?? 0) === 0) {
		return classifyAliasBroadTarget(alias.typeAnnotation, lookup);
	}
	return dictionaryValueTypes(alias.typeAnnotation, lookup).length > 0
		? { kind: "generic container" }
		: null;
}

function referenceWideningTarget(
	type: ESTree.TSTypeReference,
	environment: TypeEnvironment,
): WideningTarget | null {
	const name = typeReferenceName(type);
	if (name === null) return null;
	if (isTransparentWrapper(name, environment)) {
		const wrapped = typeArgument(type, 0);
		return wrapped === null ? null : classifyWideningTarget(wrapped, environment);
	}
	if (name === "Record" && isBuiltIn(name, environment)) return OPEN_DICTIONARY;
	const alias = environment.aliases.get(name);
	return alias === undefined ? null : aliasWideningTarget(alias, type, environment);
}

export function classifyWideningTarget(
	type: ESTree.TSType,
	environment: TypeEnvironment,
): WideningTarget | null {
	const unwrapped = unwrapTransparentType(type);
	const keyword = WIDENING_KEYWORDS.get(unwrapped.type);
	if (keyword !== undefined) return keyword;
	if (unwrapped.type === "TSTypeLiteral") return literalWideningTarget(unwrapped);
	if (unwrapped.type === "TSMappedType") return OPEN_DICTIONARY;
	return unwrapped.type === "TSTypeReference"
		? referenceWideningTarget(unwrapped, environment)
		: null;
}

function isBroadMappedKey(type: ESTree.TSType, lookup: Lookup): boolean {
	const unwrapped = unwrapTransparentType(type);
	if (
		unwrapped.type === "TSStringKeyword" ||
		unwrapped.type === "TSNumberKeyword" ||
		unwrapped.type === "TSSymbolKeyword"
	) {
		return true;
	}
	if (unwrapped.type === "TSUnionType") {
		return unwrapped.types.every((member) => isBroadMappedKey(member, lookup));
	}
	if (unwrapped.type !== "TSTypeReference") return false;
	const name = typeReferenceName(unwrapped);
	if (name === null) return false;
	const substitution = lookup.substitutions.get(name);
	if (substitution !== undefined && !isUnappliedReferenceTo(substitution, name)) {
		return isBroadMappedKey(substitution, lookup);
	}
	return name === "PropertyKey" && isBuiltIn(name, lookup.environment);
}

function aliasReferenceTarget(type: ESTree.TSTypeReference, lookup: Lookup): WideningTarget | null {
	const name = typeReferenceName(type);
	if (name === null) return null;
	const substitution = lookup.substitutions.get(name);
	if (substitution !== undefined) {
		return isUnappliedReferenceTo(substitution, name)
			? null
			: classifyAliasBroadTarget(substitution, lookup);
	}
	if (isTransparentWrapper(name, lookup.environment)) {
		const wrapped = typeArgument(type, 0);
		return wrapped === null ? null : classifyAliasBroadTarget(wrapped, lookup);
	}
	if (name === "Record" && isBuiltIn(name, lookup.environment)) return OPEN_DICTIONARY;
	const expansion = expandAlias(name, type, lookup);
	return expansion === null ? null : classifyAliasBroadTarget(expansion.type, expansion.lookup);
}

function classifyAliasBroadTarget(type: ESTree.TSType, lookup: Lookup): WideningTarget | null {
	const unwrapped = unwrapTransparentType(type);
	const keyword = WIDENING_KEYWORDS.get(unwrapped.type);
	if (keyword !== undefined) return keyword;
	if (unwrapped.type === "TSTypeLiteral") {
		return unwrapped.members.some((member) => member.type === "TSIndexSignature")
			? OPEN_DICTIONARY
			: null;
	}
	if (unwrapped.type === "TSMappedType") {
		return isBroadMappedKey(unwrapped.constraint, lookup) ? OPEN_DICTIONARY : null;
	}
	return unwrapped.type === "TSTypeReference" ? aliasReferenceTarget(unwrapped, lookup) : null;
}

function innerEvidenceExpression(expression: ESTree.Expression): ESTree.Expression | null {
	switch (expression.type) {
		case "ParenthesizedExpression":
		case "TSAsExpression":
		case "TSNonNullExpression":
		case "TSSatisfiesExpression":
		case "TSTypeAssertion":
			return expression.expression;
		default:
			return null;
	}
}

export function isKnownEvidenceExpression(expression: ESTree.Expression): boolean {
	let current = expression;
	let inner = innerEvidenceExpression(current);
	while (inner !== null) {
		current = inner;
		inner = innerEvidenceExpression(current);
	}
	return KNOWN_EVIDENCE_TYPES.has(current.type);
}
