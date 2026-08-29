import { readdirSync, readFileSync, type Dirent } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import type {
	ExtensionAPI,
	ExtensionHandler,
	InputEvent,
	InputEventResult,
	SessionStartEvent,
} from "@oh-my-pi/pi-coding-agent";
import type { AutocompleteItem, AutocompleteProvider } from "@oh-my-pi/pi-tui";

type AgentDefinition = {
	name: string;
	description: string;
};

type AgentDiscovery = (cwd: string) => AgentDefinition[];

type AgentInvocation = {
	name: string;
	work: string;
};

export interface AgentAutocompleteAPI {
	registerCommand: ExtensionAPI["registerCommand"];
	on(event: "session_start", handler: ExtensionHandler<SessionStartEvent>): void;
	on(event: "input", handler: ExtensionHandler<InputEvent, InputEventResult>): void;
}

const AGENT_NAMESPACE = "agent:";
const MID_PROMPT_AGENT_RE = /(^|\s)\/agent:([^\s/]+)(\s|$)/;
const TRAILING_AGENT_TOKEN_RE = /(^|\s)(\/agent:([^\s/]*))$/;

function findTrailingAgentToken(textBeforeCursor: string): { prefix: string; query: string } | undefined {
	const match = TRAILING_AGENT_TOKEN_RE.exec(textBeforeCursor);
	const prefix = match?.[2];
	const query = match?.[3];
	return prefix !== undefined && query !== undefined ? { prefix, query } : undefined;
}

function parseAgentInvocation(text: string): AgentInvocation | undefined {
	const trimmedStart = text.trimStart();
	if (trimmedStart.startsWith(`/${AGENT_NAMESPACE}`)) {
		const match = /^\/agent:([^\s/]+)(?:\s+([\s\S]*))?$/.exec(trimmedStart);
		if (!match?.[1]) {
			return undefined;
		}
		return { name: match[1], work: match[2]?.trim() ?? "" };
	}
	if (trimmedStart.startsWith("/") || trimmedStart.startsWith("!")) {
		return undefined;
	}

	const match = MID_PROMPT_AGENT_RE.exec(text);
	if (!match?.[2]) {
		return undefined;
	}
	const leading = match[1] ?? "";
	const trailing = match[3] ?? "";
	const tokenStart = match.index + leading.length;
	const tokenEnd = match.index + match[0].length - trailing.length;
	const before = text.slice(0, tokenStart).trimEnd();
	const after = text.slice(tokenEnd).trimStart();
	return {
		name: match[2],
		work: [before, after]
			.filter(part => part.length > 0)
			.join(" ")
			.trim(),
	};
}

function createAgentAutocompleteProvider(
	current: AutocompleteProvider,
	getAgents: () => AgentDefinition[],
): AutocompleteProvider {
	return {
		async getSuggestions(lines, cursorLine, cursorCol) {
			const currentLine = lines[cursorLine] ?? "";
			const token = findTrailingAgentToken(currentLine.slice(0, cursorCol));
			if (!token) {
				return current.getSuggestions(lines, cursorLine, cursorCol);
			}

			const query = token.query.toLocaleLowerCase();
			const items = getAgents()
				.filter(agent => agent.name.toLocaleLowerCase().startsWith(query))
				.map(
					(agent): AutocompleteItem => ({
						value: agent.name,
						label: agent.name,
						description: agent.description,
					}),
				);
			return items.length > 0 ? { items, prefix: currentLine.slice(0, cursorCol) } : null;
		},
		applyCompletion(lines, cursorLine, cursorCol, item, prefix) {
			if (!findTrailingAgentToken(prefix)) {
				return current.applyCompletion(lines, cursorLine, cursorCol, item, prefix);
			}

			const currentLine = lines[cursorLine] ?? "";
			const textBeforeCursor = currentLine.slice(0, cursorCol);
			const token = findTrailingAgentToken(textBeforeCursor);
			const agent = getAgents().find(candidate => candidate.name === item.value);
			if (!token || !agent || !agent.name.toLocaleLowerCase().startsWith(token.query.toLocaleLowerCase())) {
				return { lines, cursorLine, cursorCol };
			}

			const beforeToken = textBeforeCursor.slice(0, -token.prefix.length);
			const insert = `/${AGENT_NAMESPACE}${agent.name} `;
			const newLines = [...lines];
			newLines[cursorLine] = `${beforeToken}${insert}${currentLine.slice(cursorCol)}`;
			return {
				lines: newLines,
				cursorLine,
				cursorCol: beforeToken.length + insert.length,
			};
		},
		getInlineHint: current.getInlineHint?.bind(current),
		trySyncSlashCompletion: current.trySyncSlashCompletion?.bind(current),
		trySyncInlineReplace: current.trySyncInlineReplace?.bind(current),
		getForceFileSuggestions: current.getForceFileSuggestions?.bind(current),
		shouldTriggerFileCompletion: current.shouldTriggerFileCompletion?.bind(current),
	};
}

