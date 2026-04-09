/**
 * OjolBoost MAMS — app.js
 * Chat UI Logic: fetch /chat, typing indicator, agent badges, auto-scroll
 */

/* ── Config ─────────────────────────────────────────────── */
const CHAT_ENDPOINT = '/chat';
const DRIVER_ID     = 'DRIVER_WEB_001';
const MAX_CHARS     = 500;

/* ── Agent Badge Config ─────────────────────────────────── */
const AGENT_CONFIG = {
  'The Auditor':      { emoji: '💰', cls: 'badge-auditor',   label: 'Auditor'   },
  'Demand Analytics': { emoji: '📊', cls: 'badge-demand',    label: 'Demand'    },
  'Environmental':    { emoji: '🌤️', cls: 'badge-env',       label: 'Weather'   },
  'The Planner':      { emoji: '📅', cls: 'badge-planner',   label: 'Planner'   },
  'The Archivist':    { emoji: '📁', cls: 'badge-archivist', label: 'Archivist' },
};

/* ── State ──────────────────────────────────────────────── */
let isLoading      = false;
let toastTimer     = null;
let activeAgents   = new Set();

/* ── DOM Refs ───────────────────────────────────────────── */
const $input     = document.getElementById('msg-input');
const $sendBtn   = document.getElementById('send-btn');
const $scroll    = document.getElementById('messages-scroll');
const $container = document.getElementById('messages-container');
const $charCount = document.getElementById('char-count');
const $latency   = document.getElementById('latency-value');
const $latBox    = document.getElementById('latency-display');
const $hStatus   = document.getElementById('header-status');
const $toast     = document.getElementById('toast');

/* ── Init ───────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  // Set welcome timestamp
  const welcomeTime = document.getElementById('welcome-time');
  if (welcomeTime) welcomeTime.textContent = formatTime(new Date());

  // Textarea auto-resize & keyboard handling
  $input.addEventListener('input', onInputChange);
  $input.addEventListener('keydown', onKeyDown);

  // Focus input
  $input.focus();

  // Mobile sidebar overlay (create dynamically)
  const overlay = document.createElement('div');
  overlay.className = 'sidebar-overlay';
  overlay.id = 'sidebar-overlay';
  overlay.onclick = closeSidebar;
  document.body.appendChild(overlay);
});

/* ── Input Handling ─────────────────────────────────────── */
function onInputChange() {
  // Auto-resize textarea
  $input.style.height = 'auto';
  $input.style.height = Math.min($input.scrollHeight, 120) + 'px';

  // Char count
  const len = $input.value.length;
  $charCount.textContent = `${len}/${MAX_CHARS}`;
  $charCount.classList.toggle('over', len > MAX_CHARS);

  // Enable/disable send button
  $sendBtn.disabled = isLoading || len === 0 || len > MAX_CHARS;
}

function onKeyDown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!isLoading && $input.value.trim()) sendMessage();
  }
}

/* ── Send Message ───────────────────────────────────────── */
async function sendMessage() {
  const text = $input.value.trim();
  if (!text || isLoading) return;
  if (text.length > MAX_CHARS) { showToast('⚠️ Pesan terlalu panjang!'); return; }

  // Render user bubble
  appendUserMessage(text);

  // Clear input
  $input.value = '';
  $input.style.height = 'auto';
  $charCount.textContent = `0/${MAX_CHARS}`;
  $sendBtn.disabled = true;

  // Show typing indicator
  const typingEl = showTypingIndicator();
  setLoading(true);

  try {
    const t0 = performance.now();

    const res = await fetch(CHAT_ENDPOINT, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ message: text, driver_id: DRIVER_ID }),
    });

    const data = await res.json();
    const elapsed = performance.now() - t0;

    // Remove typing indicator
    typingEl.remove();

    if (res.ok && data.status === 'ok') {
      appendBotMessage(data.narration, data.agents_called || [], data.latency_ms);
      updateLatency(data.latency_ms || elapsed);
      updateAgentPills(data.agents_called || []);
    } else {
      const errMsg = data.narration || 'Aduh, ada error nih Bang. Coba lagi ya!';
      appendBotMessage(errMsg, [], 0, true);
      showToast('⚠️ Gagal terhubung ke Bang Jek');
    }

  } catch (err) {
    typingEl.remove();
    appendBotMessage(
      'Bang Jek lagi nggak bisa dihubungi nih 😅 Pastiin servernya nyala ya, terus coba lagi!',
      [], 0, true
    );
    showToast('❌ Tidak bisa terhubung ke server');
    console.error('[OjolBoost] Fetch error:', err);
  } finally {
    setLoading(false);
    $input.focus();
  }
}

/* ── Quick Actions ──────────────────────────────────────── */
function sendQuick(text) {
  if (isLoading) return;
  $input.value = text;
  onInputChange();
  sendMessage();
}

/* ── Message Renderers ──────────────────────────────────── */
function appendUserMessage(text) {
  const group = createEl('div', 'message-group user-group');
  group.innerHTML = `
    <div class="avatar-wrap">
      <div class="msg-avatar user-avatar">👤</div>
    </div>
    <div class="messages-stack">
      <div class="message user-message"><p>${escHtml(text)}</p></div>
      <div class="msg-meta">
        <span>Lo</span>
        <span>${formatTime(new Date())}</span>
      </div>
    </div>`;
  $scroll.appendChild(group);
  scrollBottom();
}

