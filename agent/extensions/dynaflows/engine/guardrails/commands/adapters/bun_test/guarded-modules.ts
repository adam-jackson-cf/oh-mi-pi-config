import childProcess from 'node:child_process';
import dgram from 'node:dgram';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import http from 'node:http';
import http2 from 'node:http2';
import https from 'node:https';
import net from 'node:net';
import tls from 'node:tls';
import workerThreads from 'node:worker_threads';

export const GUARDED_MODULES = {
  child_process: childProcess, dgram, fs, 'fs/promises': fsp, http, http2, https, net, tls,
  worker_threads: workerThreads,
};
