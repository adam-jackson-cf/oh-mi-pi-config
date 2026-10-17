import { randomUUID } from "node:crypto";
import { lstat, readFile, realpath, writeFile } from "node:fs/promises";
import { isAbsolute, relative, resolve } from "node:path";
import type { ImageContent, TextContent } from "@oh-my-pi/pi-ai";
import { type } from "@oh-my-pi/pi-ai";
import { z, type ExtensionAPI, type ExtensionContext } from "@oh-my-pi/pi-coding-agent";

export const LSP_EVIDENCE_POLICY_MARKER = "LSP_EVIDENCE_OBSERVATION_ONLY";

const NAVIGATION_ACTIONS = new Set(["references", "definition", "hover"]);
const BIND_TOOL = "lsp_evidence_bind";
const CAPTURE_TOOL = "lsp_evidence_capture";
const bindSchema = type({ requestPath: type("string") });
const captureSchema = type({});

const requestOperationSchema = z.object({
  operationId: z.string().min(1),
  action: z.enum(["diagnostics", "references", "definition", "hover", "symbols"]),
  file: z.string(),
  line: z.number().int().min(1).optional(),
  symbol: z.string().min(1).optional(),
  query: z.string().optional(),
}).strict();
type RequestOperation = z.infer<typeof requestOperationSchema>;

const impactRequestSchema = z.object({
  purpose: z.literal("impact"),
  requestId: z.string().min(1),
  cwd: z.string(),
  evidenceRoot: z.string(),
  capturePath: z.string(),
  targetPaths: z.array(z.string()).min(1),
  operations: z.array(requestOperationSchema).min(1),
  timeout: z.object({ perOperationMs: z.number().int().min(5000).max(300000) }).strict(),
}).strict();

/** The `lsp` tool input fields a frozen operation binds; `rename` and code-action fields must be absent. */
const lspCallSchema = z.object({
  action: z.string(),
  file: z.string(),
  timeout: z.number(),
  line: z.number().optional(),
  symbol: z.string().optional(),
  query: z.string().optional(),
  new_name: z.undefined().optional(),
  apply: z.undefined().optional(),
  payload: z.undefined().optional(),
});
type LspCall = z.infer<typeof lspCallSchema>;

interface BoundOperation {
  operationId: string;
  action: string;
  file: string;
  line?: number;
  symbol?: string;
  query?: string;
  timeout: number;
}
type OperationInput = Omit<BoundOperation, "operationId">;

interface CapturedOperation {
  isError: boolean;
  toolResponse: string;
}

interface Binding {
  requestId: string;
  captureId: string;
  capturePath: string;
  operations: BoundOperation[];
  next: number;
  pending: Map<string, number>;
  results: Map<number, CapturedOperation>;
  hasFailure: boolean;
  invalid: boolean;
  finalized: boolean;
}

type CaptureRecord =
  | { type: "request-start" | "request-complete"; requestId: string; captureId: string; operationCount: number }
  | {
    type: "operation-result"; requestId: string; captureId: string; operationId: string; ordinal: number;
    action: string; file: string; isError: boolean; toolResponse: string;
  };

const bindings = new Map<string, Binding>();

function policyActive(ctx: ExtensionContext): boolean {
  return ctx.getSystemPrompt().some((block) =>
    block.includes(LSP_EVIDENCE_POLICY_MARKER),
  );
}

function contained(root: string, path: string): boolean {
  const rel = relative(root, path);
  return rel !== "" && !rel.startsWith("..") && !isAbsolute(rel);
}

function canonicalAbsolute(path: string): boolean {
  return isAbsolute(path) && resolve(path) === path;
}

async function requireCanonicalRegular(root: string, path: string): Promise<string> {
  if (!canonicalAbsolute(path)) {
    throw new Error("request contains a non-canonical path");
  }
  const canonical = await realpath(path).catch(() => null);
  if (canonical !== path || !contained(root, path)) {
    throw new Error("request path escapes cwd or traverses a symlink");
  }
  const stat = await lstat(path);
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new Error("request target is not a regular file");
  }
  return path;
}

