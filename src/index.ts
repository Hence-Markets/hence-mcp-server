#!/usr/bin/env node

import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';

type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
type JsonRecord = Record<string, JsonValue>;

interface JsonRpcRequest {
  jsonrpc?: string;
  id?: string | number | null;
  method?: string;
  params?: JsonRecord;
}

interface JsonRpcResponse {
  jsonrpc: '2.0';
  id: string | number | null;
  result?: JsonValue;
  error?: { code: number; message: string; data?: JsonValue };
}

interface ToolDef {
  name: string;
  description: string;
  inputSchema: JsonRecord;
}

interface RuntimeConfig {
  baseUrl: string;
  campaignRef?: string;
  client: string;
  transport: 'stdio' | 'http';
  host: string;
  port: number;
  authToken?: string;
}

const VERSION = '0.1.0';
const DEFAULT_PROTOCOL_VERSION = '2025-06-18';
const DEFAULT_PUBLIC_API_BASE_URL = 'https://api.hence.ai/api';
const MAX_HTTP_BODY_BYTES = 1_000_000;

function argValue(argv: string[], name: string): string | undefined {
  const flag = `--${name}`;
  const index = argv.indexOf(flag);
  if (index >= 0) return argv[index + 1];
  const inline = argv.find((item) => item.startsWith(`${flag}=`));
  return inline ? inline.slice(flag.length + 1) : undefined;
}

function hasArg(argv: string[], name: string): boolean {
  return argv.includes(`--${name}`);
}

function parsePort(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed >= 0 && parsed <= 65_535 ? parsed : fallback;
}

function parseArgs(argv: string[]): RuntimeConfig {
  const requestedTransport = argValue(argv, 'transport') || process.env.HENCE_MCP_TRANSPORT;
  const transport = requestedTransport === 'http' || hasArg(argv, 'http')
    ? 'http'
    : 'stdio';
  const baseUrl = (argValue(argv, 'base-url') || process.env.HENCE_API_BASE_URL || DEFAULT_PUBLIC_API_BASE_URL).replace(/\/+$/, '');
  return {
    baseUrl,
    campaignRef: argValue(argv, 'ref') || process.env.HENCE_CAMPAIGN_REF,
    client: argValue(argv, 'client') || process.env.HENCE_MCP_CLIENT || 'mcp',
    transport,
    host: argValue(argv, 'host') || process.env.HOST || '0.0.0.0',
    port: parsePort(argValue(argv, 'port') || process.env.PORT, 3333),
    authToken: argValue(argv, 'auth-token') || process.env.HENCE_MCP_AUTH_TOKEN,
  };
}

const config = parseArgs(process.argv.slice(2));

const instructions = [
  'Hence MCP exposes safe paper/watch-only market workflow tools.',
  'Do not place live trades or request private keys through this server.',
  'Treat data_only routes, including TradeXYZ HIP-3 markets, as market context until execution is separately verified.',
  'Use approval tools only after explicit user approval.',
].join(' ');

