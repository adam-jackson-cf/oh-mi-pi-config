import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { patch } from './patching.ts';

const realpath = fs.realpathSync.bind(fs);
const WRITE_FLAGS = fs.constants.O_WRONLY | fs.constants.O_RDWR | fs.constants.O_CREAT | fs.constants.O_TRUNC | fs.constants.O_APPEND;
const SINGLE_TARGET = ['writeFile', 'appendFile', 'truncate', 'mkdir', 'mkdtemp', 'rm', 'rmdir', 'unlink', 'chmod', 'chown', 'utimes', 'lchmod', 'lchown', 'lutimes'];
const BOTH_TARGETS = ['rename', 'link'];
const DESTINATION_TARGET = ['copyFile', 'cp', 'symlink'];

export type Protect = (args: unknown[], index: number, operation: string) => void;

export function canonicalPath(input: string): string {
  let current = path.resolve(input);
  const tail: string[] = [];
  for (;;) {
    try { return path.join(realpath(current), ...tail.toReversed()); }
    catch (error) {
      if (!(error instanceof Error && 'code' in error && (error.code === 'ENOENT' || error.code === 'ENOTDIR'))) throw error;
      const parent = path.dirname(current);
      if (parent === current) return path.resolve(input);
      tail.push(path.basename(current));
      current = parent;
    }
  }
}

export function canonicalArgument(args: unknown[], index: number): string | null {
  const value = args[index];
  const input = value instanceof URL ? fileURLToPath(value) : Buffer.isBuffer(value) ? value.toString() : value;
  if (Number.isInteger(input)) return null;
  if (input !== String(input)) throw new TypeError('The "path" argument must be of type string, Buffer or URL');
  return canonicalPath(input);
}

export const within = (value: string, root: string) => value === root || value.startsWith(root + path.sep);

function opensForWriting(args: unknown[], index: number): boolean {
  const flags = args[index];
  if (Number.isInteger(flags)) return Boolean(Number(flags) & WRITE_FLAGS);
  return /[wa+]/.test(String(flags ?? 'r'));
}

function readStreamWrites(args: unknown[]): boolean {
  const options = args[1];
  if (!(options instanceof Object) || !('flags' in options) || !options.flags) return false;
  return opensForWriting([options.flags], 0);
}

function guardFileApis<Owner>(owner: Owner, suffix: string, protect: Protect): void {
  for (const name of SINGLE_TARGET) patch(owner, name + suffix, args => protect(args, 0, name));
  for (const name of BOTH_TARGETS) patch(owner, name + suffix, args => { protect(args, 0, name); protect(args, 1, name); });
  for (const name of DESTINATION_TARGET) patch(owner, name + suffix, args => protect(args, 1, name));
  patch(owner, 'open' + suffix, args => { if (opensForWriting(args, 1)) protect(args, 0, 'open'); });
}

export function guardUserData(protect: Protect): void {
  guardFileApis(fs, '', protect);
  guardFileApis(fs, 'Sync', protect);
  guardFileApis(fsp, '', protect);
  patch(fs, 'createWriteStream', args => protect(args, 0, 'createWriteStream'));
  patch(fs, 'createReadStream', args => { if (readStreamWrites(args)) protect(args, 0, 'createReadStream'); });
}
