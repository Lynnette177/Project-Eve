/* memories.js — fetch /api/memories and render all sections */

'use strict';

/* ── helpers ── */
function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmt(ts) {
  if (!ts) return '<span style="color:#475569">—</span>';
  // ISO → readable
  try {
    const d = new Date(ts);
    if (isNaN(d)) return esc(ts);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} `
         + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch { return esc(ts); }
}

const STATUS_PILLS = {
  active:       'pill-green',
  expired:      'pill-amber',
  archived:     'pill-slate',
  forgotten:    'pill-rose',
  deleted:      'pill-red',
  pending_info: 'pill-sky',
  completed:    'pill-green',
  failed:       'pill-red',
  cancelled:    'pill-slate',
  planned:      'pill-blue',
  skipped:      'pill-slate',
  sent:         'pill-green',
  task_started: 'pill-purple',
};

function pill(text, cls) {
  const c = cls || STATUS_PILLS[text] || 'pill-slate';
  return `<span class="pill ${c}">${esc(text)}</span>`;
}

function priorityDots(n, max = 5) {
  let html = '<span class="pri">';
  for (let i = 1; i <= max; i++) html += `<span class="pri-dot${i <= n ? ' on' : ''}"></span>`;
  return html + '</span>';
}

function bar(val, max, color = '#6366f1') {
  const pct = max > 0 ? Math.min(100, (val / max) * 100).toFixed(1) : 0;
  return `<div class="bar-wrap">
    <div class="bar"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
    <span class="bar-label">${parseFloat(val).toFixed(2)}</span>
  </div>`;
}

/* ── tab switching ── */
let currentTab = 'messages';
window.switchTab = function(tab) {
  document.querySelectorAll('.pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('pane-' + tab).classList.add('active');
  document.getElementById('tab-' + tab).classList.add('active');
  currentTab = tab;
};

/* ── filter state ── */
let coreFilter = null;
let eventFilter = null;
let _lastData = null;

/* ── main load ── */
window.loadData = function() {
  document.getElementById('loader').classList.remove('hidden');
  fetch('/api/memories')
    .then(r => r.json())
    .then(data => {
      _lastData = data;
      renderMessages(data);
      renderCore(data);
      renderEvents(data);
      renderTasks(data);
      renderProactive(data);
      document.getElementById('last-updated').textContent =
        '更新于 ' + new Date().toLocaleTimeString('zh-CN');
    })
    .catch(e => alert('加载失败: ' + e))
    .finally(() => document.getElementById('loader').classList.add('hidden'));
};

/* ═══════ MESSAGES ═══════ */
function renderMessages(data) {
  const stats = data.msg_stats || {};
  const msgs  = data.recent_messages || [];

  // stat cards
  const row = document.getElementById('msg-stats-row');
  const total = (stats.received_count || 0) + (stats.sent_count || 0);
  row.innerHTML = [
    statCard('消息总数',    total,                 '#0ea5e9'),
    statCard('用户消息',    stats.received_count,   '#10b981'),
    statCard('助手回复',    stats.sent_count,       '#6366f1'),
    statCard('对话轮次',    Math.floor(Math.min(stats.received_count, stats.sent_count) || 0), '#f59e0b'),
  ].join('');

  // bubbles
  const list = document.getElementById('msg-list');
  if (!msgs.length) { list.innerHTML = '<div class="empty">暂无消息记录</div>'; return; }

  list.innerHTML = msgs.map(m => {
    const isUser = m.role === 'user';
    const typeTag = m.message_type !== 'text'
      ? ` <span class="pill pill-amber" style="font-size:10px">${esc(m.message_type)}</span>` : '';
    return `<div class="msg-bubble ${esc(m.role)}">
      ${typeTag}
      <div>${esc(m.content || (m.media_id ? '[媒体: ' + m.media_id + ']' : '—'))}</div>
      <div class="msg-meta">#${m.id} · ${fmt(m.created_at)} · ${isUser ? '👤 用户' : '🤖 助手'}</div>
    </div>`;
  }).join('');
}

function statCard(label, val, color) {
  return `<div class="stat-card">
    <div class="stat-label">${label}</div>
    <div class="stat-value" style="color:${color}">${val ?? 0}</div>
  </div>`;
}

/* ═══════ CORE MEMORIES ═══════ */
function renderCore(data) {
  const mems = data.core_memories || [];
  const cats  = data.core_categories || [];

  // stat
  document.getElementById('core-stat-row').innerHTML = [
    statCard('核心记忆总数', mems.length, '#6366f1'),
    statCard('分类数',       cats.length, '#0ea5e9'),
    statCard('高优先级 (5)', mems.filter(m => m.priority === 5).length, '#f59e0b'),
    statCard('来源类型数',   new Set(mems.map(m => m.source)).size, '#10b981'),
  ].join('');

  // category filter
  const cf = document.getElementById('core-cat-filter');
  cf.innerHTML = `<button class="filter-btn ${!coreFilter ? 'active' : ''}" onclick="setCoreFilter(null)">全部</button>`
    + cats.map(c => `<button class="filter-btn ${coreFilter === c ? 'active' : ''}" onclick="setCoreFilter('${c}')">${esc(c)}</button>`).join('');

  renderCoreTable(mems);
}

window.setCoreFilter = function(cat) {
  coreFilter = cat;
  if (!_lastData) return;
  const cf = document.getElementById('core-cat-filter');
  cf.querySelectorAll('.filter-btn').forEach(b => {
    b.classList.toggle('active', b.textContent.trim() === (cat || '全部'));
  });
  renderCoreTable(_lastData.core_memories || []);
};

function renderCoreTable(mems) {
  const filtered = coreFilter ? mems.filter(m => m.category === coreFilter) : mems;
  const tbody = document.getElementById('core-tbody');
  if (!filtered.length) { tbody.innerHTML = '<tr><td colspan="9" class="empty">暂无数据</td></tr>'; return; }
  tbody.innerHTML = filtered.map(m => `<tr>
    <td class="mono">${m.id}</td>
    <td><code style="font-size:12px;color:#a5b4fc">${esc(m.memory_key)}</code></td>
    <td>${pill(m.category, 'pill-purple')}</td>
    <td class="content-cell">${esc(m.content)}</td>
    <td>${priorityDots(m.priority)}</td>
    <td>${bar(m.confidence, 1, '#6366f1')}</td>
    <td class="mono">${m.mention_count}</td>
    <td class="mono" style="color:#64748b">${esc(m.source)}</td>
    <td class="mono">${fmt(m.updated_at)}</td>
  </tr>`).join('');
}

/* ═══════ EVENT MEMORIES ═══════ */
function renderEvents(data) {
  const mems   = data.event_memories || [];
  const counts = data.event_status_counts || {};

  // stat
  const statRow = document.getElementById('event-stat-row');
  const all = mems.length;
  statRow.innerHTML = [
    statCard('事件记忆总数', all,                        '#f59e0b'),
    statCard('活跃',         counts.active || 0,         '#10b981'),
    statCard('已过期',       counts.expired || 0,        '#f59e0b'),
    statCard('已归档',       counts.archived || 0,       '#64748b'),
    statCard('已遗忘',       counts.forgotten || 0,      '#f87171'),
  ].join('');

  // status filter
  const sf = document.getElementById('event-status-filter');
  const statusOpts = ['全部', 'active', 'expired', 'archived', 'forgotten', 'deleted'];
  sf.innerHTML = statusOpts.map(s =>
    `<button class="filter-btn ${eventFilter === (s === '全部' ? null : s) ? 'active' : ''}"
      onclick="setEventFilter(${s === '全部' ? 'null' : '"' + s + '"'})">${esc(s)}</button>`
  ).join('');

  renderEventTable(mems);
}

window.setEventFilter = function(status) {
  eventFilter = status;
  if (!_lastData) return;
  const sf = document.getElementById('event-status-filter');
  sf.querySelectorAll('.filter-btn').forEach(b => {
    const val = b.textContent.trim() === '全部' ? null : b.textContent.trim();
    b.classList.toggle('active', val === status);
  });
  renderEventTable(_lastData.event_memories || []);
};

function renderEventTable(mems) {
  const filtered = eventFilter ? mems.filter(m => m.status === eventFilter) : mems;
  const tbody = document.getElementById('event-tbody');
  if (!filtered.length) { tbody.innerHTML = '<tr><td colspan="10" class="empty">暂无数据</td></tr>'; return; }

  // max forget_weight for bar scale
  const maxFW = Math.max(...filtered.map(m => m.forget_weight || 0), 1);

  tbody.innerHTML = filtered.map(m => `<tr>
    <td class="mono">${m.id}</td>
    <td class="content-cell">${esc(m.content)}</td>
    <td>${pill(m.category, 'pill-blue')}</td>
    <td>${pill(m.status)}</td>
    <td>${bar(m.importance, 5, '#f59e0b')}</td>
    <td>${bar(m.emotional_weight, 5, '#ec4899')}</td>
    <td>${bar(m.forget_weight, maxFW, '#6366f1')}</td>
    <td class="mono">${m.recall_count}</td>
    <td class="mono">${fmt(m.happened_at)}</td>
    <td class="mono">${fmt(m.valid_until)}</td>
  </tr>`).join('');
}

/* ═══════ TASKS ═══════ */
function renderTasks(data) {
  const tasks  = data.tasks || [];
  const active = data.active_task;

  const counts = {};
  tasks.forEach(t => { counts[t.status] = (counts[t.status] || 0) + 1; });

  document.getElementById('task-stat-row').innerHTML = [
    statCard('任务总数',   tasks.length,                '#10b981'),
    statCard('进行中',     counts.pending_info || 0,    '#0ea5e9'),
    statCard('已完成',     counts.completed || 0,       '#4ade80'),
    statCard('失败/取消',  (counts.failed || 0) + (counts.cancelled || 0), '#f87171'),
  ].join('');

  // active task banner
  const banner = document.getElementById('active-task-banner');
  const content = document.getElementById('active-task-content');
  if (active) {
    banner.style.display = '';
    content.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:13px">
        <div><span style="color:#64748b">名称：</span>${esc(active.task_name)}</div>
        <div><span style="color:#64748b">状态：</span>${pill(active.status)}</div>
        <div style="grid-column:1/-1"><span style="color:#64748b">描述：</span>${esc(active.task_description)}</div>
        ${active.last_question ? `<div style="grid-column:1/-1"><span style="color:#64748b">最近问题：</span><em>${esc(active.last_question)}</em></div>` : ''}
        ${active.task_info && Object.keys(active.task_info).length ? `
          <div style="grid-column:1/-1">
            <span style="color:#64748b">任务信息：</span>
            <details><summary style="display:inline;cursor:pointer;color:#6366f1">展开</summary>
              <pre style="margin-top:6px;font-size:11px;color:#94a3b8;overflow:auto">${esc(JSON.stringify(active.task_info, null, 2))}</pre>
            </details>
          </div>` : ''}
      </div>`;
  } else {
    banner.style.display = 'none';
  }

  // table
  const tbody = document.getElementById('task-tbody');
  if (!tasks.length) { tbody.innerHTML = '<tr><td colspan="8" class="empty">暂无任务记录</td></tr>'; return; }
  tbody.innerHTML = tasks.map(t => `<tr>
    <td class="mono">${t.id}</td>
    <td style="font-weight:600">${esc(t.task_name)}</td>
    <td>${pill(t.status)}</td>
    <td class="content-cell" style="max-width:220px">${esc(t.task_description)}</td>
    <td class="content-cell" style="max-width:200px;color:#94a3b8">${esc(t.last_question || '—')}</td>
    <td style="color:#f87171;font-size:12px">${esc(t.error_message || '—')}</td>
    <td class="mono">${fmt(t.created_at)}</td>
    <td class="mono">${fmt(t.completed_at)}</td>
  </tr>`).join('');
}

