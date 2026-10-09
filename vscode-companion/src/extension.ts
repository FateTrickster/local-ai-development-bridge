import * as crypto from 'crypto';
import * as fs from 'fs';
import * as http from 'http';
import * as os from 'os';
import * as path from 'path';
import * as vscode from 'vscode';

const MAX_BODY_BYTES = 1024 * 1024;
const DEFAULT_PORT = 8765;
let server: http.Server | undefined;
let activePort: number | undefined;
let connectionFile: string | undefined;

function dataDir(): string {
  if (process.platform === 'win32') {
    const base = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
    return path.join(base, 'LocalAIDevelopmentBridge');
  }
  const base = process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share');
  return path.join(base, 'LocalAIDevelopmentBridge');
}

function ensureToken(): string {
  const dir = dataDir();
  fs.mkdirSync(dir, { recursive: true });
  const tokenPath = path.join(dir, 'vscode-token.txt');
  try {
    const current = fs.readFileSync(tokenPath, 'utf8').trim();
    if (current) {
      return current;
    }
  } catch {
    // Generate below.
  }
  const token = crypto.randomBytes(32).toString('base64url');
  const tmp = `${tokenPath}.tmp`;
  fs.writeFileSync(tmp, token, { encoding: 'utf8', mode: 0o600 });
  fs.renameSync(tmp, tokenPath);
  return token;
}

function tokenMatches(candidate: string | undefined, token: string): boolean {
  if (!candidate) {
    return false;
  }
  const a = Buffer.from(candidate, 'utf8');
  const b = Buffer.from(token, 'utf8');
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function sendJson(res: http.ServerResponse, status: number, payload: unknown): void {
  const body = Buffer.from(JSON.stringify(payload), 'utf8');
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': body.length,
    'cache-control': 'no-store',
  });
  res.end(body);
}

async function readJson(req: http.IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const raw of req) {
    const chunk = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
    total += chunk.length;
    if (total > MAX_BODY_BYTES) {
      throw new Error('REQUEST_TOO_LARGE');
    }
    chunks.push(chunk);
  }
  if (chunks.length === 0) {
    return {};
  }
  const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('INVALID_JSON_OBJECT');
  }
  return parsed as Record<string, unknown>;
}

function workspaceRoots(): string[] {
  return (vscode.workspace.workspaceFolders || []).map((folder) => path.resolve(folder.uri.fsPath));
}

function pathWithinRoot(target: string, root: string): boolean {
  const resolvedTarget = path.resolve(target);
  const resolvedRoot = path.resolve(root);
  if (process.platform === 'win32') {
    const t = resolvedTarget.toLowerCase();
    const r = resolvedRoot.toLowerCase();
    return t === r || t.startsWith(r + path.sep);
  }
  return resolvedTarget === resolvedRoot || resolvedTarget.startsWith(resolvedRoot + path.sep);
}

function resolveWorkspaceUri(inputPath: string): vscode.Uri {
  const roots = workspaceRoots();
  if (roots.length === 0) {
    throw new Error('NO_WORKSPACE_OPEN');
  }
  if (path.isAbsolute(inputPath)) {
    const resolved = path.resolve(inputPath);
    if (!roots.some((root) => pathWithinRoot(resolved, root))) {
      throw new Error('PATH_OUTSIDE_VSCODE_WORKSPACE');
    }
    return vscode.Uri.file(resolved);
  }
  return vscode.Uri.file(path.resolve(roots[0], inputPath));
}

function isWorkspaceUri(uri: vscode.Uri): boolean {
  if (uri.scheme !== 'file') {
    return false;
  }
  return workspaceRoots().some((root) => pathWithinRoot(uri.fsPath, root));
}

function openDocumentState(uri: vscode.Uri): { version: number | null; dirty: boolean; languageId: string | null } {
  const target = path.resolve(uri.fsPath);
  const doc = vscode.workspace.textDocuments.find((candidate) => {
    if (candidate.uri.scheme !== 'file') {
      return false;
    }
    const candidatePath = path.resolve(candidate.uri.fsPath);
    return process.platform === 'win32'
      ? candidatePath.toLowerCase() === target.toLowerCase()
      : candidatePath === target;
  });
  return doc
    ? { version: doc.version, dirty: doc.isDirty, languageId: doc.languageId }
    : { version: null, dirty: false, languageId: null };
}

function severityName(severity: vscode.DiagnosticSeverity): string {
  switch (severity) {
    case vscode.DiagnosticSeverity.Error:
      return 'error';
    case vscode.DiagnosticSeverity.Warning:
      return 'warning';
    case vscode.DiagnosticSeverity.Information:
      return 'information';
    case vscode.DiagnosticSeverity.Hint:
      return 'hint';
    default:
      return 'information';
  }
}