/** Applies the per-action field rules and returns the operation the `lsp` call must equal. */
function boundOperation(operation: RequestOperation, timeoutSeconds: number): BoundOperation {
  const bound: BoundOperation = {
    operationId: operation.operationId,
    action: operation.action,
    file: operation.file,
    timeout: timeoutSeconds,
  };
  if (NAVIGATION_ACTIONS.has(operation.action)) {
    if (operation.query !== undefined || operation.line === undefined || operation.symbol === undefined) {
      throw new Error("navigation operation requires exact line and symbol");
    }
    bound.line = operation.line;
    bound.symbol = operation.symbol;
  } else if (operation.action === "symbols") {
    if (operation.line !== undefined || operation.symbol !== undefined) {
      throw new Error("symbols operation is malformed");
    }
    if (operation.query !== undefined) bound.query = operation.query;
  } else if (
    operation.line !== undefined ||
    operation.symbol !== undefined ||
    operation.query !== undefined
  ) {
    throw new Error("diagnostics operation is malformed");
  }
  return bound;
}

async function loadBinding(requestPath: string, ctx: ExtensionContext): Promise<Binding> {
  if (!canonicalAbsolute(requestPath)) {
    throw new Error("requestPath must be canonical and absolute");
  }
  const canonicalRequest = await realpath(requestPath).catch(() => null);
  const requestStat = canonicalRequest === requestPath ? await lstat(requestPath) : null;
  if (!requestStat?.isFile() || requestStat.isSymbolicLink()) {
    throw new Error("requestPath must be a regular non-symlink file");
  }

  const parsed = impactRequestSchema.safeParse(JSON.parse(await readFile(requestPath, "utf8")));
  if (!parsed.success) {
    throw new Error(`thin impact request is malformed: ${parsed.error.message}`);
  }
  const request = parsed.data;
  const canonicalCwd = await realpath(ctx.cwd).catch(() => null);
  if (request.cwd !== ctx.cwd || canonicalCwd !== ctx.cwd) {
    throw new Error("request cwd does not match the evidence session cwd");
  }
  const { evidenceRoot, capturePath } = request;
  if (
    !canonicalAbsolute(evidenceRoot) ||
    !canonicalAbsolute(capturePath) ||
    capturePath !== resolve(evidenceRoot, "impact-capture.jsonl") ||
    !contained(evidenceRoot, capturePath)
  ) {
    throw new Error("thin impact evidence root or capture path is malformed");
  }
  const canonicalEvidenceRoot = await realpath(evidenceRoot).catch(() => null);
  const evidenceRootStat =
    canonicalEvidenceRoot === evidenceRoot ? await lstat(evidenceRoot) : null;
  if (
    canonicalEvidenceRoot !== evidenceRoot ||
    !evidenceRootStat?.isDirectory() ||
    evidenceRootStat.isSymbolicLink() ||
    !contained(ctx.cwd, evidenceRoot)
  ) {
    throw new Error("thin impact evidence root escapes cwd or traverses a symlink");
  }
  if (requestPath !== resolve(evidenceRoot, "impact-request.json")) {
    throw new Error("requestPath must be the canonical impact request packet");
  }
  const captureStat = await lstat(capturePath).catch(() => null);
  if (captureStat !== null) {
    throw new Error("thin impact capture path must not exist");
  }

  const targets = new Set<string>();
  for (const target of request.targetPaths) {
    targets.add(await requireCanonicalRegular(ctx.cwd, target));
  }
  if (targets.size !== request.targetPaths.length) {
    throw new Error("request targetPaths must be unique");
  }

  const timeoutSeconds = request.timeout.perOperationMs / 1000;
  const operationIds = new Set<string>();
  const operationTuples = new Set<string>();
  const operations: BoundOperation[] = [];
  for (const operation of request.operations) {
    if (operationIds.has(operation.operationId) || !targets.has(operation.file)) {
      throw new Error("request operation is malformed or outside targetPaths");
    }
    operationIds.add(operation.operationId);
    await requireCanonicalRegular(ctx.cwd, operation.file);

    const bound = boundOperation(operation, timeoutSeconds);
    const tuple = JSON.stringify([
      bound.action,
      bound.file,
      bound.line,
      bound.symbol,
      bound.query,
    ]);
    if (operationTuples.has(tuple)) {
      throw new Error("request operations must not duplicate canonical operation tuples");
    }
    operationTuples.add(tuple);
    operations.push(bound);
  }

  return {
    requestId: request.requestId,
    captureId: `capture-${randomUUID()}`,
    capturePath,
    operations,
    next: 0,
    pending: new Map(),
    results: new Map(),
    hasFailure: false,
    invalid: false,
    finalized: false,
  };
}