const tools: ToolDef[] = [
  {
    name: 'hence_start_market_workflow',
    description: 'Start a safe paper/watch-only Hence market thesis workflow from a natural-language goal.',
    inputSchema: {
      type: 'object',
      properties: {
        goal: { type: 'string', description: 'Natural language market goal or thesis request.' },
        campaign_ref: { type: 'string', description: 'Optional attribution ref, e.g. luma-seoul.' },
        mode: { type: 'string', enum: ['paper', 'watch_only'], description: 'Safe v0 mode. Live trading is blocked.' },
        risk_level: { type: 'string', enum: ['low', 'medium', 'high'], description: 'Risk framing for the paper/watch-only plan.' },
        horizon: { type: 'string', description: 'Optional horizon such as 7d, 30d, 6m.' },
        theme: { type: 'string', description: 'Optional market theme.' },
      },
      required: ['goal'],
      additionalProperties: false,
    },
  },
  {
    name: 'hence_continue_market_workflow',
    description: 'Continue a workflow after the user answers a clarification question.',
    inputSchema: {
      type: 'object',
      properties: {
        workflow_id: { type: 'string' },
        user_answer: { type: 'string' },
      },
      required: ['workflow_id', 'user_answer'],
      additionalProperties: false,
    },
  },
  {
    name: 'hence_approve_thesis',
    description: 'Approve or edit one candidate thesis and create durable Cortex ForecastTask/evidence/paper strategy records.',
    inputSchema: {
      type: 'object',
      properties: {
        workflow_id: { type: 'string' },
        selected_candidate_id: { type: 'string' },
        human_edit: { type: 'string' },
        approved_by_user: { type: 'boolean' },
      },
      required: ['workflow_id'],
      additionalProperties: false,
    },
  },
  {
    name: 'hence_get_workflow_status',
    description: 'Read workflow progress, created durable object IDs, and SmartIntent promotion blockers.',
    inputSchema: {
      type: 'object',
      properties: {
        workflow_id: { type: 'string' },
      },
      required: ['workflow_id'],
      additionalProperties: false,
    },
  },
  {
    name: 'hence_search_events',
    description: 'Search Hence Cortex event intelligence for evidence-backed market/scenario context.',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string' },
        limit: { type: 'number' },
      },
      required: ['query'],
      additionalProperties: false,
    },
  },
  {
    name: 'hence_get_market_context',
    description: 'Fetch candidate theses, evidence, and market/reduced-context status for an asset/theme.',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string' },
        limit: { type: 'number' },
      },
      required: ['query'],
      additionalProperties: false,
    },
  },
  {
    name: 'hence_start_thesis_watch',
    description: 'Record a safe follow-up watch for an approved ForecastTask. Notifications require explicit opt-in.',
    inputSchema: {
      type: 'object',
      properties: {
        workflow_id: { type: 'string' },
        forecast_task_id: { type: 'string' },
        notify: { type: 'boolean' },
        channel: { type: 'string', enum: ['telegram', 'email', 'none'] },
        horizon: { type: 'string' },
        consent_to_followup: { type: 'boolean' },
      },
      required: ['workflow_id'],
      additionalProperties: false,
    },
  },
];

function withDefaults(args: JsonRecord): JsonRecord {
  return {
    ...args,
    ...(config.campaignRef && !args.campaign_ref && !args.campaignRef ? { campaign_ref: config.campaignRef } : {}),
    client: typeof args.client === 'string' ? args.client : config.client,
  };
}

