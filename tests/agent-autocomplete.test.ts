import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { type AgentAutocompleteAPI, registerAgentCommand } from "../agent/extensions/agent-autocomplete";
import type { AutocompleteItem, AutocompleteProvider } from "@oh-my-pi/pi-tui";

const agents = [
	{ name: "reviewer", description: "Reviews code" },
	{ name: "designer", description: "Designs interfaces" },
];

type Notification = { message: string; level: "info" | "error" };
type ExtensionContext = {
	cwd: string;
	ui: {
		notify(message: string, level: "info" | "error"): void;
		addAutocompleteProvider(factory: (current: AutocompleteProvider) => AutocompleteProvider): void;
	};
};
type SessionStartHandler = (event: { type: "session_start" }, context: ExtensionContext) => void | Promise<void>;
type InputHandler = (
	event: { type: "input"; text: string; source: "interactive" },
	context: ExtensionContext,
) => void | { handled?: boolean; text?: string } | Promise<void | { handled?: boolean; text?: string }>;
type RegisteredCommand = {
	description?: string;
	handler(args: string, context: ExtensionContext): Promise<void> | void;
};

function createExtensionHarness() {
	const discoveryCalls: string[] = [];
	const notifications: Notification[] = [];
	const registeredCommands = new Map<string, RegisteredCommand>();
	const sentUserMessages: string[] = [];
	let availableAgents = agents;
	let sessionStart: SessionStartHandler | undefined;
	let input: InputHandler | undefined;
	let autocompleteProvider: AutocompleteProvider | undefined;
	let fallbackSuggestionCalls = 0;
	let fallbackApplyCalls = 0;
	const fallbackItem = { value: "fallback", label: "fallback" };
	const fallbackResult = { items: [fallbackItem], prefix: "fallback" };
	const baseProvider: AutocompleteProvider = {
		async getSuggestions() {
			fallbackSuggestionCalls += 1;
			return fallbackResult;
		},
		applyCompletion(lines, cursorLine, cursorCol) {
			fallbackApplyCalls += 1;
			return { lines, cursorLine, cursorCol };
		},
		getInlineHint() {
			return "fallback hint";
		},
	};
	const context: ExtensionContext = {
		cwd: "/project",
		ui: {
			notify(message, level) {
				notifications.push({ message, level });
			},
			addAutocompleteProvider(factory) {
				autocompleteProvider = factory(baseProvider);
			},
		},
	};
	const piCandidate = {
		on(event: string, handler: SessionStartHandler | InputHandler) {
			if (event === "session_start") {
				// SAFETY: the event discriminator pairs session_start with SessionStartHandler.
				sessionStart = handler as SessionStartHandler;
			}
			if (event === "input") {
				// SAFETY: the event discriminator pairs input with InputHandler.
				input = handler as InputHandler;
			}
		},
		registerCommand(name: string, command: RegisteredCommand) {
			registeredCommands.set(name, command);
		},
		sendUserMessage(content: string) {
			sentUserMessages.push(content);
		},
	};
	// SAFETY: the candidate implements the complete AgentAutocompleteAPI owner contract used by registerAgentCommand.
	const pi = piCandidate as AgentAutocompleteAPI;

	registerAgentCommand(pi, cwd => {
		discoveryCalls.push(cwd);
		return availableAgents;
	});
	assert.ok(sessionStart);
	void sessionStart({ type: "session_start" }, context);
	assert.ok(autocompleteProvider);

	return {
		context,
		discoveryCalls,
		notifications,
		registeredCommands,
		sentUserMessages,
		setAvailableAgents(nextAgents: typeof agents) {
			availableAgents = nextAgents;
		},
		get fallbackSuggestionCalls() {
			return fallbackSuggestionCalls;
		},
		get fallbackApplyCalls() {
			return fallbackApplyCalls;
		},
		async suggest(line: string, cursorCol = line.length) {
			assert.ok(autocompleteProvider);
			return autocompleteProvider.getSuggestions([line], 0, cursorCol);
		},
		applyCompletion(
			line: string,
			cursorCol: number,
			item: AutocompleteItem,
			prefix: string,
		) {
			assert.ok(autocompleteProvider);
			return autocompleteProvider.applyCompletion([line], 0, cursorCol, item, prefix);
		},
		getInlineHint(line: string) {
			assert.ok(autocompleteProvider);
			return autocompleteProvider.getInlineHint?.([line], 0, line.length);
		},
		async startSession(cwd = context.cwd) {
			assert.ok(sessionStart);
			context.cwd = cwd;
			await sessionStart({ type: "session_start" }, context);
		},
		async runCommand(name: string, args: string) {
			const command = registeredCommands.get(name);
			assert.ok(command);
			await command.handler(args, context);
		},
		async submit(text: string) {
			assert.ok(input);
			return input({ type: "input", text, source: "interactive" }, context);
		},
	};
}