function actualOperation(call: LspCall): OperationInput {
  const operation: OperationInput = {
    action: call.action,
    file: call.file,
    timeout: call.timeout,
  };
  if (call.line !== undefined) operation.line = call.line;
  if (call.symbol !== undefined) operation.symbol = call.symbol;
  if (call.query !== undefined) operation.query = call.query;
  return operation;
}

/** `call` is the parsed `lsp` tool input, or undefined when it failed `lspCallSchema`. */
export function lspEvidencePolicyViolation(
  binding: Binding | undefined,
  call: LspCall | undefined,
): string | undefined {
  if (!binding) return "lsp-evidence request is not bound";
  if (binding.invalid) return "lsp-evidence binding is terminally invalid";
  if (binding.finalized) return "lsp-evidence capture is already finalized";
  if (binding.pending.size !== 0) return "lsp-evidence prior operation awaits its result";
  const expected = binding.operations[binding.next];
  if (!expected) return "lsp-evidence request has no remaining operation";
  const { operationId: _operationId, ...expectedInput } = expected;
  if (call === undefined || JSON.stringify(actualOperation(call)) !== JSON.stringify(expectedInput)) {
    return "lsp-evidence call does not equal the next frozen request operation";
  }
  return undefined;
}

function captureJsonl(binding: Binding): string {
  const records: CaptureRecord[] = [
    {
      type: "request-start",
      requestId: binding.requestId,
      captureId: binding.captureId,
      operationCount: binding.operations.length,
    },
  ];
  for (const [ordinal, operation] of binding.operations.entries()) {
    const result = binding.results.get(ordinal);
    if (!result) throw new Error("frozen LSP operation has no result");
    records.push({
      type: "operation-result",
      requestId: binding.requestId,
      captureId: binding.captureId,
      operationId: operation.operationId,
      ordinal,
      action: operation.action,
      file: operation.file,
      isError: result.isError,
      toolResponse: result.toolResponse,
    });
  }
  records.push({
    type: "request-complete",
    requestId: binding.requestId,
    captureId: binding.captureId,
    operationCount: binding.operations.length,
  });
  return records.map((record) => JSON.stringify(record)).join("\n");
}

async function finalizeCapture(binding: Binding): Promise<{
  text: string;
  isError?: true;
}> {
  if (binding.finalized) {
    return { text: "thin impact capture is already finalized", isError: true };
  }
  if (binding.invalid) {
    return { text: "thin impact capture binding is terminally invalid", isError: true };
  }
  if (
    binding.next !== binding.operations.length ||
    binding.pending.size !== 0 ||
    binding.results.size !== binding.operations.length
  ) {
    binding.invalid = true;
    return { text: "frozen LSP operation accounting is incomplete", isError: true };
  }
  let jsonl: string;
  try {
    jsonl = captureJsonl(binding);
  } catch (error) {
    return {
      text: error instanceof Error ? error.message : String(error),
      isError: true,
    };
  }
  try {
    await writeFile(binding.capturePath, `${jsonl}\n`, { flag: "wx" });
  } catch (error) {
    return {
      text: error instanceof Error ? error.message : String(error),
      isError: true,
    };
  }
  binding.finalized = true;
  if (binding.hasFailure) {
    return {
      text: "frozen LSP operation failed; trusted capture was retained",
      isError: true,
    };
  }
  return {
    text: JSON.stringify({
      requestId: binding.requestId,
      captureId: binding.captureId,
      capturePath: binding.capturePath,
    }),
  };
}