function serializeRange(range: vscode.Range): Record<string, number> {
  return {
    start_line: range.start.line + 1,
    start_column: range.start.character + 1,
    end_line: range.end.line + 1,
    end_column: range.end.character + 1,
  };
}

function serializeLocation(value: vscode.Location | vscode.LocationLink): Record<string, unknown> {
  if ('targetUri' in value) {
    return {
      path: value.targetUri.fsPath,
      target_range: serializeRange(value.targetRange),
      target_selection_range: value.targetSelectionRange ? serializeRange(value.targetSelectionRange) : null,
      origin_selection_range: value.originSelectionRange ? serializeRange(value.originSelectionRange) : null,
    };
  }
  return { path: value.uri.fsPath, range: serializeRange(value.range) };
}

async function handleDiagnostics(body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const maxResults = Math.max(1, Math.min(Number(body.max_results ?? 100), 500));
  const requested = Array.isArray(body.severity)
    ? new Set(body.severity.map((item) => String(item)))
    : null;
  let pairs: [vscode.Uri, readonly vscode.Diagnostic[]][];
  if (typeof body.path === 'string' && body.path) {
    const uri = resolveWorkspaceUri(body.path);
    pairs = [[uri, vscode.languages.getDiagnostics(uri)]];
  } else {
    pairs = vscode.languages.getDiagnostics().filter(([uri]) => isWorkspaceUri(uri));
  }

  const results: Record<string, unknown>[] = [];
  for (const [uri, diagnostics] of pairs) {
    const state = openDocumentState(uri);
    for (const diagnostic of diagnostics) {
      const severity = severityName(diagnostic.severity);
      if (requested && !requested.has(severity)) {
        continue;
      }
      results.push({
        path: uri.fsPath,
        severity,
        message: diagnostic.message,
        source: diagnostic.source ?? null,
        code: typeof diagnostic.code === 'object' ? diagnostic.code.value : diagnostic.code ?? null,
        range: serializeRange(diagnostic.range),
        document_version: state.version,
        document_dirty: state.dirty,
        language_id: state.languageId,
      });
      if (results.length >= maxResults) {
        return {
          provider_state: 'ready',
          provider_state_reason: 'vscode.languages.getDiagnostics completed',
          results,
          truncated: true,
          total_matching: results.length,
        };
      }
    }
  }
  return {
    provider_state: 'ready',
    provider_state_reason: 'vscode.languages.getDiagnostics completed',
    results,
    truncated: false,
    total_matching: results.length,
  };
}

function serializeSymbol(symbol: vscode.DocumentSymbol | vscode.SymbolInformation): Record<string, unknown> {
  if ('location' in symbol) {
    return {
      name: symbol.name,
      kind: vscode.SymbolKind[symbol.kind],
      container_name: symbol.containerName,
      location: serializeLocation(symbol.location),
    };
  }
  return {
    name: symbol.name,
    detail: symbol.detail,
    kind: vscode.SymbolKind[symbol.kind],
    range: serializeRange(symbol.range),
    selection_range: serializeRange(symbol.selectionRange),
  };
}

function flattenDocumentSymbols(symbols: vscode.DocumentSymbol[], maxResults: number): Record<string, unknown>[] {
  const output: Record<string, unknown>[] = [];
  const visit = (items: vscode.DocumentSymbol[], parent: string | null) => {
    for (const symbol of items) {
      output.push({ ...serializeSymbol(symbol), parent });
      if (output.length >= maxResults) {
        return;
      }
      visit(symbol.children, symbol.name);
      if (output.length >= maxResults) {
        return;
      }
    }
  };
  visit(symbols, null);
  return output;
}

const LSP_QUERY_TIMEOUT_MS = 3000;
const LSP_WARMUP_RETRY_MS = 350;

class LspProviderTimeoutError extends Error {
  constructor(public readonly operation: string) {
    super(`LSP_PROVIDER_TIMEOUT: ${operation}`);
    this.name = 'LspProviderTimeoutError';
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function executeProvider<T>(operation: string, command: string, ...args: unknown[]): Promise<T | undefined> {
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      vscode.commands.executeCommand<T>(command, ...args),
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new LspProviderTimeoutError(operation)), LSP_QUERY_TIMEOUT_MS);
      }),
    ]);
  } finally {
    if (timer) {
      clearTimeout(timer);
    }
  }
}

