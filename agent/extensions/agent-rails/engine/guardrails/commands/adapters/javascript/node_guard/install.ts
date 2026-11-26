import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { guardFastTier, type Deny } from './fast-tier.ts';
import { patch, type GuardCheck } from './patching.ts';
import { canonicalArgument, canonicalPath, guardUserData, within, type Protect } from './user-data.ts';

const append = fs.appendFileSync.bind(fs);

export interface GuardSettings {
  tier: string;
  protectedHome: string;
  allowed: string[];
  events: string;
}

export interface RuntimeGuards {
  deny: Deny;
  protect: Protect;
  protectPath(target: string, operation: string): void;
  patch<Owner>(owner: Owner, name: string, check: GuardCheck): void;
  tier: string;
}

type RuntimeExtension = (guards: RuntimeGuards) => void;

function journal(events: string, entry: Record<string, string | string[]>): void {
  append(events, JSON.stringify(entry) + '\n');
}

function denial(settings: GuardSettings, violations: string[]): Deny {
  let recording = false;
  return (rule, operation) => {
    const reason = `${rule}: blocked ${operation}`;
    violations.push(reason);
    if (!recording) {
      recording = true;
      try { journal(settings.events, { kind: 'violation', reason }); }
      finally { recording = false; }
    }
    throw new Error(reason);
  };
}

export function install(settings: GuardSettings, extend: RuntimeExtension = () => undefined): string[] {
  const protectedHome = canonicalPath(settings.protectedHome);
  const allowed = settings.allowed.map(canonicalPath);
  const violations: string[] = [];
  const deny = denial(settings, violations);
  const protectPath = (target: string, operation: string) => {
    if (within(target, protectedHome) && !allowed.some(root => within(target, root))) {
      deny('TEST.user-data-guard', `${operation} ${target}`);
    }
  };
  const protect: Protect = (args, index, operation) => {
    const target = canonicalArgument(args, index);
    if (target) protectPath(target, operation);
  };
  guardUserData(protect);
  if (settings.tier === 't1') guardFastTier(deny);
  extend({ deny, protect, protectPath, patch, tier: settings.tier });
  syncBuiltinESMExports();
  journal(settings.events, { kind: 'armed', tier: settings.tier, guards: settings.tier === 't1' ? ['fast-tier', 'user-data'] : ['user-data'] });
  return violations;
}