/* ═══════ PROACTIVE ═══════ */
function renderProactive(data) {
  const evts     = data.proactive_events || [];
  const settings = data.proactive_settings || {};

  // settings table
  const ps = document.getElementById('ps-tbody');
  const psKeys = Object.keys(settings);
  if (psKeys.length) {
    ps.innerHTML = psKeys.map(k => {
      const r = settings[k];
      return `<tr>
        <td><code style="color:#a5b4fc">${esc(k)}</code></td>
        <td style="word-break:break-all">${esc(r.value)}</td>
        <td class="mono">${fmt(r.updated_at)}</td>
      </tr>`;
    }).join('');
  } else {
    ps.innerHTML = '<tr><td colspan="3" class="empty">暂无设置项</td></tr>';
  }

  // events table
  const tbody = document.getElementById('proactive-tbody');
  if (!evts.length) { tbody.innerHTML = '<tr><td colspan="8" class="empty">暂无事件记录</td></tr>'; return; }
  tbody.innerHTML = evts.map(e => {
    // decision summary
    let dec = '—';
    if (e.decision && typeof e.decision === 'object') {
      const should = e.decision.should_send ?? e.decision.should_proact;
      dec = should != null ? (should ? '✅ 发送' : '⛔ 跳过') : JSON.stringify(e.decision).slice(0, 60);
    } else if (e.decision) {
      dec = String(e.decision).slice(0, 80);
    }
    return `<tr>
      <td class="mono">${e.id}</td>
      <td><code style="font-size:12px;color:#7dd3fc">${esc(e.event_type)}</code></td>
      <td>${pill(e.status)}</td>
      <td style="font-size:12px;color:#94a3b8">${esc(e.action_type)}</td>
      <td class="content-cell" style="max-width:200px;font-size:12px">${esc(e.reason || '—')}</td>
      <td class="mono">${e.task_id ?? '—'}</td>
      <td style="font-size:12px;color:#94a3b8">${esc(dec)}</td>
      <td class="mono">${fmt(e.created_at)}</td>
    </tr>`;
  }).join('');
}

/* ── kick off ── */
loadData();