async function apiRequest(path: string, init: { method?: string; body?: JsonRecord } = {}): Promise<JsonValue> {
  const res = await fetch(`${config.baseUrl}${path}`, {
    method: init.method || 'GET',
    headers: init.body ? { 'Content-Type': 'application/json' } : undefined,
    body: init.body ? JSON.stringify(init.body) : undefined,
  });
  const text = await res.text();
  let data: JsonValue = null;
  if (text) {
    try {
      data = JSON.parse(text) as JsonValue;
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const message = typeof data === 'object' && data && !Array.isArray(data)
      ? JSON.stringify(data)
      : text || `HTTP ${res.status}`;
    throw new Error(message);
  }
  return data;
}

function stringArg(args: JsonRecord, camel: string, snake = camel): string {
  const value = args[camel] ?? args[snake];
  return typeof value === 'string' ? value : '';
}

function numberArg(args: JsonRecord, key: string, fallback: number): number {
  const value = args[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

async function callTool(name: string, rawArgs: JsonRecord = {}): Promise<JsonValue> {
  const args = withDefaults(rawArgs);
  if (name === 'hence_start_market_workflow') {
    return apiRequest('/public/hence-agent/workflows/start', { method: 'POST', body: args });
  }
  if (name === 'hence_continue_market_workflow') {
    const workflowId = stringArg(args, 'workflow_id', 'workflowId');
    return apiRequest(`/public/hence-agent/workflows/${encodeURIComponent(workflowId)}/continue`, {
      method: 'POST',
      body: { user_answer: stringArg(args, 'user_answer', 'userAnswer') },
    });
  }
  if (name === 'hence_approve_thesis') {
    const workflowId = stringArg(args, 'workflow_id', 'workflowId');
    return apiRequest(`/public/hence-agent/workflows/${encodeURIComponent(workflowId)}/approve-thesis`, {
      method: 'POST',
      body: {
        selected_candidate_id: stringArg(args, 'selected_candidate_id', 'selectedCandidateId') || undefined as unknown as JsonValue,
        human_edit: stringArg(args, 'human_edit', 'humanEdit') || undefined as unknown as JsonValue,
        approved_by_user: typeof args.approved_by_user === 'boolean' ? args.approved_by_user : true,
      } as JsonRecord,
    });
  }
  if (name === 'hence_get_workflow_status') {
    const workflowId = stringArg(args, 'workflow_id', 'workflowId');
    return apiRequest(`/public/hence-agent/workflows/${encodeURIComponent(workflowId)}/status`);
  }
  if (name === 'hence_search_events') {
    const params = new URLSearchParams({ q: stringArg(args, 'query'), limit: String(numberArg(args, 'limit', 6)) });
    return apiRequest(`/public/hence-agent/events/search?${params.toString()}`);
  }
  if (name === 'hence_get_market_context') {
    const params = new URLSearchParams({ q: stringArg(args, 'query'), limit: String(numberArg(args, 'limit', 4)) });
    return apiRequest(`/public/hence-agent/market-context?${params.toString()}`);
  }
  if (name === 'hence_start_thesis_watch') {
    return apiRequest('/public/hence-agent/thesis-watches', { method: 'POST', body: args });
  }
  throw new Error(`Unknown tool: ${name}`);
}

function makeResult(id: string | number | null | undefined, result: JsonValue): JsonRpcResponse | undefined {
  if (id === undefined) return undefined;
  return { jsonrpc: '2.0', id: id as string | number | null, result };
}

function makeError(id: string | number | null | undefined, code: number, message: string, data?: JsonValue): JsonRpcResponse | undefined {
  if (id === undefined) return undefined;
  return { jsonrpc: '2.0', id: id as string | number | null, error: { code, message, ...(data === undefined ? {} : { data }) } };
}

async function handleRpc(request: JsonRpcRequest): Promise<JsonRpcResponse | undefined> {
  const id = request.id;
  const method = request.method || '';
  if (!method) return makeError(id, -32600, 'Invalid request: missing method');
  try {
    if (method === 'initialize') {
      return makeResult(id, {
        protocolVersion: typeof request.params?.protocolVersion === 'string'
          ? request.params.protocolVersion
          : DEFAULT_PROTOCOL_VERSION,
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: '@hence-markets/mcp-server', version: VERSION },
        instructions,
      });
    }
    if (method === 'tools/list') {
      return makeResult(id, { tools: tools as unknown as JsonValue });
    }
    if (method === 'tools/call') {
      const params = request.params || {};
      const name = typeof params.name === 'string' ? params.name : '';
      const args = params.arguments && typeof params.arguments === 'object' && !Array.isArray(params.arguments)
        ? params.arguments as JsonRecord
        : {};
      const result = await callTool(name, args);
      return makeResult(id, {
        content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
        structuredContent: result,
        isError: false,
      });
    }
    if (method.startsWith('notifications/')) return undefined;
    return makeError(id, -32601, `Method not found: ${method}`);
  } catch (err) {
    return makeResult(id, {
      content: [{ type: 'text', text: err instanceof Error ? err.message : String(err) }],
      isError: true,
    });
  }
}

function writeFrame(message: JsonRecord): void {
  const payload = JSON.stringify(message);
  process.stdout.write(`Content-Length: ${Buffer.byteLength(payload, 'utf8')}\r\n\r\n${payload}`);
}

async function handleStdioRequest(request: JsonRpcRequest): Promise<void> {
  const response = await handleRpc(request);
  if (response) writeFrame(response as unknown as JsonRecord);
}

function startStdio(): void {
  let buffer = Buffer.alloc(0);
  const keepAlive = setInterval(() => undefined, 2_147_483_647);
  const exitStdio = () => {
    clearInterval(keepAlive);
    process.exit(0);
  };

  function tryParseContentLength(): boolean {
    const headerEnd = buffer.indexOf('\r\n\r\n');
    if (headerEnd < 0) return false;
    const header = buffer.subarray(0, headerEnd).toString('utf8');
    const match = header.match(/Content-Length:\s*(\d+)/i);
    if (!match) return false;
    const length = Number.parseInt(match[1], 10);
    const start = headerEnd + 4;
    const end = start + length;
    if (buffer.length < end) return false;
    const payload = buffer.subarray(start, end).toString('utf8');
    buffer = buffer.subarray(end);
    void handleStdioRequest(JSON.parse(payload) as JsonRpcRequest);
    return true;
  }

  function tryParseLine(): boolean {
    const lineEnd = buffer.indexOf('\n');
    if (lineEnd < 0) return false;
    const line = buffer.subarray(0, lineEnd).toString('utf8').trim();
    buffer = buffer.subarray(lineEnd + 1);
    if (!line) return true;
    void handleStdioRequest(JSON.parse(line) as JsonRpcRequest);
    return true;
  }

  process.stdin.on('data', (chunk: Buffer) => {
    buffer = Buffer.concat([buffer, chunk]);
    try {
      while (buffer.length > 0) {
        if (buffer.toString('utf8', 0, Math.min(buffer.length, 15)).startsWith('Content-Length')) {
          if (!tryParseContentLength()) break;
        } else if (!tryParseLine()) {
          break;
        }
      }
    } catch (err) {
      process.stderr.write(`[hence-mcp] parse error: ${err instanceof Error ? err.message : String(err)}\n`);
      buffer = Buffer.alloc(0);
    }
  });

  process.stdin.on('end', exitStdio);
  process.stdin.on('close', exitStdio);
  process.stdin.resume();
}

function setCorsHeaders(res: ServerResponse): void {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'authorization, content-type, mcp-session-id, mcp-protocol-version');
  res.setHeader('Access-Control-Expose-Headers', 'mcp-session-id, mcp-protocol-version');
}

function sendJson(res: ServerResponse, status: number, body: JsonValue): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(payload),
    'MCP-Protocol-Version': DEFAULT_PROTOCOL_VERSION,
  });
  res.end(payload);
}

