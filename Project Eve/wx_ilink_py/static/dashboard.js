/* dashboard.js — 实时轮询 /api/status 并更新页面状态 */

const POLL_INTERVAL = 3000; // ms
const PIPELINE_POLL_INTERVAL = 800; // ms — 更快轮询以实时反映执行中状态

// ---------------------------------------------------------------------------
// Status polling
// ---------------------------------------------------------------------------

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val ?? '-';
}

function updateStatus(s) {
  // Hero pill
  const pill = document.getElementById('login-pill');
  if (pill) {
    pill.textContent = s.logged_in ? '已登录' : '等待登录';
    pill.className = 'pill' + (s.logged_in ? '' : ' off');
  }

  // Grid cards
  setText('val-account-id', s.account_id || '-');
  setText('val-user-id', s.user_id || '-');
  setText('val-total-received', s.total_received);
  setText('val-total-sent', s.total_sent);
  setText('val-queue-size', s.queue_size);
  setText('val-login-mode', s.login_mode || '-');
  setText('val-qr-status-grid', s.qr_state?.status || '-');

  // Status panel
  setText('val-qr-status', s.qr_state?.status || '-');
  setText('val-last-poll', s.last_poll_at || '-');
  setText('val-base-url', s.base_url || '-');
  setText('val-creds-path', s.credentials_path || '-');

  const errEl = document.getElementById('val-last-error');
  if (errEl) {
    const errText = s.last_error || '无';
    errEl.textContent = errText;
    errEl.style.color = s.last_error ? '#f87171' : '';
  }

  // QR image / link
  const qrImg = document.getElementById('qr-img');
  const qrFallback = document.getElementById('qr-fallback');
  const qrLink = document.getElementById('qr-link');
  const qrNone = document.getElementById('qr-none');

  const imgContent = s.qr_state?.qrcode_img_content;
  const qrUrl = s.qr_state?.qrcode_url;

  if (imgContent) {
    if (qrImg) { qrImg.src = imgContent; qrImg.style.display = ''; }
    if (qrFallback) qrFallback.style.display = 'none';
    if (qrLink) qrLink.style.display = 'none';
    if (qrNone) qrNone.style.display = 'none';
  } else if (qrUrl) {
    if (qrImg) qrImg.style.display = 'none';
    if (qrFallback) qrFallback.style.display = 'none';
    if (qrLink) {
      qrLink.style.display = '';
      const a = qrLink.querySelector('a');
      if (a) { a.href = qrUrl; a.textContent = qrUrl; }
    }
    if (qrNone) qrNone.style.display = 'none';
  } else {
    if (qrImg) qrImg.style.display = 'none';
    if (qrFallback) qrFallback.style.display = 'none';
    if (qrLink) qrLink.style.display = 'none';
    if (qrNone) qrNone.style.display = '';
  }
}

function poll() {
  fetch('/api/status')
    .then(r => r.json())
    .then(data => { updateStatus(data); })
    .catch(() => {});
}

setInterval(poll, POLL_INTERVAL);

// Reload-env button
function reloadEnv() {
  const btn = document.getElementById('btn-reload-env');
  const msg = document.getElementById('env-reload-msg');
  if (btn) btn.disabled = true;
  fetch('/reload-env', { method: 'POST' })
    .then(r => r.json())
    .then(() => {
      if (msg) { msg.textContent = '配置已重载 ✓'; msg.style.color = '#4ade80'; }
    })
    .catch(e => {
      if (msg) { msg.textContent = '重载失败: ' + e; msg.style.color = '#f87171'; }
    })
    .finally(() => {
      if (btn) btn.disabled = false;
    });
}

// ---------------------------------------------------------------------------
// Pipeline visualizer
// ---------------------------------------------------------------------------

// Node layout config per pipeline type
const PIPELINE_LAYOUTS = {
  chat: {
    nodes: [
      { id: 'Intent',    label: 'Intent',    sub: '意图识别' },
      { id: 'Performer', label: 'Performer', sub: '任务执行' },
      { id: 'Chat',      label: 'Chat',      sub: '对话生成' },
      { id: 'Memory',    label: 'Memory',    sub: '记忆存储' },
    ],
    // default edges (left-to-right chain)
    defaultEdges: [
      { from: 'Intent', to: 'Performer' },
      { from: 'Performer', to: 'Chat' },
      { from: 'Chat', to: 'Memory' },
    ],
  },
  proactive: {
    nodes: [
      { id: 'Proactive', label: 'Proactive', sub: '主动决策' },
      { id: 'Performer', label: 'Performer', sub: '任务执行' },
      { id: 'Chat',      label: 'Chat',      sub: '消息生成' },
    ],
    defaultEdges: [
      { from: 'Proactive', to: 'Performer' },
      { from: 'Performer', to: 'Chat' },
    ],
  },
};