describe("agent autocomplete", () => {
	test("registers every discovered agent as an autocomplete-visible slash command", () => {
		const harness = createExtensionHarness();

		assert.equal(harness.registeredCommands.get("agent:reviewer")?.description, "Reviews code");
		assert.equal(harness.registeredCommands.get("agent:designer")?.description, "Designs interfaces");
	});

	test("adds commands for agents discovered when a session starts", async () => {
		const harness = createExtensionHarness();
		harness.setAvailableAgents([{ name: "completionist", description: "Judges completion" }]);

		await harness.startSession("/other-project");

		assert.equal(harness.discoveryCalls.at(-1), "/other-project");
		assert.equal(harness.registeredCommands.get("agent:completionist")?.description, "Judges completion");
		assert.deepEqual(
			(await harness.suggest("use /agent:comp"))?.items.map(item => item.value),
			["agent:completionist"],
		);
	});

	test("offers the agent namespace before roles at leading and mid-prompt positions", async () => {
		const harness = createExtensionHarness();

		assert.deepEqual(
			(await harness.suggest("/agent"))?.items.map(item => item.value),
			["agent:"],
		);
		assert.deepEqual(
			(await harness.suggest("this is /agen"))?.items.map(item => item.value),
			["agent:"],
		);
		assert.deepEqual(
			(await harness.suggest("this is /agent:rev"))?.items.map(item => item.value),
			["agent:reviewer"],
		);
	});

	test("accepts the agent namespace without inserting a separating space", async () => {
		const harness = createExtensionHarness();
		const result = await harness.suggest("/agent");
		assert.ok(result);

		assert.deepEqual(harness.applyCompletion("/agent", "/agent".length, result.items[0], result.prefix), {
			lines: ["/agent:"],
			cursorLine: 0,
			cursorCol: "/agent:".length,
		});
	});

	test("replaces only the mid-prompt role token and preserves text after the cursor", async () => {
		const harness = createExtensionHarness();
		const line = "this is /agent:rev and keep this";
		const cursorCol = "this is /agent:rev".length;
		const result = await harness.suggest(line, cursorCol);
		assert.ok(result);

		assert.deepEqual(harness.applyCompletion(line, cursorCol, result.items[0], result.prefix), {
			lines: ["this is /agent:reviewer and keep this"],
			cursorLine: 0,
			cursorCol: "this is /agent:reviewer".length,
		});
	});

	test("delegates unrelated autocomplete behavior to the existing provider", async () => {
		const harness = createExtensionHarness();

		assert.deepEqual(await harness.suggest("plain prose"), {
			items: [{ value: "fallback", label: "fallback" }],
			prefix: "fallback",
		});
		assert.equal(harness.fallbackSuggestionCalls, 1);
		assert.deepEqual(harness.applyCompletion("plain prose", 5, { value: "fallback", label: "fallback" }, "plain"), {
			lines: ["plain prose"],
			cursorLine: 0,
			cursorCol: 5,
		});
		assert.equal(harness.fallbackApplyCalls, 1);
		assert.equal(harness.getInlineHint("plain prose"), "fallback hint");
	});

	test("sends the canonical directive when an agent slash command is submitted", async () => {
		const harness = createExtensionHarness();

		await harness.runCommand("agent:reviewer", " inspect the completion flow ");

		assert.deepEqual(harness.sentUserMessages, [
			"Use the `reviewer` task agent for the following work:\ninspect the completion flow",
		]);
	});

	test("reports the colon syntax when slash-command work is missing", async () => {
		const harness = createExtensionHarness();

		await harness.runCommand("agent:reviewer", "   ");

		assert.deepEqual(harness.notifications, [{ message: "Usage: /agent:<role> <work>", level: "error" }]);
		assert.deepEqual(harness.sentUserMessages, []);
	});

	test("transforms leading and mid-prompt tokens into the canonical directive", async () => {
		const harness = createExtensionHarness();

		assert.deepEqual(await harness.submit("/agent:reviewer inspect the completion flow"), {
			text: "Use the `reviewer` task agent for the following work:\ninspect the completion flow",
		});
		assert.deepEqual(await harness.submit("analyse automated /agent:reviewer tests carefully"), {
			text: "Use the `reviewer` task agent for the following work:\nanalyse automated tests carefully",
		});
		assert.deepEqual(harness.notifications, []);
	});

	test("validates the selected agent against current project discovery", async () => {
		const harness = createExtensionHarness();
		harness.setAvailableAgents([{ name: "designer", description: "Designs interfaces" }]);

		assert.deepEqual(await harness.submit("inspect this /agent:reviewer carefully"), { handled: true });
		assert.deepEqual(harness.notifications, [{ message: "Unknown task agent: reviewer", level: "error" }]);
		assert.equal(harness.discoveryCalls.at(-1), "/project");
	});

	test("reports the colon syntax when transformed input has no work", async () => {
		const harness = createExtensionHarness();

		assert.deepEqual(await harness.submit("/agent:reviewer"), { handled: true });
		assert.deepEqual(harness.notifications, [{ message: "Usage: /agent:<role> <work>", level: "error" }]);
	});

	for (const text of ["/agent reviewer work", "/agents:reviewer work", "/compact /agent:reviewer work"]) {
		test(`leaves unsupported or nested command input unchanged: ${text}`, async () => {
			const harness = createExtensionHarness();

			assert.equal(await harness.submit(text), undefined);
			assert.deepEqual(harness.notifications, []);
		});
	}
});