function semanticResult(
  operation: string,
  rawAvailable: boolean,
  results: Record<string, unknown>[],
  truncated: boolean,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  if (!rawAvailable) {
    return {
      provider_state: 'ready',
      provider_state_reason: 'VS Code Companion reachable; execute-provider command returned no provider response',
      semantic_state: 'PROVIDER_NOT_AVAILABLE',
      semantic_result_inconclusive: true,
      operation,
      results: [],
      truncated: false,
      ...extra,
    };
  }
  const hasResults = results.length > 0;
  return {
    provider_state: 'ready',
    provider_state_reason: hasResults
      ? 'Provider query completed with results'
      : 'Provider query completed successfully with an empty result set',
    semantic_state: hasResults ? 'READY_WITH_RESULTS' : 'READY_EMPTY',
    semantic_result_inconclusive: false,
    operation,
    results,
    truncated,
    ...extra,
  };
}

function lspErrorResult(operation: string, error: unknown): Record<string, unknown> {
  const message = error instanceof Error ? error.message : String(error);
  let semanticState = 'PROVIDER_ERROR';
  if (error instanceof LspProviderTimeoutError || message.startsWith('LSP_PROVIDER_TIMEOUT:')) {
    semanticState = 'TIMEOUT';
  } else if (message === 'PATH_OUTSIDE_VSCODE_WORKSPACE' || message === 'NO_WORKSPACE_OPEN') {
    semanticState = 'WORKSPACE_MISMATCH';
  } else if (message === 'PATH_REQUIRED') {
    semanticState = 'INVALID_REQUEST';
  }
  return {
    provider_state: 'not_ready',
    provider_state_reason: message,
    semantic_state: semanticState,
    semantic_result_inconclusive: true,
    operation,
    results: [],
    truncated: false,
  };
}

async function queryDocumentProvider<T>(
  operation: string,
  command: string,
  uri: vscode.Uri,
  args: unknown[],
): Promise<{ raw: T | undefined; metadata: Record<string, unknown> }> {
  const before = openDocumentState(uri);
  const documentState = before.version == null ? 'DOCUMENT_NOT_OPEN' : 'DOCUMENT_OPEN';
  let openedForQuery = false;
  let languageId = before.languageId;

  if (before.version == null) {
    const opened = await vscode.workspace.openTextDocument(uri);
    openedForQuery = true;
    languageId = opened.languageId;
  }

  let raw = await executeProvider<T>(operation, command, ...args);
  let warmupRetry = false;
  let initialSemanticState: string | null = null;
  if (raw == null && openedForQuery) {
    // Opening a document can activate a language extension asynchronously. One short
    // retry makes that transient visible instead of silently returning an empty list.
    initialSemanticState = 'LANGUAGE_SERVER_LOADING';
    warmupRetry = true;
    await delay(LSP_WARMUP_RETRY_MS);
    raw = await executeProvider<T>(operation, command, ...args);
  }

  return {
    raw,
    metadata: {
      document_state: documentState,
      document_opened_for_query: openedForQuery,
      language_id: languageId,
      warmup_retry_attempted: warmupRetry,
      initial_semantic_state: initialSemanticState,
    },
  };
}