function sendEmpty(res: ServerResponse, status: number): void {
  res.writeHead(status, { 'MCP-Protocol-Version': DEFAULT_PROTOCOL_VERSION });
  res.end();
}

function isAuthorized(req: IncomingMessage): boolean {
  if (!config.authToken) return true;
  const header = req.headers.authorization || '';
  return header === `Bearer ${config.authToken}`;
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let total = 0;
    req.on('data', (chunk: Buffer) => {
      total += chunk.length;
      if (total > MAX_HTTP_BODY_BYTES) {
        reject(new Error('Request body too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

async function handleHttpRequest(req: IncomingMessage, res: ServerResponse): Promise<void> {
  setCorsHeaders(res);

  if (req.method === 'OPTIONS') {
    sendEmpty(res, 204);
    return;
  }

  const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
  if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/health')) {
    sendJson(res, 200, {
      ok: true,
      name: '@hence-markets/mcp-server',
      version: VERSION,
      transport: 'http',
      endpoint: '/mcp',
      tools: tools.map((tool) => tool.name),
    });
    return;
  }

  if (url.pathname !== '/mcp') {
    sendJson(res, 404, { ok: false, error: 'not_found' });
    return;
  }

  if (!isAuthorized(req)) {
    sendJson(res, 401, { ok: false, error: 'unauthorized' });
    return;
  }

  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST, OPTIONS');
    sendJson(res, 405, { ok: false, error: 'method_not_allowed' });
    return;
  }

  try {
    const body = await readBody(req);
    const input = JSON.parse(body) as JsonRpcRequest | JsonRpcRequest[];
    if (Array.isArray(input)) {
      const responses = (await Promise.all(input.map((item) => handleRpc(item)))).filter(Boolean) as JsonRpcResponse[];
      if (responses.length === 0) {
        sendEmpty(res, 202);
        return;
      }
      sendJson(res, 200, responses as unknown as JsonValue);
      return;
    }
    const response = await handleRpc(input);
    if (!response) {
      sendEmpty(res, 202);
      return;
    }
    sendJson(res, 200, response as unknown as JsonValue);
  } catch (err) {
    const response = makeError(null, -32700, err instanceof Error ? err.message : String(err));
    sendJson(res, 400, response as unknown as JsonValue);
  }
}

function startHttp(): void {
  const server = createServer((req, res) => {
    void handleHttpRequest(req, res).catch((err) => {
      setCorsHeaders(res);
      sendJson(res, 500, { ok: false, error: err instanceof Error ? err.message : String(err) });
    });
  });
  server.on('error', (err) => {
    process.stderr.write(`[hence-mcp] server error: ${err instanceof Error ? err.message : String(err)}\n`);
    process.exitCode = 1;
  });
  server.listen(config.port, config.host, () => {
    const address = server.address();
    const port = typeof address === 'object' && address ? address.port : config.port;
    process.stderr.write(`[hence-mcp] listening on http://${config.host}:${port}/mcp\n`);
  });
}

if (config.transport === 'http') {
  startHttp();
} else {
  startStdio();
}
