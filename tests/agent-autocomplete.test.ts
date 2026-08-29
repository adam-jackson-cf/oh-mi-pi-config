import assert from "node:assert/strict";
import { describe, test } from "node:test";
import type { AutocompleteProviderFactory } from "@oh-my-pi/pi-coding-agent";
import type { AutocompleteProvider } from "@oh-my-pi/pi-tui";
import { type AgentAutocompleteAPI, registerAgentCommand } from "../agent/extensions/agent-autocomplete";

const agents = [
	{ name: "reviewer", description: "Reviews code" },
	{ name: "designer", description: "Designs interfaces" },
];

type Notification = { message: string; level: "info" | "error" };
type ExtensionContext = {
	cwd: string;
	ui: {
		addAutocompleteProvider(factory: AutocompleteProviderFactory): void;
		notify(message: string, level: "info" | "error"): void;
	};
};
type SessionStartHandler = (event: { type: "session_start" }, context: ExtensionContext) => void | Promise<void>;
type InputHandler = (
	event: { type: "input"; text: string; source: "interactive" },
	context: ExtensionContext,
) => void | { handled?: boolean; text?: string } | Promise<void | { handled?: boolean; text?: string }>;

function createExtensionHarness() {
	const commandNames: string[] = [];
	const discoveryCalls: string[] = [];
	const notifications: Notification[] = [];
	const providerFactories: AutocompleteProviderFactory[] = [];
	let availableAgents = agents;
	let sessionStart: SessionStartHandler | undefined;
	let input: InputHandler | undefined;
	const context: ExtensionContext = {
		cwd: "/project",
		ui: {
			addAutocompleteProvider(factory) {
				providerFactories.push(factory);
			},
			notify(message, level) {
				notifications.push({ message, level });
			},
		},
	};
	const piCandidate = {
		registerCommand(name: string) {
			commandNames.push(name);
		},
		on(event: string, handler: SessionStartHandler | InputHandler) {
			if (event === "session_start") {
				// SAFETY: the session_start event contract supplies a SessionStartHandler.
				sessionStart = handler as SessionStartHandler;
			}
			if (event === "input") {
				// SAFETY: the input event contract supplies an InputHandler.
				input = handler as InputHandler;
			}
		},
	};
	// SAFETY: the candidate implements the complete AgentAutocompleteAPI owner contract used by registerAgentCommand.
	const pi = piCandidate as AgentAutocompleteAPI;

	registerAgentCommand(pi, cwd => {
		discoveryCalls.push(cwd);
		return availableAgents;
	});

	return {
		commandNames,
		context,
		discoveryCalls,
		notifications,
		setAvailableAgents(nextAgents: typeof agents) {
			availableAgents = nextAgents;
		},
		async startSession(cwd = context.cwd) {
			assert.ok(sessionStart);
			context.cwd = cwd;
			await sessionStart({ type: "session_start" }, context);
		},
		createProvider(base: AutocompleteProvider) {
			assert.equal(providerFactories.length, 1);
			return providerFactories[0](base);
		},
		async submit(text: string) {
			assert.ok(input);
			return input({ type: "input", text, source: "interactive" }, context);
		},
	};
}

function createBaseProvider() {
	const suggestionCalls: string[][] = [];
	const base: AutocompleteProvider = {
		async getSuggestions(lines) {
			suggestionCalls.push(lines);
			return {
				items: [{ value: "base", label: "base" }],
				prefix: "base",
			};
		},
		applyCompletion(lines, cursorLine, cursorCol) {
			return { lines, cursorLine, cursorCol };
		},
	};
	return { base, suggestionCalls };
}

describe("agent autocomplete", () => {
	test("registers only the extension health slash command", () => {
		const harness = createExtensionHarness();

		assert.deepEqual(harness.commandNames, ["extension-health-agent-autocomplete"]);
	});

	test("offers agent options after the colon at the start or within a prompt", async () => {
		const harness = createExtensionHarness();
		await harness.startSession();
		const { base } = createBaseProvider();
		const provider = harness.createProvider(base);

		assert.deepEqual(await provider.getSuggestions(["/agent:"], 0, 7), {
			items: [
				{ value: "reviewer", label: "reviewer", description: "Reviews code" },
				{ value: "designer", label: "designer", description: "Designs interfaces" },
			],
			prefix: "/agent:",
		});
		assert.deepEqual(await provider.getSuggestions(["analyse /agent:rev"], 0, 18), {
			items: [{ value: "reviewer", label: "reviewer", description: "Reviews code" }],
			prefix: "analyse /agent:rev",
		});
	});

	test("does not treat bare agent or plural agents tokens as agent autocomplete", async () => {
		const harness = createExtensionHarness();
		await harness.startSession();
		const { base, suggestionCalls } = createBaseProvider();
		const provider = harness.createProvider(base);

		assert.equal((await provider.getSuggestions(["/agent"], 0, 6))?.prefix, "base");
		assert.equal((await provider.getSuggestions(["analyse /agents:"], 0, 16))?.prefix, "base");
		assert.deepEqual(suggestionCalls, [["/agent"], ["analyse /agents:"]]);
	});

	test("applies only the agent token and preserves surrounding prompt lines", async () => {
		const harness = createExtensionHarness();
		await harness.startSession();
		const { base } = createBaseProvider();
		const provider = harness.createProvider(base);
		const lines = ["first line", "analyse /agent:rev", "last line"];
		const suggestions = await provider.getSuggestions(lines, 1, 18);
		assert.ok(suggestions);

		assert.deepEqual(provider.applyCompletion(lines, 1, 18, suggestions.items[0], suggestions.prefix), {
			lines: ["first line", "analyse /agent:reviewer ", "last line"],
			cursorLine: 1,
			cursorCol: 24,
		});
	});

	test("refreshes agent options when a session starts", async () => {
		const harness = createExtensionHarness();
		harness.setAvailableAgents([{ name: "completionist", description: "Judges completion" }]);
		await harness.startSession("/other-project");
		const { base } = createBaseProvider();
		const provider = harness.createProvider(base);

		assert.equal(harness.discoveryCalls.at(-1), "/other-project");
		assert.deepEqual(await provider.getSuggestions(["/agent:comp"], 0, 11), {
			items: [{ value: "completionist", label: "completionist", description: "Judges completion" }],
			prefix: "/agent:comp",
		});
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

	test("reports the colon syntax when work is missing", async () => {
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