function getProfileName(): string | undefined {
	const profileFlag = process.argv.findIndex(argument => argument === "--profile");
	if (profileFlag >= 0) {
		return process.argv[profileFlag + 1];
	}

	const inlineProfile = process.argv.find(argument => argument.startsWith("--profile="));
	return inlineProfile?.slice("--profile=".length);
}

function getAgentDirectory(): string {
	if (process.env.PI_CODING_AGENT_DIR) {
		return process.env.PI_CODING_AGENT_DIR;
	}

	const profile = getProfileName();
	return profile ? join(homedir(), ".omp", "profiles", profile, "agent") : join(homedir(), ".omp", "agent");
}

function findProjectAgentDirectory(cwd: string): string | undefined {
	let directory = resolve(cwd);
	while (true) {
		const agentDirectory = join(directory, ".omp", "agents");
		try {
			if (readdirSync(agentDirectory, { withFileTypes: true })) {
				return agentDirectory;
			}
		} catch {
			// No project agent directory at this level.
		}

		const parent = dirname(directory);
		if (parent === directory) {
			return undefined;
		}
		directory = parent;
	}
}

function parseFrontmatterValue(frontmatter: string, key: string): string | undefined {
	const match = new RegExp(`^${key}:\\s*(.+)$`, "m").exec(frontmatter);
	if (!match) {
		return undefined;
	}

	const value = match[1].trim();
	if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) {
		try {
			return JSON.parse(value);
		} catch {
			return value.slice(1, -1);
		}
	}
	if (value.length >= 2 && value.startsWith("'") && value.endsWith("'")) {
		return value.slice(1, -1).replace(/''/g, "'");
	}
	return value;
}

function readAgentDirectory(directory: string): AgentDefinition[] {
	let entries: Dirent<string>[];
	try {
		entries = readdirSync(directory, { encoding: "utf8", withFileTypes: true });
	} catch {
		return [];
	}

	return entries
		.filter(entry => entry.isFile() && entry.name.endsWith(".md"))
		.sort((left, right) => left.name.localeCompare(right.name))
		.flatMap(entry => {
			try {
				const content = readFileSync(join(directory, entry.name), "utf8");
				const frontmatter = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(content)?.[1];
				if (!frontmatter) {
					return [];
				}

				const name = parseFrontmatterValue(frontmatter, "name");
				const description = parseFrontmatterValue(frontmatter, "description");
				return name && description ? [{ name, description }] : [];
			} catch {
				return [];
			}
		});
}

export function discoverAgentDefinitions(cwd: string): AgentDefinition[] {
	const directories = [findProjectAgentDirectory(cwd), join(getAgentDirectory(), "agents")].filter(
		(directory): directory is string => Boolean(directory),
	);
	const agents = new Map<string, AgentDefinition>();

	for (const directory of directories) {
		for (const agent of readAgentDirectory(directory)) {
			if (!agents.has(agent.name)) {
				agents.set(agent.name, agent);
			}
		}
	}

	return [...agents.values()].sort((left, right) => left.name.localeCompare(right.name));
}

export function registerAgentCommand(
	pi: AgentAutocompleteAPI,
	discoverAgents: AgentDiscovery = discoverAgentDefinitions,
): void {
	let agents = discoverAgents(process.cwd());
	let autocompleteRegistered = false;

	pi.registerCommand("extension-health-agent-autocomplete", {
		description: "Verify agent-autocomplete extension registration",
		handler: async (_args, context) => {
			context.ui.notify("Extension registered: agent-autocomplete", "info");
		},
	});

	pi.on("session_start", (_event, context) => {
		agents = discoverAgents(context.cwd);
		if (!autocompleteRegistered) {
			context.ui.addAutocompleteProvider(current => createAgentAutocompleteProvider(current, () => agents));
			autocompleteRegistered = true;
		}
	});

	pi.on("input", (event, context) => {
		const invocation = parseAgentInvocation(event.text);
		if (!invocation) {
			return;
		}

		agents = discoverAgents(context.cwd);
		const agent = agents.find(candidate => candidate.name === invocation.name);
		if (!agent) {
			context.ui.notify(`Unknown task agent: ${invocation.name}`, "error");
			return { handled: true };
		}
		if (!invocation.work) {
			context.ui.notify("Usage: /agent:<role> <work>", "error");
			return { handled: true };
		}

		return {
			text: `Use the \`${agent.name}\` task agent for the following work:\n${invocation.work}`,
		};
	});
}

export default registerAgentCommand;