const NODE_W = 110, NODE_H = 56, NODE_GAP = 60;
const SVG_PAD_X = 20, SVG_PAD_Y = 20;

function buildLayout(type) {
  const layout = PIPELINE_LAYOUTS[type] || PIPELINE_LAYOUTS.chat;
  const nodes = layout.nodes;
  const totalW = nodes.length * NODE_W + (nodes.length - 1) * NODE_GAP + SVG_PAD_X * 2;
  const totalH = NODE_H + SVG_PAD_Y * 2;
  return { nodes, defaultEdges: layout.defaultEdges, totalW, totalH };
}

function nodeX(i) { return SVG_PAD_X + i * (NODE_W + NODE_GAP); }
function nodeY() { return SVG_PAD_Y; }

function nodeClass(nodeId, state) {
  if (state.error && state.active_node === nodeId) return 'pnode pnode-error';
  if (state.active_node === nodeId) return 'pnode pnode-active';
  if ((state.skipped_nodes || []).includes(nodeId)) return 'pnode pnode-skipped';
  if ((state.completed_nodes || []).includes(nodeId)) return 'pnode pnode-done';
  return 'pnode pnode-idle';
}

function edgeIsActive(edge, state) {
  // An edge is "active" (flowing) when its source is done and target is active or done
  const completed = state.completed_nodes || [];
  const active = state.active_node;
  return completed.includes(edge.from) && (active === edge.to || completed.includes(edge.to));
}