const NO_LANGUAGE_SERVER = /^(?:No language server found(?: for this (?:action|file))?|.+: No language server found)$/;

function observedLspFailure(isError: boolean, content: (TextContent | ImageContent)[]): boolean {
  return isError || content.some((block) => block.type === "text" && NO_LANGUAGE_SERVER.test(block.text));
}

export default function lspEvidencePolicy(pi: ExtensionAPI): void {
  pi.registerCommand("extension-health-lsp-evidence-policy", {
    description: "Verify lsp-evidence-policy extension registration",
    handler: async (_args, ctx) => {
      ctx.ui.notify("Extension registered: lsp-evidence-policy", "info");
    },
  });

  pi.registerTool({
    name: BIND_TOOL,
    label: "Bind LSP Evidence Request",
    description:
      "Bind this lsp-evidence session to one immutable request before any LSP operation.",
    parameters: bindSchema,
    hidden: true,
    approval: "read",
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      if (!policyActive(ctx)) {
        return {
          content: [{ type: "text", text: "lsp-evidence policy marker is absent" }],
          isError: true,
        };
      }
      const sessionId = ctx.sessionManager.getSessionId();
      if (bindings.has(sessionId)) {
        return {
          content: [{ type: "text", text: "lsp-evidence request is already bound" }],
          isError: true,
        };
      }
      try {
        const binding = await loadBinding(params.requestPath, ctx);
        bindings.set(sessionId, binding);
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify({
                requestId: binding.requestId,
                operationCount: binding.operations.length,
              }),
            },
          ],
        };
      } catch (error) {
        return {
          content: [
            {
              type: "text",
              text: error instanceof Error ? error.message : String(error),
            },
          ],
          isError: true,
        };
      }
    },
  });

  pi.registerTool({
    name: CAPTURE_TOOL,
    label: "Finalize Trusted LSP Impact Capture",
    description:
      "Persist the extension-observed frozen LSP operation record after every bound operation returns.",
    parameters: captureSchema,
    hidden: true,
    approval: "read",
    async execute(_toolCallId, _params, _signal, _onUpdate, ctx) {
      if (!policyActive(ctx)) {
        return {
          content: [{ type: "text", text: "lsp-evidence policy marker is absent" }],
          isError: true,
        };
      }
      const binding = bindings.get(ctx.sessionManager.getSessionId());
      if (!binding) {
        return {
          content: [{ type: "text", text: "lsp-evidence request is not bound" }],
          isError: true,
        };
      }
      const result = await finalizeCapture(binding);
      return {
        content: [{ type: "text", text: result.text }],
        isError: result.isError,
      };
    },
  });

  pi.on("tool_call", (event, ctx) => {
    if (event.toolName !== "lsp" || !policyActive(ctx)) return undefined;
    const binding = bindings.get(ctx.sessionManager.getSessionId());
    const call = lspCallSchema.safeParse(event.input);
    const reason = lspEvidencePolicyViolation(binding, call.success ? call.data : undefined);
    if (reason !== undefined) return { block: true, reason };
    if (binding) binding.pending.set(event.toolCallId, binding.next);
    return undefined;
  });

  pi.on("tool_result", (event, ctx) => {
    if (event.toolName !== "lsp" || !policyActive(ctx)) return undefined;
    const binding = bindings.get(ctx.sessionManager.getSessionId());
    const ordinal = binding?.pending.get(event.toolCallId);
    if (binding && ordinal !== undefined) {
      binding.results.set(ordinal, {
        isError: event.isError,
        toolResponse: JSON.stringify(event.content),
      });
      binding.hasFailure ||= observedLspFailure(event.isError, event.content);
      binding.pending.delete(event.toolCallId);
      binding.next += 1;
    }
    return undefined;
  });

  pi.on("session_shutdown", (_event, ctx) => {
    bindings.delete(ctx.sessionManager.getSessionId());
  });
}
