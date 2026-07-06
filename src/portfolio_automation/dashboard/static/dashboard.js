// Real-time WebSocket connection and helper utilities
let ws = null;

function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws`;
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log('Connected to real-time WebSocket');
    const badge = document.getElementById('ws-status-indicator');
    if (badge) {
      badge.className = 'w-2.5 h-2.5 rounded-full bg-green-500 animate-pulse';
    }
  };

  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      handleWebSocketMessage(payload);
    } catch (err) {
      console.error('Error parsing WS message:', err);
    }
  };

  ws.onclose = () => {
    const badge = document.getElementById('ws-status-indicator');
    if (badge) {
      badge.className = 'w-2.5 h-2.5 rounded-full bg-red-500';
    }
    setTimeout(connectWebSocket, 3000);
  };
}

function handleWebSocketMessage(payload) {
  const { type, data } = payload;
  showToast(`${type}: ${data.message || JSON.stringify(data)}`);

  if (typeof window.refreshDashboardData === 'function') {
    window.refreshDashboardData();
  }
}

function showToast(msg) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = 'glass-card px-4 py-3 rounded-lg shadow-lg border border-slate-700 flex items-center space-x-3 transition duration-300 transform translate-y-0';
  toast.innerHTML = `<span class="text-sm font-medium text-slate-200">${msg}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 4000);
}

document.addEventListener('DOMContentLoaded', () => {
  connectWebSocket();
});
