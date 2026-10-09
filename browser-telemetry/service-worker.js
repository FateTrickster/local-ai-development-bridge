const PORTS = Array.from({ length: 11 }, (_, i) => 8766 + i);
let cachedBase = null;
let cachedToken = null;

async function discoverBridge() {
  if (cachedBase && cachedToken) return { base: cachedBase, token: cachedToken };
  for (const port of PORTS) {
    const base = `http://127.0.0.1:${port}`;
    try {
      const response = await fetch(`${base}/api/session`, { cache: 'no-store' });
      if (!response.ok) continue;
      const session = await response.json();
      if (!session.telemetry_token) continue;
      cachedBase = base;
      cachedToken = session.telemetry_token;
      return { base, token: cachedToken };
    } catch (_) {
      // Try the next localhost dashboard port.
    }
  }
  cachedBase = null;
  cachedToken = null;
  throw new Error('LOCAL_AI_BRIDGE_DASHBOARD_NOT_FOUND');
}

async function postTelemetry(message) {
  const { base, token } = await discoverBridge();
  const payload = {
    output_tokens: Math.max(0, Math.round(Number(message.output_tokens) || 0)),
    duration_ms: Math.max(1, Math.round(Number(message.duration_ms) || 1)),
    source: String(message.source || 'chatgpt-browser-estimate'),
    model: String(message.model || 'ChatGPT Web (estimated)')
  };
  if (!payload.output_tokens) return;

  let response;
  try {
    response = await fetch(`${base}/api/telemetry/ai-output`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Bridge-Telemetry-Token': token
      },
      body: JSON.stringify(payload)
    });
  } catch (error) {
    cachedBase = null;
    cachedToken = null;
    throw error;
  }

  if (response.status === 403) {
    cachedToken = null;
    const refreshed = await discoverBridge();
    response = await fetch(`${refreshed.base}/api/telemetry/ai-output`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Bridge-Telemetry-Token': refreshed.token
      },
      body: JSON.stringify(payload)
    });
  }
  if (!response.ok) throw new Error(`TELEMETRY_HTTP_${response.status}`);
}

chrome.runtime.onMessage.addListener((message) => {
  if (!message || message.type !== 'ai-output-delta') return;
  postTelemetry(message).catch(() => {
    // Telemetry is best-effort and must never interfere with ChatGPT Web.
  });
});