async function handleLsp(body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const operation = String(body.operation ?? '');
  const maxResults = Math.max(1, Math.min(Number(body.max_results ?? 100), 500));
  try {
    if (operation === 'workspace_symbols') {
      const query = String(body.query ?? '');
      const raw = await executeProvider<vscode.SymbolInformation[]>(
        operation,
        'vscode.executeWorkspaceSymbolProvider',
        query,
      );
      const symbols = raw ?? [];
      const results = symbols.slice(0, maxResults).map(serializeSymbol);
      return semanticResult(operation, raw != null, results, symbols.length > maxResults, {
        query,
        document_state: null,
      });
    }

    if (typeof body.path !== 'string' || !body.path) {
      throw new Error('PATH_REQUIRED');
    }
    const uri = resolveWorkspaceUri(body.path);

    if (operation === 'document_symbols') {
      const queried = await queryDocumentProvider<(vscode.DocumentSymbol | vscode.SymbolInformation)[]>(
        operation,
        'vscode.executeDocumentSymbolProvider',
        uri,
        [uri],
      );
      const symbols = queried.raw ?? [];
      const results = symbols.length > 0 && symbols[0] instanceof vscode.DocumentSymbol
        ? flattenDocumentSymbols(symbols as vscode.DocumentSymbol[], maxResults)
        : (symbols as vscode.SymbolInformation[]).slice(0, maxResults).map(serializeSymbol);
      return semanticResult(operation, queried.raw != null, results, symbols.length > maxResults, queried.metadata);
    }

    const line = Math.max(1, Number(body.line ?? 1));
    const column = Math.max(1, Number(body.column ?? 1));
    const position = new vscode.Position(line - 1, column - 1);

    if (operation === 'definition') {
      const queried = await queryDocumentProvider<(vscode.Location | vscode.LocationLink)[]>(
        operation,
        'vscode.executeDefinitionProvider',
        uri,
        [uri, position],
      );
      const locations = queried.raw ?? [];
      const results = locations.slice(0, maxResults).map(serializeLocation);
      return semanticResult(operation, queried.raw != null, results, locations.length > maxResults, queried.metadata);
    }
    if (operation === 'references') {
      const queried = await queryDocumentProvider<vscode.Location[]>(
        operation,
        'vscode.executeReferenceProvider',
        uri,
        [uri, position],
      );
      const locations = queried.raw ?? [];
      const includeDeclaration = body.include_declaration !== false;
      let filtered = locations;
      if (!includeDeclaration) {
        filtered = locations.filter((location) => !(location.uri.toString() === uri.toString() && location.range.contains(position)));
      }
      const results = filtered.slice(0, maxResults).map(serializeLocation);
      return semanticResult(operation, queried.raw != null, results, filtered.length > maxResults, queried.metadata);
    }
    if (operation === 'implementation') {
      const queried = await queryDocumentProvider<(vscode.Location | vscode.LocationLink)[]>(
        operation,
        'vscode.executeImplementationProvider',
        uri,
        [uri, position],
      );
      const locations = queried.raw ?? [];
      const results = locations.slice(0, maxResults).map(serializeLocation);
      return semanticResult(operation, queried.raw != null, results, locations.length > maxResults, queried.metadata);
    }
    if (operation === 'hover') {
      const queried = await queryDocumentProvider<vscode.Hover[]>(
        operation,
        'vscode.executeHoverProvider',
        uri,
        [uri, position],
      );
      const hovers = queried.raw ?? [];
      const results = hovers.slice(0, maxResults).map((hover) => ({
        range: hover.range ? serializeRange(hover.range) : null,
        contents: hover.contents.map((content) => {
          if (typeof content === 'string') {
            return content;
          }
          if (content instanceof vscode.MarkdownString) {
            return content.value;
          }
          return `${content.language}: ${content.value}`;
        }),
      }));
      return semanticResult(operation, queried.raw != null, results, hovers.length > maxResults, queried.metadata);
    }
    throw new Error(`UNKNOWN_LSP_OPERATION: ${operation}`);
  } catch (error) {
    return lspErrorResult(operation, error);
  }
}

async function handleDocument(body: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (typeof body.path !== 'string' || !body.path) {
    throw new Error('PATH_REQUIRED');
  }
  const uri = resolveWorkspaceUri(body.path);
  let document = vscode.workspace.textDocuments.find((candidate) => candidate.uri.toString() === uri.toString());
  if (!document) {
    document = await vscode.workspace.openTextDocument(uri);
  }
  const text = document.getText();
  const lines = text.split(/\r?\n/);
  const startLine = Math.max(1, Number(body.start_line ?? 1));
  const requestedEnd = body.end_line == null ? lines.length : Math.max(startLine, Number(body.end_line));
  const endLine = Math.min(lines.length, requestedEnd);
  const selected = lines.slice(startLine - 1, endLine).join('\n');
  const maxBytes = Math.max(1024, Math.min(Number(body.max_bytes ?? 262144), 4 * 1024 * 1024));
  const bytes = Buffer.from(selected, 'utf8');
  const bounded = bytes.length > maxBytes ? bytes.subarray(0, maxBytes).toString('utf8') : selected;
  return {
    provider_state: 'ready',
    path: uri.fsPath,
    language_id: document.languageId,
    document_version: document.version,
    document_dirty: document.isDirty,
    total_lines: lines.length,
    actual_start_line: startLine,
    actual_end_line: startLine - 1 + bounded.split(/\r?\n/).length,
    truncated: bytes.length > maxBytes || endLine < lines.length,
    content: bounded,
  };
}

