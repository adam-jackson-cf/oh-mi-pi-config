import childProcess from 'node:child_process';
import dgram from 'node:dgram';
import http from 'node:http';
import http2 from 'node:http2';
import https from 'node:https';
import net from 'node:net';
import tls from 'node:tls';
import workerThreads from 'node:worker_threads';
import { patch, type GuardCheck } from './patching.ts';

export type Deny = (rule: string, operation: string) => never;

function blockAll<Owner>(owner: Owner, names: string[], deny: Deny): void {
  for (const name of names) patch(owner, name, () => deny('TEST.fast-tier-isolation', name));
}

const PROCESS_STARTERS = ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork'];

export function guardFastTier(deny: Deny): void {
  const block = (operation: string): GuardCheck => () => deny('TEST.fast-tier-isolation', operation);
  for (const name of PROCESS_STARTERS) patch(childProcess, name, block(`child_process.${name}`));
  patch(childProcess.ChildProcess.prototype, 'spawn', block('ChildProcess.spawn'));
  if (globalThis.Bun) {
    for (const name of ['spawn', 'spawnSync']) patch(globalThis.Bun, name, block(`Bun.${name}`));
  }
  blockAll(net, ['connect', 'createConnection'], deny);
  blockAll(net.Socket.prototype, ['connect'], deny);
  blockAll(net.Server.prototype, ['listen'], deny);
  blockAll(tls, ['connect'], deny);
  blockAll(dgram.Socket.prototype, ['connect', 'send', 'bind'], deny);
  blockAll(http, ['request', 'get'], deny);
  blockAll(https, ['request', 'get'], deny);
  blockAll(http2, ['connect'], deny);
  blockAll(globalThis, ['fetch', 'WebSocket'], deny);
  Object.defineProperty(workerThreads, 'Worker', {
    value: function GuardedWorker() {
      deny('TEST.fast-tier-isolation', 'worker_threads.Worker');
    },
  });
}
