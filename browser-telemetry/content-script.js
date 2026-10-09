(() => {
  'use strict';

  const REPORT_INTERVAL_MS = 1200;
  const STABLE_AFTER_MS = 2600;
  const SCAN_THROTTLE_MS = 120;
  const ASSISTANT_SELECTOR = '[data-message-author-role="assistant"]';

  let currentNode = null;
  let lastEstimatedTokens = 0;
  let lastReportAt = 0;
  let stableTimer = null;
  let scanTimer = null;

  function estimateTokens(text) {
    const value = String(text || '').trim();
    if (!value) return 0;
    const cjkMatches = value.match(/[\u3400-\u9fff\uf900-\ufaff]/g);
    const cjk = cjkMatches ? cjkMatches.length : 0;
    const nonCjkChars = Math.max(0, value.replace(/[\u3400-\u9fff\uf900-\ufaff]/g, '').replace(/\s+/g, '').length);
    return Math.max(1, Math.round(cjk + nonCjkChars / 4));
  }

  function textTokens(node) {
    return estimateTokens(node && (node.innerText || node.textContent || ''));
  }

  function sendDelta(node, force = false) {
    if (!node || node !== currentNode) return;
    const now = Date.now();
    const estimated = textTokens(node);
    const delta = Math.max(0, estimated - lastEstimatedTokens);
    const duration = Math.max(1, now - lastReportAt);

    if (delta <= 0) return;
    if (!force && duration < REPORT_INTERVAL_MS) return;

    lastEstimatedTokens = Math.max(lastEstimatedTokens, estimated);
    lastReportAt = now;
    chrome.runtime.sendMessage({
      type: 'ai-output-delta',
      output_tokens: delta,
      duration_ms: duration,
      source: 'chatgpt-browser-estimate',
      model: 'ChatGPT Web (estimated)'
    }).catch(() => {});
  }

  function armStableTimer(node) {
    if (stableTimer) clearTimeout(stableTimer);
    stableTimer = setTimeout(() => sendDelta(node, true), STABLE_AFTER_MS);
  }

  function handleAssistantNode(node) {
    if (!node) return;
    if (node !== currentNode) {
      if (currentNode) sendDelta(currentNode, true);
      currentNode = node;
      // Treat the text already present when a message node first appears as the
      // baseline. This prevents page refresh/navigation from re-reporting old
      // conversation text as fresh output. Only later growth is measured.
      lastEstimatedTokens = textTokens(node);
      lastReportAt = Date.now();
      armStableTimer(node);
      return;
    }
    sendDelta(node, false);
    armStableTimer(node);
  }

  function scan() {
    scanTimer = null;
    const nodes = document.querySelectorAll(ASSISTANT_SELECTOR);
    if (!nodes.length) return;
    const node = nodes[nodes.length - 1];
    if ((node.innerText || node.textContent || '').trim()) handleAssistantNode(node);
  }

  function scheduleScan() {
    if (scanTimer) return;
    scanTimer = setTimeout(scan, SCAN_THROTTLE_MS);
  }

  const observer = new MutationObserver(scheduleScan);
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    characterData: true
  });

  scheduleScan();
})();
