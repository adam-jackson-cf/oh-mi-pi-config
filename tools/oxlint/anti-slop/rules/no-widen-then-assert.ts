import { defineRule } from "@oxlint/plugins";
import type { ESTree, Variable } from "@oxlint/plugins";

type BroadTypeKind = "top" | "object" | "record";

type KnownValueEvidence = {
  readonly type: ESTree.TSType | null;
};

type ScopeList = readonly {
  readonly references: readonly {
    readonly identifier: ESTree.Node;
    readonly resolved: Variable | null;
  }[];
}[];

type ConstBinding = {
  readonly declarator: ESTree.VariableDeclarator;
  readonly init: ESTree.Expression;
};

type Annotation = {
  readonly identifier: ESTree.Node;
  readonly type: ESTree.TSType;
};

type Widening = {
  readonly broadKind: BroadTypeKind;
  readonly original: ESTree.Expression;
};

type WidenedBinding = {
  readonly broadKind: BroadTypeKind;
  readonly evidence: KnownValueEvidence;
  readonly declaredAt: number;
  readonly boundary: ESTree.Node | null;
};

const functionBoundaryTypes = new Set([
  "ArrowFunctionExpression",
  "FunctionDeclaration",
  "FunctionExpression",
  "TSDeclareFunction",
  "TSEmptyBodyFunctionExpression",
]);

const alwaysObjectTypes = new Set([
  "TSArrayType",
  "TSConstructorType",
  "TSFunctionType",
  "TSMappedType",
  "TSObjectKeyword",
  "TSTupleType",
]);

const constructedValueTypes = new Set([
  "ArrayExpression",
  "ArrowFunctionExpression",
  "ClassExpression",
  "FunctionExpression",
  "Literal",
  "NewExpression",
  "ObjectExpression",
  "TemplateLiteral",
]);

function unwrapExpressionParentheses(expression: ESTree.Expression): ESTree.Expression {
  let current = expression;
  while (current.type === "ParenthesizedExpression") current = current.expression;
  return current;
}

function unwrapTypeParentheses(type: ESTree.TSType): ESTree.TSType {
  let current = type;
  while (current.type === "TSParenthesizedType") current = current.typeAnnotation;
  return current;
}

function typeReferenceName(type: ESTree.TSTypeReference): string | null {
  return type.typeName.type === "Identifier" ? type.typeName.name : null;
}

function isUnknownOrAnyType(type: ESTree.TSType): boolean {
  const unwrapped = unwrapTypeParentheses(type);
  return unwrapped.type === "TSUnknownKeyword" || unwrapped.type === "TSAnyKeyword";
}

function isBroadRecordKeyType(type: ESTree.TSType): boolean {
  const unwrapped = unwrapTypeParentheses(type);
  if (
    unwrapped.type === "TSStringKeyword" ||
    unwrapped.type === "TSNumberKeyword" ||
    unwrapped.type === "TSSymbolKeyword"
  ) {
    return true;
  }
  if (unwrapped.type === "TSUnionType") return unwrapped.types.every(isBroadRecordKeyType);
  return unwrapped.type === "TSTypeReference" && typeReferenceName(unwrapped) === "PropertyKey";
}

function typeArguments(type: ESTree.TSTypeReference): readonly ESTree.TSType[] {
  return type.typeArguments?.params ?? [];
}

function isBroadRecordReference(type: ESTree.TSTypeReference): boolean {
  const name = typeReferenceName(type);
  const parameters = typeArguments(type);
  const [first, second] = parameters;
  if (name === "Readonly") return first !== undefined && isBroadRecordType(first);
  return (
    name === "Record" &&
    parameters.length === 2 &&
    first !== undefined &&
    second !== undefined &&
    isBroadRecordKeyType(first) &&
    isUnknownOrAnyType(second)
  );
}

function isBroadIndexLiteral(type: ESTree.TSTypeLiteral): boolean {
  const [member] = type.members;
  if (type.members.length !== 1 || member?.type !== "TSIndexSignature") return false;
  const [parameter] = member.parameters;
  return (
    member.parameters.length === 1 &&
    parameter !== undefined &&
    isBroadRecordKeyType(parameter.typeAnnotation.typeAnnotation) &&
    isUnknownOrAnyType(member.typeAnnotation.typeAnnotation)
  );
}

function isBroadRecordType(type: ESTree.TSType): boolean {
  const unwrapped = unwrapTypeParentheses(type);
  if (unwrapped.type === "TSTypeReference") return isBroadRecordReference(unwrapped);
  return unwrapped.type === "TSTypeLiteral" && isBroadIndexLiteral(unwrapped);
}

