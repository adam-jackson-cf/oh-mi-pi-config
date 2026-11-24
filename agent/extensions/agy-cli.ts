import type { AssistantMessage, AssistantMessageEventStream, Context, Effort, Model, SimpleStreamOptions, Usage } from "@oh-my-pi/pi-ai";
import { createAssistantMessageEventStream, THINKING_EFFORTS } from "@oh-my-pi/pi-ai";
import { z, type ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import type { Subprocess } from "bun";

const PROVIDER = "agy";
const API = "agy-cli";
const MUTATING_TOOLS = new Set(["edit", "write", "bash", "eval", "ast_edit"]);
const SUBSCRIPTION_OVERRIDE_ENV = ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "AGY_ADC_AUTH"];
const EFFORT_ORDER: readonly string[] = THINKING_EFFORTS;
const ASSIGNMENT_SECTIONS = ["Role", "Context", "Plan"];
const READ_ONLY_SETTINGS = JSON.stringify({
	permissions: {
		allow: [
			"read_file(*)",
			...["ls", "cat", "head", "tail", "wc", "rg", "grep", "stat", "pwd"].map(command => `command(${command})`),
			...["status", "log", "show", "diff", "blame", "ls-files"].map(subcommand => `command(git ${subcommand})`),
		],
	},
});

type ModelSpec = { id: string; name: string; levels: string[]; maxTokens: number };
const MODELS: ModelSpec[] = [
	{ id: "gemini-4-carbon", name: "Gemini 4 Carbon (agy)", levels: ["low", "medium", "high"], maxTokens: 262144 },
	{ id: "gemini-4-neon", name: "Gemini 4 Neon (agy)", levels: ["low", "medium", "high", "xhigh"], maxTokens: 262144 },
	{ id: "gemini-4-hydrogen", name: "Gemini 4 Hydrogen (agy)", levels: ["low", "medium", "high", "xhigh"], maxTokens: 262144 },
	{ id: "gemini-4-argon", name: "Gemini 4 Argon (agy)", levels: ["low", "medium", "high"], maxTokens: 65536 },
	{ id: "gemini-3.8-flash", name: "Gemini 3.8 Flash (agy)", levels: ["low", "medium", "high"], maxTokens: 65536 },
];

type Conversation = { id: string; seen: number };
const conversations = new Map<string, Conversation>();

const agyUsageSchema = z.object({
	input_tokens: z.number().optional(),
	output_tokens: z.number().optional(),
	thinking_tokens: z.number().optional(),
	cache_read_tokens: z.number().optional(),
});
const agyResultSchema = z.object({
	conversation_id: z.string().optional(),
	status: z.string().optional(),
	response: z.string().optional(),
	usage: agyUsageSchema.optional(),
});
const agyEventSchema = z.object({
	event: z.string().optional(),
	conversation_id: z.string().optional(),
	step_update: z
		.object({
			state: z.string().optional(),
			step_type: z.string().optional(),
			tool_name: z.string().optional(),
			text_delta: z.string().optional(),
			tool_info: z.object({ parameters: z.unknown().optional() }).optional(),
		})
		.optional(),
	result: agyResultSchema.optional(),
});
type AgyUsage = z.infer<typeof agyUsageSchema>;
type AgyResult = z.infer<typeof agyResultSchema>;
type AgyEvent = z.infer<typeof agyEventSchema>;

type AgyRun = { conversationId: string | undefined; result: AgyResult | undefined };

export default function agyCli(pi: ExtensionAPI): void {
	pi.registerProvider(PROVIDER, {
		baseUrl: "https://antigravity.google/",
		apiKey: "agy-cli-signin",
		api: API,
		streamSimple: (model, context, options) => {
			const stream = createAssistantMessageEventStream();
			void run(stream, model, context, options);
			return stream;
		},
		models: MODELS.map(spec => ({
			id: spec.id,
			name: spec.name,
			reasoning: true,
			thinking: { mode: "effort", efforts: THINKING_EFFORTS.filter(effort => spec.levels.includes(effort)) },
			input: ["text"],
			cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
			contextWindow: 1048576,
			maxTokens: spec.maxTokens,
		})),
	});
}