function appendBotMessage(text, agentsCalled, latencyMs, isError = false) {
  const group = createEl('div', 'message-group bot-group');

  // Build badges HTML
  let badgesHtml = '';
  if (agentsCalled.length > 0) {
    const badges = agentsCalled.map(name => {
      const cfg = AGENT_CONFIG[name] || { emoji: '🤖', cls: 'badge-default', label: name };
      return `<span class="agent-badge ${cfg.cls}">${cfg.emoji} ${cfg.label}</span>`;
    }).join('');
    badgesHtml = `<div class="agent-badges">${badges}</div>`;
  }

  // Format latency
  const latencyStr = latencyMs > 0
    ? `${(latencyMs / 1000).toFixed(1)}s`
    : '';

  const metaExtra = [
    latencyStr && `⚡ ${latencyStr}`,
  ].filter(Boolean).join(' · ');

  group.innerHTML = `
    <div class="avatar-wrap">
      <div class="msg-avatar bot-avatar">🤖</div>
    </div>
    <div class="messages-stack">
      <div class="message bot-message${isError ? ' error-msg' : ''}">
        <p>${formatBotText(text)}</p>
      </div>
      ${badgesHtml}
      <div class="msg-meta">
        <span>Bang Jek</span>
        <span>${formatTime(new Date())}</span>
        ${metaExtra ? `<span>${metaExtra}</span>` : ''}
      </div>
    </div>`;

  $scroll.appendChild(group);
  scrollBottom();
}

/* ── Typing Indicator ────────────────────────────────────── */
function showTypingIndicator() {
  const el = createEl('div', 'typing-indicator');
  el.id = 'typing-indicator';
  el.innerHTML = `
    <div class="avatar-wrap">
      <div class="msg-avatar bot-avatar">🤖</div>
    </div>
    <div class="typing-bubble">
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
    </div>`;
  $scroll.appendChild(el);
  scrollBottom();
  return el;
}

/* ── Agent Status Pills (Sidebar) ───────────────────────── */
function updateAgentPills(agentsCalled) {
  // Reset all
  document.querySelectorAll('.agent-pill').forEach(p => p.classList.remove('active'));

  // Activate called agents
  const idMap = {
    'The Auditor':      'status-auditor',
    'Demand Analytics': 'status-demand',
    'Environmental':    'status-env',
    'The Planner':      'status-planner',
    'The Archivist':    'status-archivist',
  };
  agentsCalled.forEach(name => {
    const el = document.getElementById(idMap[name]);
    if (el) {
      el.classList.add('active');
      // Auto-reset after 4s
      setTimeout(() => el.classList.remove('active'), 4000);
    }
  });
}

/* ── Latency Display ─────────────────────────────────────── */
function updateLatency(ms) {
  $latency.textContent = ms >= 1000
    ? `${(ms / 1000).toFixed(1)}s`
    : `${Math.round(ms)}ms`;

  $latBox.classList.remove('fast', 'slow');
  if (ms < 2000)      $latBox.classList.add('fast');
  else if (ms > 5000) $latBox.classList.add('slow');
}

/* ── Loading State ───────────────────────────────────────── */
function setLoading(state) {
  isLoading = state;
  $sendBtn.disabled = state;
  $input.disabled = state;

  // Update header status
  if (state) {
    $hStatus.innerHTML = `<span class="online-dot" style="background:var(--warning);"></span> Memproses...`;
  } else {
    $hStatus.innerHTML = `<span class="online-dot"></span> Online &amp; Siap`;
  }
}

/* ── Sidebar Toggle ──────────────────────────────────────── */
function toggleSidebar() {
  const sidebar  = document.getElementById('sidebar');
  const overlay  = document.getElementById('sidebar-overlay');
  const isOpen   = sidebar.classList.contains('open');
  if (isOpen) closeSidebar();
  else {
    sidebar.classList.add('open');
    overlay.classList.add('visible');
  }
}

function closeSidebar() {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  sidebar.classList.remove('open');
  overlay.classList.remove('visible');
}

/* ── Clear Chat ──────────────────────────────────────────── */
function clearChat() {
  const welcome = document.getElementById('welcome-msg');
  // Remove all except welcome message
  const children = [...$scroll.children];
  children.forEach(el => {
    if (el.id !== 'welcome-msg') el.remove();
  });
  // Reset latency
  $latency.textContent = '—';
  $latBox.classList.remove('fast', 'slow');
  showToast('🗑️ Chat dihapus');
}

/* ── Coming Soon ─────────────────────────────────────────── */
function showComingSoon(feature) {
  showToast(`🚧 ${feature} — Coming Soon!`);
}

/* ── Toast ───────────────────────────────────────────────── */
function showToast(msg, duration = 2800) {
  if (toastTimer) clearTimeout(toastTimer);
  $toast.textContent = msg;
  $toast.classList.add('show');
  toastTimer = setTimeout(() => $toast.classList.remove('show'), duration);
}

/* ── Utilities ───────────────────────────────────────────── */
function scrollBottom() {
  requestAnimationFrame(() => {
    $container.scrollTo({ top: $container.scrollHeight, behavior: 'smooth' });
  });
}

function createEl(tag, className) {
  const el = document.createElement(tag);
  el.className = className;
  return el;
}

function escHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/\n/g, '<br>');
}

function formatBotText(text) {
  // Convert newlines to <br>, preserve emojis
  return escHtml(text);
}

function formatTime(date) {
  return date.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });
}
