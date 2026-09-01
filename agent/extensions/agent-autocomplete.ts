import { readdirSync, readFileSync, type Dirent } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import type {
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

type AgentCommandContext = {
	ui: {
		notify(message: string, level: "info" | "error"): void;
	};
};

export interface AgentAutocompleteAPI {
	on(event: "session_start", handler: ExtensionHandler<SessionStartEvent>): void;
	on(event: "input", handler: ExtensionHandler<InputEvent, InputEventResult>): void;
	registerCommand(
		name: string,
		options: {
			description?: string;
			handler(args: string, context: AgentCommandContext): Promise<void> | void;
		},
	): void;
	sendUserMessage(content: string): void;
}

const AGENT_NAMESPACE = "agent:";
const MID_PROMPT_AGENT_RE = /(^|\s)\/agent:([^\s/]+)(\s|$)/;
const TRAILING_SLASH_TOKEN_RE = /(?:^|\s)(\/[^\s/]*)$/;

function getAgentToken(
	lines: string[],
	cursorLine: number,
	cursorCol: number,
): { prefix: string; tokenStart: number } | undefined {
	const currentLine = lines[cursorLine] ?? "";
	const textBeforeCursor = currentLine.slice(0, cursorCol);
	const prefix = TRAILING_SLASH_TOKEN_RE.exec(textBeforeCursor)?.[1];
	if (!prefix) {
		return undefined;
	}

	const query = prefix.slice(1).toLowerCase();
	if (query.length === 0 || !(AGENT_NAMESPACE.startsWith(query) || query.startsWith(AGENT_NAMESPACE))) {
		return undefined;
	}
	return { prefix, tokenStart: textBeforeCursor.length - prefix.length };
}

function getAgentSuggestions(
	agents: AgentDefinition[],
	prefix: string,
): { items: AutocompleteItem[]; prefix: string } | null {
	const query = prefix.slice(1).toLowerCase();
	if (AGENT_NAMESPACE.startsWith(query) && query !== AGENT_NAMESPACE) {
		return {
			items: [
				{
					value: AGENT_NAMESPACE,
					label: AGENT_NAMESPACE,
					description: "Select a task agent for the following work",
				},
			],
			prefix,
		};
	}

	const roleQuery = query.slice(AGENT_NAMESPACE.length);
	const items = agents
		.filter(agent => agent.name.toLowerCase().startsWith(roleQuery))
		.map(agent => ({
			value: `${AGENT_NAMESPACE}${agent.name}`,
			label: `${AGENT_NAMESPACE}${agent.name}`,
			description: agent.description,
		}));
	return items.length > 0 ? { items, prefix } : null;
}

function wrapAutocompleteProvider(
	current: AutocompleteProvider,
	getAgents: () => AgentDefinition[],
): AutocompleteProvider {
	const wrapped: AutocompleteProvider = {
		async getSuggestions(lines, cursorLine, cursorCol, signal) {
			const token = getAgentToken(lines, cursorLine, cursorCol);
			if (token) {
				return getAgentSuggestions(getAgents(), token.prefix);
			}
			return current.getSuggestions(lines, cursorLine, cursorCol, signal);
		},
		applyCompletion(lines, cursorLine, cursorCol, item, prefix) {
			const token = getAgentToken(lines, cursorLine, cursorCol);
			if (!token || token.prefix !== prefix || !item.value.startsWith(AGENT_NAMESPACE)) {
				return current.applyCompletion(lines, cursorLine, cursorCol, item, prefix);
			}

			const currentLine = lines[cursorLine] ?? "";
			const afterCursor = currentLine.slice(cursorCol);
			const separator = item.value === AGENT_NAMESPACE || /^\s/.test(afterCursor) ? "" : " ";
			const insert = `/${item.value}${separator}`;
			const newLines = [...lines];
			newLines[cursorLine] = `${currentLine.slice(0, token.tokenStart)}${insert}${afterCursor}`;
			return {
				lines: newLines,
				cursorLine,
				cursorCol: token.tokenStart + insert.length,
			};
		},
	};

	if (current.getInlineHint) {
		wrapped.getInlineHint = current.getInlineHint.bind(current);
	}
	if (current.trySyncSlashCompletion) {
		wrapped.trySyncSlashCompletion = current.trySyncSlashCompletion.bind(current);
	}
	if (current.trySyncInlineReplace) {
		wrapped.trySyncInlineReplace = current.trySyncInlineReplace.bind(current);
	}
	if (current.getForceFileSuggestions) {
		wrapped.getForceFileSuggestions = current.getForceFileSuggestions.bind(current);
	}
	if (current.shouldTriggerFileCompletion) {
		wrapped.shouldTriggerFileCompletion = current.shouldTriggerFileCompletion.bind(current);
	}
	return wrapped;
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

function formatAgentDirective(agentName: string, work: string): string {
	return `Use the \`${agentName}\` task agent for the following work:\n${work}`;
}

function registerDiscoveredAgentCommands(
	pi: AgentAutocompleteAPI,
	agents: AgentDefinition[],
	registeredCommands: Set<string>,
): void {
	for (const agent of agents) {
		const commandName = `${AGENT_NAMESPACE}${agent.name}`;
		if (registeredCommands.has(commandName)) {
			continue;
		}
		registeredCommands.add(commandName);
		pi.registerCommand(commandName, {
			description: agent.description,
			handler: (args, context) => {
				const work = args.trim();
				if (!work) {
					context.ui.notify("Usage: /agent:<role> <work>", "error");
					return;
				}
				pi.sendUserMessage(formatAgentDirective(agent.name, work));
			},
		});
	}
}

export function registerAgentCommand(
	pi: AgentAutocompleteAPI,
	discoverAgents: AgentDiscovery = discoverAgentDefinitions,
): void {
	let agents = discoverAgents(process.cwd());
	let autocompleteProviderRegistered = false;
	const registeredCommands = new Set<string>();
	registerDiscoveredAgentCommands(pi, agents, registeredCommands);

	pi.on("session_start", (_event, context) => {
		agents = discoverAgents(context.cwd);
		registerDiscoveredAgentCommands(pi, agents, registeredCommands);
		if (!autocompleteProviderRegistered) {
			context.ui.addAutocompleteProvider(current => wrapAutocompleteProvider(current, () => agents));
			autocompleteProviderRegistered = true;
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
			text: formatAgentDirective(agent.name, invocation.work),
		};
	});
}

export default registerAgentCommand;