function agyModelId(model: Model, reasoning: Effort | undefined): string {
	const levels = MODELS.find(spec => spec.id === model.id)?.levels ?? ["medium"];
	const wanted: string = reasoning ?? "medium";
	if (levels.includes(wanted)) return `${model.id}-${wanted}`;
	const top = levels[levels.length - 1] ?? "medium";
	const clamped = EFFORT_ORDER.indexOf(wanted) > EFFORT_ORDER.indexOf(top) ? top : (levels[0] ?? "medium");
	return `${model.id}-${clamped}`;
}

function assignment(context: Context): string {
	const system = (context.systemPrompt ?? []).join("\n\n");
	const sections: string[] = [];
	for (const heading of ASSIGNMENT_SECTIONS) {
		const body = new RegExp(`(?:^|\\n)⁂ ${heading}\\n([\\s\\S]*?)(?=\\n⁂ |$)`).exec(system)?.[1]?.trim();
		if (body) sections.push(`## ${heading}\n${body}`);
	}
	return sections.join("\n\n");
}

function messageText(message: Context["messages"][number]): string {
	if (message.role !== "user" && message.role !== "assistant") return "";
	if (message.role === "user" && message.synthetic) return "";
	if (!Array.isArray(message.content)) return message.content;
	return message.content
		.map(part => (part.type === "text" ? part.text : ""))
		.filter(Boolean)
		.join("\n");
}

function buildPrompt(context: Context, resumedFrom: number | undefined): string {
	const messages = context.messages;
	if (resumedFrom !== undefined) {
		return messages
			.slice(resumedFrom)
			.filter(message => message.role === "user")
			.map(messageText)
			.filter(Boolean)
			.join("\n\n");
	}
	const parts: string[] = [];
	const brief = assignment(context);
	if (brief) parts.push(brief);
	const last = messages.length - 1;
	const history = messages
		.slice(0, last)
		.map(message => {
			const text = messageText(message);
			return text ? `### ${message.role}\n${text}` : "";
		})
		.filter(Boolean);
	if (history.length > 0) parts.push(`## Conversation so far\n${history.join("\n\n")}`);
	const latest = messages[last];
	const latestText = latest ? messageText(latest) : "";
	parts.push(parts.length > 0 ? `## Current request\n${latestText}` : latestText);
	return parts.join("\n\n");
}

function usageFrom(usage: AgyUsage): Usage {
	const input = usage.input_tokens ?? 0;
	const output = (usage.output_tokens ?? 0) + (usage.thinking_tokens ?? 0);
	const cacheRead = usage.cache_read_tokens ?? 0;
	const total: Usage = {
		input,
		output,
		cacheRead,
		cacheWrite: 0,
		totalTokens: input + output + cacheRead,
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
	};
	if (usage.thinking_tokens !== undefined) total.reasoningTokens = usage.thinking_tokens;
	return total;
}

class AgyTurnWriter {
	readonly output: AssistantMessage;
	#stream: AssistantMessageEventStream;
	#textIndex: number | undefined;
	#activityIndex: number | undefined;

	constructor(model: Model, stream: AssistantMessageEventStream) {
		this.#stream = stream;
		this.output = {
			role: "assistant",
			content: [],
			api: model.api,
			provider: model.provider,
			model: model.id,
			usage: usageFrom({}),
			stopReason: "stop",
			timestamp: Date.now(),
		};
		stream.push({ type: "start", partial: this.output });
	}

