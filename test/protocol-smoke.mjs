import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { spawn } from 'node:child_process';

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

function waitForExit(child, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      child.kill();
      reject(new Error('child process timed out'));
    }, timeoutMs);
    child.once('close', (code, signal) => {
      clearTimeout(timeout);
      resolve({ code, signal });
    });
    child.once('error', (err) => {
      clearTimeout(timeout);
      reject(err);
    });
  });
}

async function smokeStdio() {
  const stdinPayload = [
    frame({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18' } }),
    frame({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} }),
  ].join('');
  const child = spawn(process.execPath, [entry, '--base-url', 'http://127.0.0.1:1/api'], {
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stdout = Buffer.alloc(0);
  let stderr = Buffer.alloc(0);
  child.stdout.on('data', (chunk) => {
    stdout = Buffer.concat([stdout, chunk]);
  });
  child.stderr.on('data', (chunk) => {
    stderr = Buffer.concat([stderr, chunk]);
  });
  child.stdin.end(stdinPayload);
  const { code, signal } = await waitForExit(child);
  assert.equal(code, 0, `stdio server exited with code ${code} signal ${signal}: ${stderr.toString('utf8')}`);
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