function broadTypeKind(type: ESTree.TSType): BroadTypeKind | null {
  const unwrapped = unwrapTypeParentheses(type);
  if (unwrapped.type === "TSUnknownKeyword" || unwrapped.type === "TSAnyKeyword") return "top";
  if (unwrapped.type === "TSObjectKeyword") return "object";
  return isBroadRecordType(unwrapped) ? "record" : null;
}

function assertedExpression(
  node: ESTree.TSAsExpression | ESTree.TSTypeAssertion,
): ESTree.Expression {
  return unwrapExpressionParentheses(node.expression);
}

function assertionFromExpression(
  expression: ESTree.Expression,
): ESTree.TSAsExpression | ESTree.TSTypeAssertion | null {
  const unwrapped = unwrapExpressionParentheses(expression);
  return unwrapped.type === "TSAsExpression" || unwrapped.type === "TSTypeAssertion"
    ? unwrapped
    : null;
}

function normalizedTypeText(sourceText: string, type: ESTree.TSType): string {
  return sourceText.slice(type.start, type.end).replaceAll(/\s+/gu, "");
}

function typesHaveSameSyntax(
  sourceText: string,
  left: ESTree.TSType | null,
  right: ESTree.TSType,
): boolean {
  return (
    left !== null &&
    normalizedTypeText(sourceText, unwrapTypeParentheses(left)) ===
      normalizedTypeText(sourceText, unwrapTypeParentheses(right))
  );
}

function isDefinitelyObjectType(type: ESTree.TSType): boolean {
  const unwrapped = unwrapTypeParentheses(type);
  if (alwaysObjectTypes.has(unwrapped.type)) return true;
  if (unwrapped.type === "TSTypeLiteral") return unwrapped.members.length > 0;
  if (unwrapped.type === "TSIntersectionType") return unwrapped.types.every(isDefinitelyObjectType);
  return (
    unwrapped.type === "TSTypeOperator" &&
    unwrapped.operator === "readonly" &&
    isDefinitelyObjectType(unwrapped.typeAnnotation)
  );
}

function isNarrowerRecordReference(type: ESTree.TSTypeReference): boolean {
  const name = typeReferenceName(type);
  const parameters = typeArguments(type);
  const [first, second] = parameters;
  if (name === "Readonly") return first !== undefined && isDefinitelyNarrowerRecordType(first);
  return (
    name === "Record" &&
    parameters.length === 2 &&
    second !== undefined &&
    !isUnknownOrAnyType(second)
  );
}

function isDefinitelyNarrowerRecordType(type: ESTree.TSType): boolean {
  const unwrapped = unwrapTypeParentheses(type);
  if (unwrapped.type === "TSTypeLiteral") {
    return unwrapped.members.some((member) => member.type !== "TSIndexSignature");
  }
  return unwrapped.type === "TSTypeReference" && isNarrowerRecordReference(unwrapped);
}

function functionBoundary(node: ESTree.Node): ESTree.Node | null {
  let current = node.parent;
  while (current !== null && current.type !== "Program") {
    if (functionBoundaryTypes.has(current.type)) return current;
    current = current.parent;
  }
  return null;
}

function resolvedVariableForIdentifier(
  scopes: ScopeList,
  identifier: ESTree.IdentifierReference,
): Variable | null {
  for (const scope of scopes) {
    const reference = scope.references.find(
      (candidate) =>
        candidate.identifier.start === identifier.start &&
        candidate.identifier.end === identifier.end,
    );
    if (reference !== undefined) return reference.resolved;
  }
  return null;
}

function variableDeclarator(variable: Variable): ESTree.VariableDeclarator | null {
  for (const definition of variable.defs) {
    if (definition.type === "Variable" && definition.node.type === "VariableDeclarator") {
      return definition.node;
    }
  }
  return null;
}

function stableConstBinding(variable: Variable): ConstBinding | null {
  const declarator = variableDeclarator(variable);
  if (
    declarator === null ||
    declarator.parent.type !== "VariableDeclaration" ||
    declarator.parent.kind !== "const" ||
    declarator.init === null
  ) {
    return null;
  }
  return variable.references.some((reference) => reference.isWrite() && !reference.init)
    ? null
    : { declarator, init: declarator.init };
}

function annotationOf(variable: Variable): Annotation | null {
  for (const identifier of variable.identifiers) {
    const type = identifier.typeAnnotation?.typeAnnotation;
    if (type !== undefined) return { identifier, type };
  }
  return null;
}

