import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { escapeHatchRules } from "../tools/oxlint/anti-slop/escape-hatch-rules.ts";
import { typeContractRules } from "../tools/oxlint/anti-slop/type-contract-rules.ts";

const remediation = /^Root cause: (?<cause>.+?)\. Fix: (?<fix>.+)\.$/;
const placeholder = /\b(todo|tbd|tbc|fixme|xxx|placeholder|lorem)\b|\.\.\./i;
const minimumWords = 8;

function messageProblems(message: string): string[] {
	const parts = remediation.exec(message)?.groups;
	if (parts === undefined) {
		return ["message must read 'Root cause: <cause>. Fix: <fix>.'"];
	}
	const problems: string[] = [];
	for (const [label, text] of [
		["root cause", parts["cause"] ?? ""],
		["fix", parts["fix"] ?? ""],
	] as const) {
		if (text.split(/\s+/).length < minimumWords) {
			problems.push(`${label} must state at least ${minimumWords} words`);
		} else if (placeholder.test(text)) {
			problems.push(`${label} holds placeholder text`);
		}
	}
	return problems;
}

const rules = { ...escapeHatchRules, ...typeContractRules };
const messages = Object.entries(rules).flatMap(([name, rule]) =>
	Object.values(rule.meta?.messages ?? {}).map(message => ({ name, message })),
);

describe("anti-slop rule messages", () => {
	test("every rule message states its root cause and a holistic fix", () => {
		assert.equal(messages.length >= Object.keys(rules).length, true);
		for (const { name, message } of messages) {
			assert.deepEqual(messageProblems(message), [], name);
		}
		const texts = messages.map(item => item.message);
		assert.equal(new Set(texts).size, texts.length);
	});

	test("messages without a root cause, with a short fix or with placeholders fail", () => {
		const cause = "the value reached this code before anything parsed it into a type";
		const fix = "parse the input once at its boundary into a named domain type";
		assert.deepEqual(messageProblems(`Root cause: ${cause}. Fix: ${fix}.`), []);
		assert.deepEqual(messageProblems(`Fix: ${fix}.`), [
			"message must read 'Root cause: <cause>. Fix: <fix>.'",
		]);
		assert.deepEqual(messageProblems(`Root cause: ${cause}. Fix: rename it.`), [
			"fix must state at least 8 words",
		]);
		assert.deepEqual(messageProblems(`Root cause: TODO explain why this rule fires here later. Fix: ${fix}.`), [
			"root cause holds placeholder text",
		]);
	});
});
