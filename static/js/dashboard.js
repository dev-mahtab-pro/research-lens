const DASHBOARD_REFRESH_MS = 2000;
const THEME_STORAGE_KEY = 'researchlens-theme';
let dashboardTimer = null;

initializeDashboard();

function initializeDashboard() {
    initializeTheme();
    fetchDashboardMetrics();
    dashboardTimer = setInterval(fetchDashboardMetrics, DASHBOARD_REFRESH_MS);
}

function initializeTheme() {
    const savedTheme = localStorage.getItem(THEME_STORAGE_KEY);
    const preferredTheme = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    applyTheme(savedTheme || preferredTheme);
}

function toggleTheme() {
    const nextTheme = document.body.dataset.theme === 'light' ? 'dark' : 'light';
    applyTheme(nextTheme);
    localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
}

function applyTheme(theme) {
    document.body.dataset.theme = theme;
    const toggle = document.getElementById('themeToggle');
    if (toggle) {
        toggle.setAttribute('aria-pressed', theme === 'light' ? 'true' : 'false');
        toggle.title = `Switch to ${theme === 'light' ? 'dark' : 'light'} mode`;
    }
}

async function fetchDashboardMetrics() {
    try {
        const response = await fetch('/api/dashboard_metrics');
        const data = await response.json();
        renderDashboard(data);
    } catch (error) {
        renderDashboardError(error);
    }
}

function renderDashboard(data) {
    const latest = data.latest_request || {};
    const ollama = data.ollama || {};
    const system = data.system || {};
    const runtime = data.runtime || {};

    setText('dashboardRefreshLabel', 'Live polling every 2 seconds');
    setText('dashboardLastUpdated', `Last updated: ${new Date((data.timestamp || 0) * 1000).toLocaleTimeString()}`);
    setText(
        'dashboardRuntimeMode',
        runtime.force_gpu
            ? `GPU-first mode enabled on GPU ${formatInteger(runtime.main_gpu)}. DeepSeek may still fall back partly to CPU if VRAM is insufficient.`
            : 'GPU force mode is disabled.'
    );

    setText('latestProfile', latest.profile || 'Idle');
    setText('latestTps', latest.status === 'running' ? 'Updating...' : formatMetric(latest.tokens_per_second, ' tok/s'));
    setText('latestModel', latest.model || latest.reported_model || 'No recent request');
    setText('latestGenerated', latest.status === 'running' ? 'In progress' : formatInteger(latest.generated_tokens));
    setText('latestPrompt', formatInteger(latest.prompt_tokens));
    setText('latestDuration', formatMetric(latest.total_duration_seconds, ' s'));

    setText('activeRequests', formatInteger(data.active_requests || 0));
    setText('ollamaReachability', ollama.reachable ? 'Connected' : 'Offline');
    renderActiveModels(ollama.active_models || [], ollama.note || '');

    renderCpu(system.cpu || {});
    renderMemory(system.memory || {});
    renderGpu(system.gpu || {});
    renderModelStats(data.models || []);
}

function renderActiveModels(models, note) {
    const container = document.getElementById('activeModelsList');
    if (!models.length) {
        container.innerHTML = `<div class="dashboard-list-empty">${escapeHtml(note || 'No loaded Ollama models right now.')}</div>`;
        return;
    }

    container.innerHTML = models.map(model => `
        <div class="dashboard-list-row">
            <div>
                <strong>${escapeHtml(model.name || 'Unknown model')}</strong>
                <span>${model.size_vram_gb ? `${model.size_vram_gb} GB VRAM` : 'VRAM size unavailable'}</span>
            </div>
            <span class="dashboard-list-tag">Loaded</span>
        </div>
    `).join('');
}

function renderCpu(cpu) {
    setText('cpuLabel', cpu.available ? `${formatInteger(cpu.logical_cores)} threads` : 'Unavailable');
    setText('cpuUsage', formatPercent(cpu.usage_percent));
    setText(
        'cpuMeta',
        cpu.available
            ? `${formatInteger(cpu.physical_cores)} cores / ${formatInteger(cpu.logical_cores)} threads${cpu.frequency_mhz ? ` / ${cpu.frequency_mhz} MHz` : ''}`
            : (cpu.note || 'CPU metrics unavailable.')
    );
    setMeter('cpuBar', cpu.usage_percent);
}