function healthPayload(): Record<string, unknown> {
  return {
    ready: true,
    pid: process.pid,
    port: activePort,
    semantic_contract_version: 2,
    lsp_query_timeout_ms: LSP_QUERY_TIMEOUT_MS,
    lsp_warmup_retry_ms: LSP_WARMUP_RETRY_MS,
    semantic_states: [
      'READY_WITH_RESULTS',
      'READY_EMPTY',
      'PROVIDER_NOT_AVAILABLE',
      'LANGUAGE_SERVER_LOADING',
      'WORKSPACE_MISMATCH',
      'TIMEOUT',
      'INVALID_REQUEST',
      'PROVIDER_ERROR',
    ],
    document_states: ['DOCUMENT_OPEN', 'DOCUMENT_NOT_OPEN'],
    workspace_folders: workspaceRoots(),
    open_documents: vscode.workspace.textDocuments.map((doc) => ({
      path: doc.uri.scheme === 'file' ? doc.uri.fsPath : doc.uri.toString(),
      language_id: doc.languageId,
      version: doc.version,
      dirty: doc.isDirty,
    })),
  };
}

function createServer(token: string): http.Server {
  return http.createServer(async (req, res) => {
    try {
      const remote = req.socket.remoteAddress;
      if (remote !== '127.0.0.1' && remote !== '::1' && remote !== '::ffff:127.0.0.1') {
        sendJson(res, 403, { error: 'LOCALHOST_ONLY' });
        return;
      }
      const supplied = req.headers['x-local-ai-bridge-token'];
      const candidate = Array.isArray(supplied) ? supplied[0] : supplied;
      if (!tokenMatches(candidate, token)) {
        sendJson(res, 404, { error: 'not_found' });
        return;
      }
      const requestUrl = new URL(req.url || '/', 'http://127.0.0.1');
      if (req.method === 'GET' && requestUrl.pathname === '/health') {
        sendJson(res, 200, healthPayload());
        return;
      }
      if (req.method !== 'POST') {
        sendJson(res, 405, { error: 'METHOD_NOT_ALLOWED' });
        return;
      }
      const body = await readJson(req);
      if (requestUrl.pathname === '/diagnostics') {
        sendJson(res, 200, await handleDiagnostics(body));
        return;
      }
      if (requestUrl.pathname === '/lsp') {
        sendJson(res, 200, await handleLsp(body));
        return;
      }
      if (requestUrl.pathname === '/document') {
        sendJson(res, 200, await handleDocument(body));
        return;
      }
      sendJson(res, 404, { error: 'not_found' });
    } catch (error) {
      sendJson(res, 400, { error: error instanceof Error ? error.message : String(error) });
    }
  });
}

async function listen(serverInstance: http.Server, port: number): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const onError = (error: NodeJS.ErrnoException) => {
      serverInstance.off('listening', onListening);
      reject(error);
    };
    const onListening = () => {
      serverInstance.off('error', onError);
      resolve();
    };
    serverInstance.once('error', onError);
    serverInstance.once('listening', onListening);
    serverInstance.listen(port, '127.0.0.1');
  });
}

async function startCompanion(context: vscode.ExtensionContext): Promise<void> {
  const token = ensureToken();
  const preferred = vscode.workspace.getConfiguration('localAiBridge').get<number>('companionPort', DEFAULT_PORT);
  let lastError: unknown;
  for (let port = preferred; port <= preferred + 10; port += 1) {
    const candidate = createServer(token);
    try {
      await listen(candidate, port);
      server = candidate;
      activePort = port;
      break;
    } catch (error) {
      lastError = error;
      candidate.close();
    }
  }
  if (!server || activePort == null) {
    throw lastError instanceof Error ? lastError : new Error('NO_AVAILABLE_COMPANION_PORT');
  }

  const dir = dataDir();
  fs.mkdirSync(dir, { recursive: true });
  connectionFile = path.join(dir, 'vscode-companion.json');
  fs.writeFileSync(
    connectionFile,
    JSON.stringify({ port: activePort, pid: process.pid, started_at: new Date().toISOString() }, null, 2),
    'utf8',
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('localAiBridge.showCompanionStatus', () => {
      vscode.window.showInformationMessage(
        `Local AI Bridge Companion: ready on 127.0.0.1:${activePort}; workspace folders: ${workspaceRoots().length}`,
      );
    }),
    new vscode.Disposable(() => {
      server?.close();
      if (connectionFile) {
        try {
          const info = JSON.parse(fs.readFileSync(connectionFile, 'utf8')) as { pid?: number };
          if (info.pid === process.pid) {
            fs.unlinkSync(connectionFile);
          }
        } catch {
          // Ignore stale/missing state.
        }
      }
    }),
  );
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  try {
    await startCompanion(context);
    console.log(`Local AI Development Bridge Companion listening on 127.0.0.1:${activePort}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(`Local AI Development Bridge Companion failed: ${message}`);
    vscode.window.showErrorMessage(`Local AI Bridge Companion failed to start: ${message}`);
  }
}

export function deactivate(): void {
  server?.close();
}