function renderPipeline(state) {
  const type = state.pipeline_type || 'chat';
  const { nodes, defaultEdges, totalW, totalH } = buildLayout(type);

  const svg = document.getElementById('pipeline-svg');
  if (!svg) return;
  svg.setAttribute('viewBox', `0 0 ${totalW} ${totalH}`);
  svg.setAttribute('height', totalH);

  // Compute active edges: use state.edges (actual data flow) merged with defaults
  const activeEdges = state.edges || [];
  const edgeSet = defaultEdges.map(e => ({
    ...e,
    active: edgeIsActive(e, state) || activeEdges.some(ae => ae.from === e.from && ae.to === e.to),
  }));

  // Render edges
  const edgeG = document.getElementById('pipeline-edges');
  edgeG.innerHTML = '';
  edgeSet.forEach(edge => {
    const fromIdx = nodes.findIndex(n => n.id === edge.from);
    const toIdx   = nodes.findIndex(n => n.id === edge.to);
    if (fromIdx < 0 || toIdx < 0) return;
    const x1 = nodeX(fromIdx) + NODE_W;
    const y1 = nodeY() + NODE_H / 2;
    const x2 = nodeX(toIdx);
    const y2 = nodeY() + NODE_H / 2;
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    const mx = (x1 + x2) / 2;
    path.setAttribute('d', `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
    path.setAttribute('class', edge.active ? 'pedge pedge-active' : 'pedge');
    edgeG.appendChild(path);
  });

  // Render nodes
  const nodeG = document.getElementById('pipeline-nodes');
  nodeG.innerHTML = '';
  nodes.forEach((node, i) => {
    const x = nodeX(i);
    const y = nodeY();
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    const nodeData = (state.node_data || {})[node.id];
    const clickable = nodeData && (nodeData.input || nodeData.output);
    g.setAttribute('class', nodeClass(node.id, state) + (clickable ? ' pnode-clickable' : ''));
    g.setAttribute('transform', `translate(${x},${y})`);
    if (clickable) {
      g.addEventListener('click', () => openNodeModal(node.id, node.label, nodeData));
    }

    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('width', NODE_W);
    rect.setAttribute('height', NODE_H);
    rect.setAttribute('rx', 12);
    rect.setAttribute('ry', 12);
    g.appendChild(rect);

    const text1 = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text1.setAttribute('x', NODE_W / 2);
    text1.setAttribute('y', NODE_H / 2 - 6);
    text1.setAttribute('text-anchor', 'middle');
    text1.setAttribute('dominant-baseline', 'middle');
    text1.setAttribute('font-size', '13');
    text1.setAttribute('font-weight', '700');
    text1.textContent = node.label;
    g.appendChild(text1);

    const text2 = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text2.setAttribute('x', NODE_W / 2);
    text2.setAttribute('y', NODE_H / 2 + 12);
    text2.setAttribute('text-anchor', 'middle');
    text2.setAttribute('dominant-baseline', 'middle');
    text2.setAttribute('font-size', '10');
    text2.setAttribute('font-weight', '400');
    text2.setAttribute('fill', '#94a3b8');
    text2.textContent = node.sub;
    g.appendChild(text2);

    nodeG.appendChild(g);
  });

  // Trigger text
  const triggerEl = document.getElementById('pipeline-trigger');
  if (triggerEl) {
    const prefix = type === 'proactive' ? '🔔 主动触达: ' : '💬 用户消息: ';
    triggerEl.textContent = state.trigger ? prefix + state.trigger : '等待首次运行...';
  }

  // Status text
  const statusEl = document.getElementById('pipeline-status-text');
  if (statusEl) {
    if (state.running) {
      statusEl.textContent = `⚙️ 正在执行 ${state.active_node || '...'}`;
      statusEl.style.color = '#60a5fa';
    } else if (state.error) {
      statusEl.textContent = `❌ 出错: ${state.error}`;
      statusEl.style.color = '#f87171';
    } else if (state.finished_at) {
      const elapsed = state.finished_at && state.started_at
        ? ((state.finished_at - state.started_at) * 1000).toFixed(0) + ' ms'
        : '';
      statusEl.textContent = `✅ 完成 ${elapsed ? '· ' + elapsed : ''}`;
      statusEl.style.color = '#4ade80';
    } else {
      statusEl.textContent = '';
    }
  }
}

// ---------------------------------------------------------------------------
// Node detail modal
// ---------------------------------------------------------------------------

function syntaxHighlightJson(obj) {
  const str = JSON.stringify(obj, null, 2);
  return str.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g, match => {
    if (/^"/.test(match)) {
      if (/:$/.test(match)) return `<span class="json-key">${match}</span>`;
      return `<span class="json-str">${match}</span>`;
    }
    if (/true|false/.test(match)) return `<span class="json-bool">${match}</span>`;
    if (/null/.test(match)) return `<span class="json-null">${match}</span>`;
    return `<span class="json-num">${match}</span>`;
  });
}

function renderMessages(messages) {
  if (!Array.isArray(messages) || messages.length === 0) return null;
  return messages.map(msg => {
    const role = msg.role || 'unknown';
    const roleClass = `msg-role-${role}`;
    let content = msg.content || '';
    // Try to parse content as JSON
    let rendered = '';
    try {
      const parsed = JSON.parse(content);
      rendered = syntaxHighlightJson(parsed);
    } catch {
      // escape HTML and show as plain text
      rendered = content.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    return `<div class="msg-block"><div class="msg-role ${roleClass}">${role}</div><div>${rendered}</div></div>`;
  }).join('');
}

function renderNodeData(data, isInput) {
  if (!data) return '<span class="node-panel-empty">暂无数据</span>';
  const messages = data.messages;
  const extra = isInput ? data : (data.content !== undefined ? data : null);

  let html = '';

  // If we have messages array, render each message as a chat turn
  if (Array.isArray(messages) && messages.length > 0) {
    const rendered = renderMessages(messages);
    if (rendered) html += rendered;
  }

  // Also show the content / structured input
  const contentKey = isInput ? null : 'content';
  if (!isInput && data.content !== undefined) {
    let c = data.content;
    let rendered = '';
    try {
      const parsed = typeof c === 'string' ? JSON.parse(c) : c;
      rendered = syntaxHighlightJson(parsed);
    } catch {
      rendered = String(c).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    html += `<div class="msg-block"><div class="msg-role msg-role-assistant">structured output</div><div>${rendered}</div></div>`;
  }
  if (isInput) {
    // Show the raw input object (minus messages)
    const { messages: _m, ...rest } = data;
    if (Object.keys(rest).length > 0) {
      html = `<div class="msg-block"><div class="msg-role msg-role-user">input context</div><div>${syntaxHighlightJson(rest)}</div></div>` + html;
    }
  }

  return html || '<span class="node-panel-empty">暂无数据</span>';
}

function openNodeModal(nodeId, label, nodeData) {
  const overlay = document.getElementById('node-modal-overlay');
  const title = document.getElementById('node-modal-title');
  const inputEl = document.getElementById('node-modal-input');
  const outputEl = document.getElementById('node-modal-output');

  title.textContent = `${label} Agent — 详情`;
  inputEl.innerHTML = renderNodeData(nodeData.input, true);
  outputEl.innerHTML = renderNodeData(nodeData.output, false);

  overlay.classList.add('open');
}

function closeNodeModal(event) {
  if (event && event.target !== document.getElementById('node-modal-overlay')) return;
  document.getElementById('node-modal-overlay').classList.remove('open');
}

// Close on Escape
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.getElementById('node-modal-overlay')?.classList.remove('open');
});

function pollPipeline() {
  fetch('/api/pipeline-status')
    .then(r => r.json())
    .then(data => { renderPipeline(data); })
    .catch(() => {});
}

setInterval(pollPipeline, PIPELINE_POLL_INTERVAL);
pollPipeline(); // immediate first render