	text(delta: string): void {
		if (!delta || this.#stream.done) return;
		if (this.#textIndex === undefined) {
			this.output.content.push({ type: "text", text: "" });
			this.#textIndex = this.output.content.length - 1;
			this.#stream.push({ type: "text_start", contentIndex: this.#textIndex, partial: this.output });
		}
		const block = this.output.content[this.#textIndex];
		if (block?.type !== "text") return;
		block.text += delta;
		this.#stream.push({ type: "text_delta", contentIndex: this.#textIndex, delta, partial: this.output });
	}

	activity(line: string): void {
		if (this.#stream.done) return;
		if (this.#activityIndex === undefined) {
			this.output.content.push({ type: "thinking", thinking: "" });
			this.#activityIndex = this.output.content.length - 1;
			this.#stream.push({ type: "thinking_start", contentIndex: this.#activityIndex, partial: this.output });
		}
		const block = this.output.content[this.#activityIndex];
		if (block?.type !== "thinking") return;
		const delta = `${line}\n`;
		block.thinking += delta;
		this.#stream.push({ type: "thinking_delta", contentIndex: this.#activityIndex, delta, partial: this.output });
	}

	finish(result: AgyResult): void {
		if (this.#stream.done) return;
		const response = result.response ?? "";
		const streamed = this.#textIndex === undefined ? undefined : this.output.content[this.#textIndex];
		if (streamed?.type === "text") streamed.text = response;
		else this.text(response);
		this.output.usage = usageFrom(result.usage ?? {});
		this.#endBlocks();
		this.#stream.push({ type: "done", reason: "stop", message: this.output });
	}

	fail(reason: "aborted" | "error", message: string): void {
		if (this.#stream.done) return;
		this.#endBlocks();
		this.output.stopReason = reason;
		this.output.errorMessage = message;
		this.#stream.push({ type: "error", reason, error: this.output });
	}

	#endBlocks(): void {
		for (const index of [this.#activityIndex, this.#textIndex]) {
			if (index === undefined) continue;
			const block = this.output.content[index];
			if (block?.type === "thinking")
				this.#stream.push({ type: "thinking_end", contentIndex: index, content: block.thinking, partial: this.output });
			if (block?.type === "text")
				this.#stream.push({ type: "text_end", contentIndex: index, content: block.text, partial: this.output });
		}
		this.#activityIndex = undefined;
		this.#textIndex = undefined;
	}
}

function parseEvent(line: string): AgyEvent | undefined {
	try {
		return agyEventSchema.parse(JSON.parse(line));
	} catch {
		return undefined;
	}
}

function applyStep(step: NonNullable<AgyEvent["step_update"]>, writer: AgyTurnWriter): void {
	if (step.step_type === "agent_response" && step.text_delta) writer.text(step.text_delta);
	if (step.step_type !== "tool" || step.state !== "ACTIVE") return;
	const parameters = JSON.stringify(step.tool_info?.parameters ?? {});
	writer.activity(`agy ▸ ${step.tool_name ?? "tool"} ${parameters.slice(0, 160)}`);
}

function applyEvent(event: AgyEvent, state: AgyRun, writer: AgyTurnWriter): void {
	if (event.event === "init") state.conversationId = event.conversation_id ?? state.conversationId;
	if (event.event === "result") state.result = event.result;
	if (event.step_update) applyStep(event.step_update, writer);
}

async function readEvents(child: Subprocess<"ignore", "pipe", "pipe">, writer: AgyTurnWriter): Promise<AgyRun> {
	const state: AgyRun = { conversationId: undefined, result: undefined };
	const handle = (line: string): void => {
		if (!line.trim()) return;
		const event = parseEvent(line);
		if (event) applyEvent(event, state, writer);
		else writer.activity(`agy ▸ ${line.slice(0, 200)}`);
	};
	const decoder = new TextDecoder();
	let buffer = "";
	for await (const chunk of child.stdout) {
		buffer += decoder.decode(chunk, { stream: true });
		for (let newline = buffer.indexOf("\n"); newline >= 0; newline = buffer.indexOf("\n")) {
			handle(buffer.slice(0, newline));
			buffer = buffer.slice(newline + 1);
		}
	}
	handle(buffer);
	return state;
}

function agyArgs(model: Model, context: Context, options: SimpleStreamOptions | undefined, prompt: string, resumed: Conversation | undefined): string[] {
	const mutating = (context.tools ?? []).some(tool => MUTATING_TOOLS.has(tool.name));
	return [
		"agy",
		"-p",
		prompt,
		"--model",
		agyModelId(model, options?.reasoning),
		"--output-format",
		"stream-json",
		...(mutating ? ["--dangerously-skip-permissions"] : ["--mode", "plan", "--settings", READ_ONLY_SETTINGS]),
		...(resumed ? ["--conversation", resumed.id] : []),
	];
}

function spawnAgy(args: string[], options: SimpleStreamOptions | undefined): Subprocess<"ignore", "pipe", "pipe"> {
	const env = { ...process.env };
	for (const key of SUBSCRIPTION_OVERRIDE_ENV) delete env[key];
	return Bun.spawn(args, { cwd: options?.cwd ?? process.cwd(), env, stdin: "ignore", stdout: "pipe", stderr: "pipe" });
}

function failureMessage(result: AgyResult | undefined, exitCode: number, stderr: string): string {
	const status = result?.status ?? `exit ${exitCode}`;
	const signIn = /sign in/i.test(stderr) ? "; run `agy` once in a terminal to sign in" : "";
	return `agy provider: run failed (${status})${signIn}${stderr ? `: ${stderr.slice(-800)}` : ""}`;
}

type AgyExit = { state: AgyRun; exitCode: number; stderr: string };

async function execute(child: Subprocess<"ignore", "pipe", "pipe">, writer: AgyTurnWriter, signal: AbortSignal | undefined): Promise<AgyExit> {
	const abort = (): void => {
		child.kill();
	};
	signal?.addEventListener("abort", abort, { once: true });
	const state = await readEvents(child, writer);
	const exitCode = await child.exited;
	signal?.removeEventListener("abort", abort);
	const stderr = (await new Response(child.stderr).text()).trim();
	return { state, exitCode, stderr };
}

function startTurn(model: Model, context: Context, options: SimpleStreamOptions | undefined, writer: AgyTurnWriter): Subprocess<"ignore", "pipe", "pipe"> | undefined {
	const previous = options?.sessionId ? conversations.get(options.sessionId) : undefined;
	const resumed = previous !== undefined && previous.seen < context.messages.length ? previous : undefined;
	const prompt = buildPrompt(context, resumed?.seen);
	if (!prompt.trim()) {
		writer.fail("error", "agy provider: this turn has no user text to send");
		return undefined;
	}
	try {
		return spawnAgy(agyArgs(model, context, options, prompt, resumed), options);
	} catch (error) {
		writer.fail("error", `agy provider: could not start agy (${String(error)}); install it and run \`agy\` once to sign in`);
		return undefined;
	}
}

function settle(exit: AgyExit, context: Context, options: SimpleStreamOptions | undefined, writer: AgyTurnWriter): void {
	if (options?.signal?.aborted) {
		writer.fail("aborted", "agy run cancelled");
		return;
	}
	const result = exit.state.result;
	if (result?.status !== "SUCCESS") {
		writer.fail("error", failureMessage(result, exit.exitCode, exit.stderr));
		return;
	}
	const id = result.conversation_id ?? exit.state.conversationId;
	if (options?.sessionId && id) conversations.set(options.sessionId, { id, seen: context.messages.length + 1 });
	writer.finish(result);
}

async function run(
	stream: AssistantMessageEventStream,
	model: Model,
	context: Context,
	options: SimpleStreamOptions | undefined,
): Promise<void> {
	const writer = new AgyTurnWriter(model, stream);
	const child = startTurn(model, context, options, writer);
	if (!child) return;
	settle(await execute(child, writer, options?.signal), context, options, writer);
}
