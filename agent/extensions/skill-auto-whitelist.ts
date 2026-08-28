import { readFile } from "node:fs/promises";
import path from "node:path";
import type { ExtensionAPI, Skill } from "@oh-my-pi/pi-coding-agent";
import { z } from "zod";

const CONFIG_FILE_NAME = "skill-auto-whitelist.json";
const SKILL_POLICY =
  "Only the skills listed below may be selected automatically. Other discovered skills MUST be used only when the user explicitly refers to the skill by exact name.";
const SKILLS_BLOCK_PATTERN = /<skills>\r?\n([\s\S]*?)\r?\n<\/skills>/g;
const SKILL_ENTRY_PATTERN = /^(\s*-\s+)([^:\r\n]+):/;
const SKILL_NAME_CHARACTER_PATTERN = /[A-Za-z0-9_-]/;
const AUTOMATIC_SKILLS_CONFIG_SCHEMA = z.object({
  automaticSkills: z.array(z.unknown()),
});

export function parseAutomaticSkills(content: string): Set<string> {
  const configResult = AUTOMATIC_SKILLS_CONFIG_SCHEMA.safeParse(JSON.parse(content));
  if (!configResult.success) {
    throw new Error('Expected an object with an "automaticSkills" array.');
  }

  const automaticSkills = new Set<string>();
  for (const value of configResult.data.automaticSkills) {
    const skillNameResult = z.string().safeParse(value);
    if (
      !skillNameResult.success ||
      !skillNameResult.data.trim() ||
      skillNameResult.data !== skillNameResult.data.trim()
    ) {
      throw new Error('Every "automaticSkills" entry must be a non-empty, trimmed string.');
    }
    automaticSkills.add(skillNameResult.data);
  }
  return automaticSkills;
}

function containsExactSkillName(prompt: string, skillName: string): boolean {
  let offset = prompt.indexOf(skillName);
  while (offset !== -1) {
    const before = prompt[offset - 1];
    const after = prompt[offset + skillName.length];
    const hasBoundaryBefore = before === undefined || !SKILL_NAME_CHARACTER_PATTERN.test(before);
    const hasBoundaryAfter = after === undefined || !SKILL_NAME_CHARACTER_PATTERN.test(after);
    if (hasBoundaryBefore && hasBoundaryAfter) return true;
    offset = prompt.indexOf(skillName, offset + skillName.length);
  }
  return false;
}

export function selectExposedSkillNames(
  skills: readonly Skill[],
  automaticSkills: ReadonlySet<string>,
  prompt: string,
): Set<string> {
  const exposed = new Set<string>();
  for (const skill of skills) {
    if (skill._source?.level === "project") {
      exposed.add(skill.name);
      continue;
    }
    if (skill._source?.level !== "user") continue;
    if (automaticSkills.has(skill.name) || containsExactSkillName(prompt, skill.name)) {
      exposed.add(skill.name);
    }
  }
  return exposed;
}

export function filterSkillPrompt(
  systemPrompt: readonly string[],
  skills: readonly Skill[],
  exposedSkillNames: ReadonlySet<string>,
): string[] {
  const activeSkills = new Map(skills.map((skill) => [skill.name, skill]));
  let policyAdded = systemPrompt.some((block) => block.includes(SKILL_POLICY));

  return systemPrompt.map((block) =>
    block.replace(SKILLS_BLOCK_PATTERN, (_match, body: string) => {
      const retainedLines = body.split(/\r?\n/).filter((line) => {
        const name = line.match(SKILL_ENTRY_PATTERN)?.[2]?.trim();
        if (!name) return true;

        const skill = activeSkills.get(name);
        if (!skill || skill._source?.level !== "user") return true;
        return exposedSkillNames.has(name);
      });
      const policy = policyAdded ? "" : `${SKILL_POLICY}\n`;
      policyAdded = true;
      return `${policy}<skills>\n${retainedLines.join("\n")}\n</skills>`;
    }),
  );
}

export default function skillAutoWhitelist(pi: ExtensionAPI): void {
  pi.registerCommand("extension-health-skill-auto-whitelist", {
    description: "Verify skill-auto-whitelist extension registration",
    handler: async (_args, ctx) => {
      ctx.ui.notify("Extension registered: skill-auto-whitelist", "info");
    },
  });

  const configPath = path.join(pi.pi.getAgentDir(), CONFIG_FILE_NAME);
  let reportedError: string | undefined;

  pi.on("before_agent_start", async (event, ctx) => {
    let automaticSkills: Set<string>;
    try {
      automaticSkills = parseAutomaticSkills(await readFile(configPath, "utf8"));
      reportedError = undefined;
    } catch (error) {
      automaticSkills = new Set();
      const message = error instanceof Error ? error.message : String(error);
      if (message !== reportedError) {
        ctx.ui.notify(
          `Skill auto-whitelist configuration is invalid; global skills require explicit naming. ${message}`,
          "warning",
        );
        reportedError = message;
      }
    }

    const skills = pi.pi.getActiveSkills();
    const exposedSkillNames = selectExposedSkillNames(skills, automaticSkills, event.prompt);
    return {
      systemPrompt: filterSkillPrompt(event.systemPrompt, skills, exposedSkillNames),
    };
  });
}