function identifierEvidence(
  identifier: ESTree.IdentifierReference,
  scopes: ScopeList,
  boundary: ESTree.Node | null,
  visitedVariables: ReadonlySet<Variable>,
): KnownValueEvidence | null {
  const variable = resolvedVariableForIdentifier(scopes, identifier);
  if (variable === null || visitedVariables.has(variable)) return null;
  const annotation = annotationOf(variable);
  if (annotation !== null) {
    return functionBoundary(annotation.identifier) !== boundary ||
      broadTypeKind(annotation.type) !== null
      ? null
      : { type: annotation.type };
  }
  const binding = stableConstBinding(variable);
  if (binding === null || functionBoundary(binding.declarator) !== boundary) return null;
  return knownValueEvidence(binding.init, scopes, boundary, new Set([...visitedVariables, variable]));
}

function knownValueEvidence(
  expression: ESTree.Expression,
  scopes: ScopeList,
  boundary: ESTree.Node | null,
  visitedVariables: ReadonlySet<Variable>,
): KnownValueEvidence | null {
  const unwrapped = unwrapExpressionParentheses(expression);
  if (unwrapped.type === "TSAsExpression" || unwrapped.type === "TSTypeAssertion") {
    return broadTypeKind(unwrapped.typeAnnotation) === null
      ? { type: unwrapped.typeAnnotation }
      : null;
  }
  if (constructedValueTypes.has(unwrapped.type)) return { type: null };
  return unwrapped.type === "Identifier"
    ? identifierEvidence(unwrapped, scopes, boundary, visitedVariables)
    : null;
}

function wideningOf(
  declaredType: ESTree.TSType | undefined,
  init: ESTree.Expression,
): Widening | null {
  const assertion = assertionFromExpression(init);
  const initializerBroadKind = assertion === null ? null : broadTypeKind(assertion.typeAnnotation);
  const declaredBroadKind = declaredType === undefined ? null : broadTypeKind(declaredType);
  const broadKind = declaredBroadKind ?? initializerBroadKind;
  if (broadKind === null) return null;
  const original =
    assertion !== null && initializerBroadKind !== null ? assertedExpression(assertion) : init;
  return { broadKind, original };
}

function widenedBinding(variable: Variable, scopes: ScopeList): WidenedBinding | null {
  const binding = stableConstBinding(variable);
  if (binding === null) return null;
  const { declarator, init } = binding;
  const { id } = declarator;
  if (id.type !== "Identifier") return null;
  const widening = wideningOf(id.typeAnnotation?.typeAnnotation, init);
  if (widening === null) return null;
  const boundary = functionBoundary(declarator);
  const evidence = knownValueEvidence(widening.original, scopes, boundary, new Set([variable]));
  return evidence === null
    ? null
    : { broadKind: widening.broadKind, evidence, declaredAt: declarator.end, boundary };
}

function assertionIsNarrower(
  sourceText: string,
  broadKind: BroadTypeKind,
  evidence: KnownValueEvidence,
  assertedType: ESTree.TSType,
): boolean {
  if (broadTypeKind(assertedType) !== null) return false;
  if (broadKind === "top") return true;
  if (typesHaveSameSyntax(sourceText, evidence.type, assertedType)) return true;
  if (broadKind === "object") return isDefinitelyObjectType(assertedType);
  return isDefinitelyNarrowerRecordType(assertedType);
}

/** Detect immutable local bindings that erase a known type and are later asserted back to a narrower type. */
export const noWidenThenAssertRule = defineRule({
  meta: {
    type: "problem",
    docs: {
      description:
        "Disallow local const flows that explicitly widen a known value before asserting the widened binding to a narrower type.",
    },
    messages: {
      widenThenAssert:
        'Root cause: binding "{{name}}" is declared with a broader type and later asserted back, so the compiler stops checking the value in between. Fix: keep the precise type from initialisation through use, parsing boundary input once.',
    },
  },
  createOnce(context) {
    let scopes: ScopeList = [];

    const checkAssertion = (node: ESTree.TSAsExpression | ESTree.TSTypeAssertion) => {
      const expression = assertedExpression(node);
      if (expression.type !== "Identifier") return;

      const variable = resolvedVariableForIdentifier(scopes, expression);
      if (variable === null) return;
      const widened = widenedBinding(variable, scopes);
      if (
        widened === null ||
        node.start <= widened.declaredAt ||
        functionBoundary(node) !== widened.boundary ||
        !assertionIsNarrower(
          context.sourceCode.text,
          widened.broadKind,
          widened.evidence,
          node.typeAnnotation,
        )
      ) {
        return;
      }

      context.report({
        node,
        messageId: "widenThenAssert",
        data: { name: expression.name },
      });
    };

    return {
      Program() {
        scopes = context.sourceCode.scopeManager.scopes;
      },
      TSAsExpression: checkAssertion,
      TSTypeAssertion: checkAssertion,
    };
  },
});