function renderMemory(memory) {
    setText('memoryUsage', formatPercent(memory.usage_percent));
    setText(
        'memoryMeta',
        memory.available
            ? `${formatMetric(memory.used_gb, ' GB')} used of ${formatMetric(memory.total_gb, ' GB')}`
            : (memory.note || 'RAM metrics unavailable.')
    );
    setMeter('memoryBar', memory.usage_percent);
}

function renderGpu(gpu) {
    setText('gpuLabel', gpu.available ? (gpu.name || 'GPU') : 'Unavailable');
    setText('gpuUsage', formatPercent(gpu.usage_percent));
    const details = gpu.available
        ? `${formatMetric(gpu.memory_used_gb, ' GB')} / ${formatMetric(gpu.memory_total_gb, ' GB')} VRAM${gpu.temperature_c !== null && gpu.temperature_c !== undefined ? ` / ${gpu.temperature_c} C` : ''}`
        : (gpu.note || 'GPU metrics unavailable.');
    setText('gpuMeta', details);
    setMeter('gpuBar', gpu.usage_percent);
}

function renderModelStats(models) {
    const container = document.getElementById('modelStatsGrid');
    if (!models.length) {
        container.innerHTML = '<div class="dashboard-list-empty">No model activity yet.</div>';
        return;
    }

    container.innerHTML = models.map(model => `
        <article class="model-stat-card">
            <div class="model-stat-top">
                <div>
                    <h3>${escapeHtml(model.label || model.id || 'Model')}</h3>
                    <p>${escapeHtml(model.badge || 'Local model')}</p>
                </div>
                <span class="dashboard-chip">${model.active_requests ? `${formatInteger(model.active_requests)} active` : (model.loaded_in_ollama ? 'Loaded' : 'Idle')}</span>
            </div>
            <div class="model-stat-grid">
                <div><span>Calls</span><strong>${formatInteger(model.calls)}</strong></div>
                <div><span>Prompt Tokens</span><strong>${formatInteger(model.is_live ? model.live_prompt_tokens : model.prompt_tokens)}</strong></div>
                <div><span>Generated Tokens</span><strong>${model.is_live ? 'In progress' : formatInteger(model.generated_tokens)}</strong></div>
                <div><span>Last Speed</span><strong>${model.is_live ? 'Updating...' : formatMetric(model.last_tokens_per_second, ' tok/s')}</strong></div>
                <div><span>Average Speed</span><strong>${formatMetric(model.average_tokens_per_second, ' tok/s')}</strong></div>
                <div><span>Runtime</span><strong>${formatMetric(model.is_live ? model.live_runtime_seconds : model.total_duration_seconds, ' s')}</strong></div>
                <div><span>Ollama Status</span><strong>${model.is_live ? 'Running' : (model.loaded_in_ollama ? 'Loaded' : 'Not Loaded')}</strong></div>
                <div><span>VRAM</span><strong>${formatMetric(model.ollama_vram_gb, ' GB')}</strong></div>
                <div><span>Live State</span><strong>${model.is_live ? 'Refreshing every 2 sec' : 'Waiting'}</strong></div>
            </div>
        </article>
    `).join('');
}

function renderDashboardError(error) {
    setText('dashboardRefreshLabel', 'Connection error');
    setText('dashboardLastUpdated', 'Could not load live metrics from the server.');
    setText('ollamaReachability', 'Unknown');
    console.error(error);
}

function setText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
}

function setMeter(id, value) {
    const element = document.getElementById(id);
    if (!element) return;
    const numeric = typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
    element.style.width = `${numeric}%`;
}

function formatInteger(value) {
    if (value === null || value === undefined || value === '') return '--';
    return Number(value).toLocaleString();
}

function formatMetric(value, suffix = '') {
    if (value === null || value === undefined || value === '') return `--${suffix}`;
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return `--${suffix}`;
    return `${numeric.toFixed(numeric >= 100 ? 0 : 2)}${suffix}`;
}

function formatPercent(value) {
    return formatMetric(value, '%');
}

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
