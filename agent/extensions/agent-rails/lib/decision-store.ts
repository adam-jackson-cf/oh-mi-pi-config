// The agent-rails decision store: one SQLite database under the omp agent dir that holds every Jev
// judgment answer, deterministic check result and rail decision. engine/data/decision-store.sql
// is the only schema; the engine applies the same file through Python's sqlite3.
import { Database } from "bun:sqlite";
import { mkdirSync, readFileSync } from "node:fs";
import { homedir, hostname } from "node:os";
import { dirname, join } from "node:path";

export const STORE_FILE = "agent-rails/agent-rails.db";
export const SCHEMA_PATH = join(import.meta.dir, "..", "engine", "data", "decision-store.sql");
const BUSY_TIMEOUT_MS = 5000;
let schema: { text: string; version: number } | undefined;

/** decision-store.sql and the schema version it sets, read on first use so loading the extension never depends on it. */
export function storeSchema(): { text: string; version: number } {
	if (schema) return schema;
	const text = readFileSync(SCHEMA_PATH, "utf8");
	const version = /^PRAGMA user_version = (\d+);$/m.exec(text)?.[1];
	if (version === undefined) throw new Error(`${SCHEMA_PATH} sets no user_version`);
	schema = { text, version: Number(version) };
	return schema;
}

/** The omp agent dir: PI_CODING_AGENT_DIR, else ~/.omp/agent, as the engine resolves it. Rails state lives here. */
export function agentDir(value: string | undefined = process.env["PI_CODING_AGENT_DIR"]): string {
	return value?.trim() || join(homedir(), ".omp", "agent");
}

/** The decision store under the omp agent dir. */
export function storePath(dir: string | undefined = process.env["PI_CODING_AGENT_DIR"]): string {
	return join(agentDir(dir), STORE_FILE);
}

/** The machine a row was written on. */
export const machineName = (): string => hostname();

const userVersion = (db: Database): number => db.query<{ user_version: number }, []>("PRAGMA user_version").get()?.user_version ?? 0;

/** Opens the store, creating it on first use; a database at an unknown schema version is refused. */
export function openStore(path: string = storePath()): Database {
	const { text, version: expected } = storeSchema();
	mkdirSync(dirname(path), { recursive: true });
	const db = new Database(path, { create: true, strict: true });
	try {
		db.exec(`PRAGMA busy_timeout = ${BUSY_TIMEOUT_MS}`);
		db.exec("PRAGMA journal_mode = WAL");
		if (userVersion(db) === 0) db.exec(text);
		const version = userVersion(db);
		if (version !== expected) throw new Error(`the decision store ${path} is at schema version ${version}; this harness reads version ${expected} only`);
		return db;
	} catch (error) {
		db.close();
		throw error;
	}
}

/** Runs `use` on an open store and closes it. */
export function withStore<T>(use: (db: Database) => T, path: string = storePath()): T {
	const db = openStore(path);
	try { return use(db); }
	finally { db.close(); }
}
