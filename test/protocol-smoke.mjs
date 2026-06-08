import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { execFile, spawn } from 'node:child_process';

const entry = new URL('../dist/index.js', import.meta.url).pathname;

function freePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
  });
}

function frame(message) {
  const payload = JSON.stringify(message);
  return `Content-Length: ${Buffer.byteLength(payload)}\r\n\r\n${payload}`;
}

function parseFrames(output) {
  let buffer = Buffer.alloc(0);
  const frames = [];
  const push = (chunk) => {
    buffer = Buffer.concat([buffer, chunk]);
    while (true) {
      const headerEnd = buffer.indexOf('\r\n\r\n');
      if (headerEnd < 0) return frames;
      const header = buffer.subarray(0, headerEnd).toString('utf8');
      const match = header.match(/Content-Length:\s*(\d+)/i);
      if (!match) return frames;
      const length = Number.parseInt(match[1], 10);
      const start = headerEnd + 4;
      const end = start + length;
      if (buffer.length < end) return frames;
      frames.push(JSON.parse(buffer.subarray(start, end).toString('utf8')));
      buffer = buffer.subarray(end);
    }
  };
  push(Buffer.from(output));
  return frames;
}

function execShell(command, options = {}) {
  return new Promise((resolve, reject) => {
    execFile('/bin/sh', ['-c', command], { maxBuffer: 2_000_000, ...options }, (error, stdout, stderr) => {
      if (error) {
        error.stdout = stdout;
        error.stderr = stderr;
        reject(error);
        return;
      }
      resolve({ stdout, stderr });
    });
  });
}

async function smokeStdio() {
  const stdinPayload = [
    frame({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18' } }),
    frame({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} }),
  ].join('');
  const encoded = Buffer.from(stdinPayload).toString('base64');
  const { stdout } = await execShell(`printf %s '${encoded}' | base64 -d | ${process.execPath} '${entry}' --base-url http://127.0.0.1:1/api`);
  const responses = parseFrames(stdout);
  assert.equal(responses[0].id, 1);
  assert.equal(responses[0].result.serverInfo.name, '@hence-markets/mcp-server');
  assert.equal(responses[1].id, 2);
  assert.ok(responses[1].result.tools.some((tool) => tool.name === 'hence_start_market_workflow'));
}

async function waitForHttp(port) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/health`);
      if (res.ok) return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw new Error('HTTP server did not start');
}

async function smokeHttp() {
  const port = await freePort();
  const child = spawn(process.execPath, [entry, '--http', '--port', String(port), '--host', '127.0.0.1'], {
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  try {
    await waitForHttp(port);
    const initialize = await fetch(`http://127.0.0.1:${port}/mcp`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 'init', method: 'initialize', params: {} }),
    }).then((res) => res.json());
    assert.equal(initialize.id, 'init');
    assert.equal(initialize.result.serverInfo.name, '@hence-markets/mcp-server');

    const list = await fetch(`http://127.0.0.1:${port}/mcp`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 'tools', method: 'tools/list', params: {} }),
    }).then((res) => res.json());
    assert.ok(list.result.tools.some((tool) => tool.name === 'hence_get_market_context'));
  } finally {
    child.kill();
    await Promise.race([once(child, 'exit'), new Promise((resolve) => setTimeout(resolve, 500))]);
  }
}

await smokeStdio();
await smokeHttp();
console.log('protocol smoke ok');
