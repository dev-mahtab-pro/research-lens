// ============================================
// ResearchLens - Main JavaScript
// ============================================

function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

let selectedMode = 'summarize';
let currentWorkflow = 'discover';
let currentResults = {};
let currentPaperName = '';
let currentDownloadName = 'research-analysis';
let pendingDiscoveryPayload = null;
let selectedTimelineMinutes = 'none';
let selectedAnalysisInput = 'upload';
const THEME_STORAGE_KEY = 'researchlens-theme';
let stepInterval = null;
let progressInterval = null;
let activeRequestController = null;
let currentResearchContextId = null;
let currentChatContext = null;
let chatHistory = [];
let chatRequestInFlight = false;
let historyItems = [];
const COPILOT_HEIGHT_STORAGE_KEY = 'researchlens-copilot-height';

initializeTheme();
initializeWorkflow();

window.addEventListener('scroll', () => {
    const button = document.getElementById('backToTop');
    if (button) button.classList.toggle('visible', window.scrollY > 500);
    const header = document.querySelector('.site-header');
    if (header) {
        header.classList.toggle('header-hidden', window.scrollY > 140);
        header.classList.toggle('header-at-top', window.scrollY <= 140);
    }
}, { passive: true });

function scrollToTop() {
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function initializeTheme() {
    const savedTheme = localStorage.getItem(THEME_STORAGE_KEY);
    const preferredTheme = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    const theme = savedTheme || preferredTheme;
    applyTheme(theme);
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

function initializeWorkflow() {
    selectWorkflow('discover');
    selectAnalysisInput('upload');
    initializeCopilotHeight();
    loadHistoryList();
}

function initializeCopilotHeight() {
    const savedHeight = localStorage.getItem(COPILOT_HEIGHT_STORAGE_KEY) || '720';
    setCopilotHeight(savedHeight);
    const range = document.getElementById('copilotHeightRange');
    if (range) {
        range.value = savedHeight;
    }
}

function setCopilotHeight(value) {
    const numeric = Math.max(560, Math.min(1200, Number(value) || 720));
    document.documentElement.style.setProperty('--copilot-height', `${numeric}px`);
    localStorage.setItem(COPILOT_HEIGHT_STORAGE_KEY, String(numeric));
}

function togglePanelMaximize(panelId) {
    const panel = document.getElementById(panelId);
    if (!panel) return;
    const isMaximized = panel.classList.toggle('panel-maximized');
    document.body.classList.toggle('panel-focus-mode', isMaximized);
    const button = panel.querySelector('.panel-expand-btn');
    if (button) {
        button.innerHTML = isMaximized ? '&#x2715;' : '&#x26F6;';
        button.setAttribute('aria-label', isMaximized ? 'Restore panel' : 'Maximize panel');
        button.title = isMaximized ? 'Restore panel' : 'Maximize panel';
    }
}

function focusResearchOutput() {
    const panel = document.getElementById('resultsPanel');
    if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function handleDiscoveryKeydown(event) {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    const button = document.getElementById('discoverBtn');
    if (button && !button.disabled) button.click();
}

function selectWorkflow(workflow) {
    currentWorkflow = workflow;
    const discoverButton = document.getElementById('workflowDiscover');
    const analyzeButton = document.getElementById('workflowAnalyze');
    const discoverSection = document.getElementById('discoverSection');
    const analyzeSection = document.getElementById('analyzeSection');

    if (discoverButton && analyzeButton) {
        discoverButton.classList.toggle('active', workflow === 'discover');
        analyzeButton.classList.toggle('active', workflow === 'analyze');
    }

    if (discoverSection && analyzeSection) {
        discoverSection.style.display = workflow === 'discover' ? 'block' : 'none';
        analyzeSection.style.display = workflow === 'analyze' ? 'block' : 'none';
    }
}

function selectAnalysisInput(inputType) {
    selectedAnalysisInput = inputType;
    const uploadButton = document.getElementById('inputMethodUpload');
    const pasteButton = document.getElementById('inputMethodPaste');
    const uploadPanel = document.getElementById('uploadInputPanel');
    const pastePanel = document.getElementById('pasteInputPanel');
    const fileInput = document.getElementById('paperFile');
    const fileDisplay = document.getElementById('fileDisplay');
    const uploadZone = document.getElementById('uploadZone');
    const paperText = document.getElementById('paperText');

    if (uploadButton && pasteButton) {
        uploadButton.classList.toggle('active', inputType === 'upload');
        pasteButton.classList.toggle('active', inputType === 'paste');
    }

    if (uploadPanel && pastePanel) {
        uploadPanel.style.display = inputType === 'upload' ? 'block' : 'none';
        pastePanel.style.display = inputType === 'paste' ? 'block' : 'none';
    }

    if (inputType === 'paste' && fileInput) {
        fileInput.value = '';
        currentPaperName = 'Pasted Research Text';
        if (fileDisplay) fileDisplay.style.display = 'none';
        if (uploadZone) uploadZone.classList.remove('has-file');
    }

    if (inputType === 'upload' && paperText && !paperText.value.trim()) {
        currentPaperName = '';
    }
}

function selectModelFromDropdown(select) {
    const modelId = select.value || '';
    document.querySelectorAll('[data-model-summary]').forEach(item => {
        item.classList.toggle('active', item.getAttribute('data-model-summary') === modelId);
    });
    fetch('/api/ollama/prepare', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken()},
        body: JSON.stringify({model: modelId}),
    }).catch(() => {});
}

function handleFileSelect(input) {
    const file = input.files[0];
    if (!file) return;

    const zone = document.getElementById('uploadZone');
    const display = document.getElementById('fileDisplay');
    const nameEl = document.getElementById('fileName');
    const sizeEl = document.getElementById('fileSize');

    nameEl.textContent = file.name;
    sizeEl.textContent = formatBytes(file.size);
    display.style.display = 'flex';
    zone.classList.add('has-file');
    currentPaperName = file.name;
}

function handleDatasetSelect(input) {
    const files = Array.from(input.files || []);
    const summary = document.getElementById('datasetFileSummary');
    const list = document.getElementById('datasetFileList');

    if (!summary || !list) return;

    if (!files.length) {
        summary.textContent = 'No dataset files selected';
        list.style.display = 'none';
        list.innerHTML = '';
        return;
    }

    summary.textContent = `${files.length} dataset file${files.length > 1 ? 's' : ''} selected`;
    list.style.display = 'grid';
    list.innerHTML = files.map(file => `
        <div class="dataset-file-chip">
            <span class="dataset-file-name">${escapeHtml(file.name)}</span>
            <span class="dataset-file-size">${formatBytes(file.size)}</span>
        </div>
    `).join('');
}

function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function estimateTokensFromFile(file) {
    if (!file) return 0;
    return Math.max(1, Math.round(file.size / 4));
}

function estimateTokenCountFromText(text) {
    if (!text) return 0;
    return Math.max(1, Math.round(text.length / 4));
}

function getEffectiveAnalysisInput(file, pastedText) {
    if ((pastedText || '').trim()) {
        return 'paste';
    }
    if (file) {
        return 'upload';
    }
    return selectedAnalysisInput;
}

async function readJsonResponse(response, fallbackMessage) {
    const contentType = (response.headers.get('content-type') || '').toLowerCase();
    let payload = null;
    let rawText = '';

    try {
        if (contentType.includes('application/json')) {
            payload = await response.json();
        } else {
            rawText = (await response.text()).trim();
            if (rawText) {
                try {
                    payload = JSON.parse(rawText);
                } catch (parseError) {
                    payload = null;
                }
            }
        }
    } catch (readError) {
        rawText = '';
    }

    if (!response.ok) {
        const serverMessage = payload && typeof payload.error === 'string'
            ? payload.error
            : rawText || `${fallbackMessage} (HTTP ${response.status})`;
        throw new Error(serverMessage);
    }

    if (payload && typeof payload === 'object') {
        return payload;
    }

    throw new Error(fallbackMessage);
}

const uploadZone = document.getElementById('uploadZone');
const supportedExtensions = ['.pdf', '.txt', '.doc', '.docx', '.ppt', '.pptx'];
if (uploadZone) {
    uploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadZone.classList.add('drag-over');
    });

    uploadZone.addEventListener('dragleave', () => {
        uploadZone.classList.remove('drag-over');
    });

    uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadZone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        const lowerName = file ? file.name.toLowerCase() : '';
        if (file && supportedExtensions.some(ext => lowerName.endsWith(ext))) {
            const dt = new DataTransfer();
            dt.items.add(file);
            const input = document.getElementById('paperFile');
            input.files = dt.files;
            handleFileSelect(input);
        }
    });
}

function selectMode(card) {
    document.querySelectorAll('.mode-card').forEach(c => c.classList.remove('active'));
    card.classList.add('active');
    selectedMode = card.getAttribute('data-mode');
}

async function runAnalysis() {
    focusResearchOutput();
    const modelName = document.getElementById('modelName').value.trim();
    const fileInput = document.getElementById('paperFile');
    const datasetInput = document.getElementById('datasetFiles');
    const file = fileInput.files[0];
    const datasetFiles = Array.from(datasetInput?.files || []);
    const pastedText = (document.getElementById('paperText')?.value || '').trim();
    const effectiveInput = getEffectiveAnalysisInput(file, pastedText);

    const formData = new FormData();
    formData.append('model', modelName);
    formData.append('analysis_type', selectedMode);
    formData.append('analysis_input', effectiveInput);
    formData.append('internet_search', document.getElementById('internetSearch')?.checked ? 'on' : 'off');
    if (effectiveInput === 'upload' && file) formData.append('paper', file);
    if (effectiveInput === 'paste' && pastedText) formData.append('paper_text', pastedText);
    datasetFiles.forEach(fileItem => formData.append('dataset_files', fileItem));

    showLoading(getModeLabel(selectedMode), 'analysis', {
        estimatedTokens: effectiveInput === 'paste' ? estimateTokenCountFromText(pastedText) : estimateTokensFromFile(file),
        fileName: file ? file.name : 'Pasted Research Text',
    });
    setButtonsDisabled(true);
    activeRequestController = new AbortController();

    try {
        const response = await fetch('/analyze', { method: 'POST', body: formData, headers: {'X-CSRF-Token': getCsrfToken()}, signal: activeRequestController.signal });
        const data = await readJsonResponse(response, 'The analysis request failed.');

        if (data.error) {
            showError(data.error);
        } else {
            currentResults = { [selectedMode]: data.result };
            currentResearchContextId = data.context_id || data.paper_id || null;
            showSingleResult(data);
            loadHistoryList();
        }
    } catch (err) {
        if (err.name === 'AbortError') {
            showError('Research was stopped from the interface. The current server task may finish in the background.');
        } else {
            showError(err.message || 'Connection error. Please check your network and private AI engine.');
        }
    } finally {
        activeRequestController = null;
        setButtonsDisabled(false);
    }
}

async function runAllAnalysis() {
    focusResearchOutput();
    const modelName = document.getElementById('modelName').value.trim();
    const fileInput = document.getElementById('paperFile');
    const datasetInput = document.getElementById('datasetFiles');
    const file = fileInput.files[0];
    const datasetFiles = Array.from(datasetInput?.files || []);
    const pastedText = (document.getElementById('paperText')?.value || '').trim();
    const effectiveInput = getEffectiveAnalysisInput(file, pastedText);

    const formData = new FormData();
    formData.append('model', modelName);
    formData.append('analysis_input', effectiveInput);
    formData.append('internet_search', document.getElementById('internetSearch')?.checked ? 'on' : 'off');
    if (effectiveInput === 'upload' && file) formData.append('paper', file);
    if (effectiveInput === 'paste' && pastedText) formData.append('paper_text', pastedText);
    datasetFiles.forEach(fileItem => formData.append('dataset_files', fileItem));

    showLoading('Full paper report', 'report', {
        estimatedTokens: effectiveInput === 'paste' ? estimateTokenCountFromText(pastedText) : estimateTokensFromFile(file),
        fileName: file ? file.name : 'Pasted Research Text',
    });
    setButtonsDisabled(true);
    activeRequestController = new AbortController();

    try {
        const response = await fetch('/analyze_all', { method: 'POST', body: formData, headers: {'X-CSRF-Token': getCsrfToken()}, signal: activeRequestController.signal });
        const data = await readJsonResponse(response, 'The full report request failed.');

        if (data.error) {
            showError(data.error);
        } else {
            currentResults = data.results;
            currentResearchContextId = data.context_id || data.paper_id || null;
            showAllResults(data);
            loadHistoryList();
        }
    } catch (err) {
        if (err.name === 'AbortError') {
            showError('Research was stopped from the interface. The current server task may finish in the background.');
        } else {
            showError(err.message || 'Connection error. Please check your network and private AI engine.');
        }
    } finally {
        activeRequestController = null;
        setButtonsDisabled(false);
    }
}

async function runDiscovery() {
    const modelName = document.getElementById('modelName').value.trim();
    const field = document.getElementById('researchField').value.trim();
    const theme = document.getElementById('researchTheme').value.trim();

    if (!field) { showError('Please enter the research field.'); return; }
    if (!theme) { showError('Please enter the research theme.'); return; }

    pendingDiscoveryPayload = { modelName, field, theme };
    openTimelineModal();
}

function openTimelineModal() {
    document.getElementById('timelineModal').style.display = 'flex';
}

function closeTimelineModal() {
    document.getElementById('timelineModal').style.display = 'none';
}

function selectTimelineOption(button) {
    document.querySelectorAll('.timeline-option').forEach(option => option.classList.remove('active'));
    button.classList.add('active');
    selectedTimelineMinutes = button.getAttribute('data-minutes') || 'none';
}

async function confirmDiscoveryTimeline() {
    if (!pendingDiscoveryPayload) {
        closeTimelineModal();
        return;
    }

    closeTimelineModal();
    focusResearchOutput();

    const formData = new FormData();
    formData.append('model', pendingDiscoveryPayload.modelName);
    formData.append('field', pendingDiscoveryPayload.field);
    formData.append('theme', pendingDiscoveryPayload.theme);
    formData.append('crawl_minutes', String(selectedTimelineMinutes));
    formData.append('internet_search', document.getElementById('internetSearch')?.checked ? 'on' : 'off');

    const label = selectedTimelineMinutes === 'none'
        ? 'Discovering papers '
        : `Discovering papers (${selectedTimelineMinutes} min budget)`;
    showLoading(label, 'discovery', { unlimited: selectedTimelineMinutes === 'none' });
    setButtonsDisabled(true);
    activeRequestController = new AbortController();

    try {
        const response = await fetch('/discover_papers', { method: 'POST', body: formData, headers: {'X-CSRF-Token': getCsrfToken()}, signal: activeRequestController.signal });
        const data = await readJsonResponse(response, 'The paper discovery request failed.');

        if (data.error) {
            showError(data.error);
        } else {
            currentResearchContextId = data.context_id || data.paper_id || null;
            showDiscoveryResults(data);
            loadHistoryList();
        }
    } catch (err) {
        if (err.name === 'AbortError') {
            showError('Research was stopped from the interface. The current server task may finish in the background.');
        } else {
            showError(err.message || 'Connection error. Please check your network and private AI engine.');
        }
    } finally {
        activeRequestController = null;
        setButtonsDisabled(false);
        pendingDiscoveryPayload = null;
    }
}

function showLoading(label, taskType = 'analysis', options = {}) {
    hideAll();
    document.getElementById('loadingState').style.display = 'flex';
    document.getElementById('loadingText').textContent = `Working: ${label}...`;
    document.getElementById('loadingProgressFill').style.width = '0%';
    const loadingNote = document.getElementById('loadingNote');
    const stopButton = document.getElementById('stopResearchBtn');
    stopButton.style.display = 'inline-flex';

    const steps = ['step1', 'step2', 'step3'];
    let currentStep = 0;
    let tick = 0;
    const statusSets = {
        analysis: [
            'Preparing the research input',
            'Extracting research text',
            'Structuring the research context',
            'Running model reasoning',
            'Building the final write-up'
        ],
        report: [
            'Preparing full report workflow',
            'Reading the research input',
            'Running multiple analysis passes',
            'Combining results into a report',
            'Formatting the final output'
        ],
        discovery: [
            'Turning your theme into search queries',
            'Scanning research sources',
            'Collecting and deduplicating papers',
            'Ranking papers by relevance',
            'Drafting the research overview'
        ]
    };
    const statuses = statusSets[taskType] || statusSets.analysis;
    const estimatedTokens = Number(options.estimatedTokens || 0);
    if (taskType === 'discovery' && options.unlimited) {
        loadingNote.textContent = 'Research will continue until you stop it from the interface. Stay patient while papers are being collected and ranked.';
    } else if (taskType === 'discovery') {
        loadingNote.textContent = 'Crawling papers and organizing results within the selected time budget.';
    } else if (estimatedTokens > 0) {
        const tokenLabel = estimatedTokens.toLocaleString();
        loadingNote.textContent = estimatedTokens >= 25000
            ? `Total token estimate is ~${tokenLabel}. It may take a while because the data is large. Stay patient while processing finishes.`
            : `Total token estimate is ~${tokenLabel}.`;
    } else {
        loadingNote.textContent = 'Preparing your request.';
    }

    steps.forEach(s => {
        document.getElementById(s).className = 'loading-step';
    });
    document.getElementById('step1').classList.add('active');
    document.getElementById('loadingStatus').textContent = statuses[0];

    stepInterval = setInterval(() => {
        document.getElementById(steps[currentStep]).classList.remove('active');
        document.getElementById(steps[currentStep]).classList.add('done');
        currentStep = (currentStep + 1) % steps.length;
        if (currentStep === 0) {
            steps.forEach(stepId => {
                document.getElementById(stepId).className = 'loading-step';
            });
        }
        document.getElementById(steps[currentStep]).classList.add('active');
    }, 1700);

    progressInterval = setInterval(() => {
        tick += 1;
        const loopPosition = tick % 25;
        const percent = Math.min(92, 7 + Math.round((loopPosition / 24) * 85));
        document.getElementById('loadingProgressFill').style.width = `${percent}%`;
        document.getElementById('loadingStatus').textContent = statuses[tick % statuses.length];
    }, 200);
}

function clearStepInterval() {
    if (stepInterval) { clearInterval(stepInterval); stepInterval = null; }
    if (progressInterval) { clearInterval(progressInterval); progressInterval = null; }
}

function stopActiveResearch() {
    if (activeRequestController) {
        activeRequestController.abort();
    }
}

function showSingleResult(data) {
    clearStepInterval();
    hideAll();
    currentResearchContextId = data.context_id || data.paper_id || null;
    currentPaperName = data.paper_name || 'Pasted Research Text';

    const content = document.getElementById('resultsContent');
    const single = document.getElementById('singleResult');
    const allDiv = document.getElementById('allResults');
    const discovery = document.getElementById('discoveryResult');
    const meta = document.getElementById('resultMeta');
    const body = document.getElementById('resultBody');

    allDiv.style.display = 'none';
    discovery.style.display = 'none';
    single.style.display = 'block';

    meta.innerHTML = `
        <span class="meta-badge">${escapeHtml(data.type)}</span>
        <span>File: ${escapeHtml(data.paper_name)}</span>
        <span>/</span>
        <span>~${data.word_count.toLocaleString()} words</span>
        <span>/</span>
        <span>~${Number(data.estimated_tokens || 0).toLocaleString()} tokens</span>
        <span>/</span>
        <span>Model: ${escapeHtml(data.model || '')}</span>
    `;

    body.innerHTML = renderMarkdown(data.result);
    currentDownloadName = sanitizeFilename(data.paper_name || 'research-analysis');

    content.style.display = 'block';
    document.getElementById('resultsActions').style.display = 'flex';
    showPaperInsights(data.chat_context);
}

function showAllResults(data) {
    clearStepInterval();
    hideAll();
    currentResearchContextId = data.context_id || data.paper_id || null;
    currentPaperName = data.paper_name || 'Pasted Research Text';

    const content = document.getElementById('resultsContent');
    const single = document.getElementById('singleResult');
    const allDiv = document.getElementById('allResults');
    const discovery = document.getElementById('discoveryResult');

    single.style.display = 'none';
    discovery.style.display = 'none';
    allDiv.style.display = 'block';

    const tabs = ['summarize', 'weaknesses', 'new_ideas', 'implementation'];
    tabs.forEach(tab => {
        const el = document.getElementById(`${tab}-content`);
        if (el && data.results[tab]) {
            el.innerHTML = `<div class="result-meta"><span class="meta-badge">${getTabLabel(tab)}</span><span>File: ${escapeHtml(data.paper_name)}</span><span>/</span><span>~${data.word_count.toLocaleString()} words</span><span>/</span><span>~${Number(data.estimated_tokens || 0).toLocaleString()} tokens</span><span>/</span><span>Model: ${escapeHtml(data.model || '')}</span></div><div class="result-surface markdown-body">${renderMarkdown(data.results[tab])}</div>`;
        }
    });

    switchTabById('summarize');

    content.style.display = 'block';
    document.getElementById('resultsActions').style.display = 'flex';
    currentDownloadName = sanitizeFilename(data.paper_name || 'research-analysis');
    showPaperInsights(data.chat_context);
}

function showDiscoveryResults(data) {
    clearStepInterval();
    hideAll();
    currentResearchContextId = data.context_id || data.paper_id || null;
    currentPaperName = `${data.field || 'Research'} - ${data.theme || 'Discovery'}`;

    const content = document.getElementById('resultsContent');
    const single = document.getElementById('singleResult');
    const allDiv = document.getElementById('allResults');
    const discovery = document.getElementById('discoveryResult');
    const meta = document.getElementById('discoveryMeta');
    const summary = document.getElementById('discoverySummary');
    const papers = document.getElementById('discoveryPapers');

    single.style.display = 'none';
    allDiv.style.display = 'none';
    discovery.style.display = 'block';

    meta.innerHTML = `
        <span class="meta-badge">Paper Discovery</span>
        <span>Field: ${escapeHtml(data.field)}</span>
        <span>/</span>
        <span>Theme: ${escapeHtml(data.theme)}</span>
        <span>/</span>
        <span>Model: ${escapeHtml(data.model || '')}</span>
        <span>/</span>
        <span>Budget: ${data.crawl_minutes ? `${escapeHtml(String(data.crawl_minutes))} min` : 'Manual stop'}</span>
    `;

    const queryText = (data.queries || []).map(query => `- ${query}`).join('\n');
    const sourceWarnings = (data.source_errors || []).length
        ? `\n\n## Source Notes\n${data.source_errors.map(item => `- ${item}`).join('\n')}`
        : '';
    const timingNote = `## Crawl Timing\n- Budget: ${data.crawl_minutes ? `${data.crawl_minutes} minute(s)` : 'Manual stop'}\n- Elapsed: ${data.elapsed_seconds || 0} seconds\n- Stopped by time limit: ${data.stopped_due_to_time ? 'Yes' : 'No'}`;
    summary.innerHTML = renderMarkdown(`## Focus\n${data.focus || 'Related research discovery'}\n\n${timingNote}\n\n## Search Queries\n${queryText}\n\n${data.ai_overview || ''}${sourceWarnings}`);
    papers.innerHTML = renderPaperCards(data.papers || []);

    currentResults = {
        discovery: `# Paper Discovery\n\nField: ${data.field}\nTheme: ${data.theme}\nModel: ${data.model || ''}\nBudget: ${data.crawl_minutes ? `${data.crawl_minutes} minute(s)` : 'Manual stop'}\nElapsed: ${data.elapsed_seconds || 0} seconds\nStopped by time limit: ${data.stopped_due_to_time ? 'Yes' : 'No'}\n\n## Search Queries\n${queryText}\n\n${data.ai_overview || ''}\n\n## Papers\n${(data.papers || []).map((paper, index) => `${index + 1}. ${paper.title} (${paper.source}) - ${paper.url}`).join('\n')}`
    };
    currentDownloadName = sanitizeFilename(`${data.field}-${data.theme}`);

    content.style.display = 'block';
    document.getElementById('resultsActions').style.display = 'flex';
    showPaperInsights(data.chat_context);
}

function switchTab(btn, tabId) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    switchTabById(tabId);
}

function switchTabById(tabId) {
    const tabIds = ['summarize', 'weaknesses', 'new_ideas', 'implementation'];
    tabIds.forEach(id => {
        const el = document.getElementById(`${id}-content`);
        if (el) el.style.display = id === tabId ? 'block' : 'none';
    });
}

function renderMarkdown(text) {
    if (!text) return '';

    const normalized = String(text).replace(/\r\n/g, '\n').trim();
    const blocks = normalized.split(/\n\s*\n/);
    const htmlBlocks = [];

    for (const rawBlock of blocks) {
        const block = rawBlock.trim();
        if (!block) continue;

        if (/^\|.+\|$/m.test(block) && block.split('\n').length >= 2) {
            htmlBlocks.push(renderMarkdownTable(block));
            continue;
        }

        if (/^(?:- |\* ).+/m.test(block)) {
            const items = block.split('\n').filter(line => /^(?:- |\* ).+/.test(line.trim()));
            htmlBlocks.push(`<ul>${items.map(line => `<li>${renderInlineMarkdown(line.trim().slice(2))}</li>`).join('')}</ul>`);
            continue;
        }

        if (/^\d+\. .+/m.test(block)) {
            const items = block.split('\n').filter(line => /^\d+\. .+/.test(line.trim()));
            htmlBlocks.push(`<ol>${items.map(line => `<li>${renderInlineMarkdown(line.trim().replace(/^\d+\. /, ''))}</li>`).join('')}</ol>`);
            continue;
        }

        if (block.startsWith('### ')) {
            htmlBlocks.push(`<h3>${renderInlineMarkdown(block.slice(4))}</h3>`);
            continue;
        }
        if (block.startsWith('## ')) {
            htmlBlocks.push(`<h2>${renderInlineMarkdown(block.slice(3))}</h2>`);
            continue;
        }
        if (block.startsWith('# ')) {
            htmlBlocks.push(`<h1>${renderInlineMarkdown(block.slice(2))}</h1>`);
            continue;
        }
        if (block.startsWith('> ')) {
            const quoteLines = block.split('\n').map(line => line.replace(/^>\s?/, '').trim()).join('<br>');
            htmlBlocks.push(`<blockquote>${renderInlineMarkdown(quoteLines)}</blockquote>`);
            continue;
        }

        const paragraph = block
            .split('\n')
            .map(line => renderInlineMarkdown(line.trim()))
            .join('<br>');
        htmlBlocks.push(`<p>${paragraph}</p>`);
    }

    return htmlBlocks.join('');
}

function renderMarkdownTable(block) {
    const rows = block
        .split('\n')
        .map(line => line.trim())
        .filter(line => line.startsWith('|') && line.endsWith('|'));

    if (rows.length < 2) {
        return `<p>${renderInlineMarkdown(block)}</p>`;
    }

    const headerCells = splitMarkdownRow(rows[0]);
    const separatorIndex = rows[1].includes('---') ? 1 : -1;
    const bodyRows = rows.slice(separatorIndex >= 0 ? 2 : 1);

    const thead = `<thead><tr>${headerCells.map(cell => `<th>${renderInlineMarkdown(cell)}</th>`).join('')}</tr></thead>`;
    const tbody = bodyRows.length
        ? `<tbody>${bodyRows.map(row => `<tr>${splitMarkdownRow(row).map(cell => `<td>${renderInlineMarkdown(cell)}</td>`).join('')}</tr>`).join('')}</tbody>`
        : '';

    return `<div class="table-wrap"><table>${thead}${tbody}</table></div>`;
}

function splitMarkdownRow(row) {
    return row
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|')
        .map(cell => cell.trim());
}

function renderInlineMarkdown(text) {
    return escapeHtml(text)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>');
}

function hideAll() {
    document.getElementById('emptyState').style.display = 'none';
    document.getElementById('loadingState').style.display = 'none';
    document.getElementById('resultsContent').style.display = 'none';
    document.getElementById('errorState').style.display = 'none';
    document.getElementById('stopResearchBtn').style.display = 'none';
    clearPaperInsights();
}

function showError(msg) {
    clearStepInterval();
    hideAll();
    document.getElementById('errorMessage').textContent = msg;
    document.getElementById('errorState').style.display = 'flex';
}

function clearError() {
    hideAll();
    document.getElementById('emptyState').style.display = 'flex';
    document.getElementById('resultsActions').style.display = 'none';
}

function setButtonsDisabled(disabled) {
    document.getElementById('discoverBtn').disabled = disabled;
    document.getElementById('analyzeBtn').disabled = disabled;
    document.getElementById('analyzeAllBtn').disabled = disabled;
}

function copyResults() {
    const texts = Object.values(currentResults).join('\n\n---\n\n');
    navigator.clipboard.writeText(texts).then(() => {
        const btn = document.querySelector('.action-btn');
        const original = btn.innerHTML;
        btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><polyline points="20,6 9,17 4,12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg> Copied`;
        setTimeout(() => { btn.innerHTML = original; }, 2000);
    });
}

function downloadResults() {
    const lines = [];
    const modeNames = {
        summarize: '# Paper Summary',
        weaknesses: '# Weakness Analysis',
        new_ideas: '# New Research Ideas',
        implementation: '# Implementation Roadmap',
        discovery: '# Paper Discovery'
    };

    lines.push('# ResearchLens Report');
    lines.push(`Paper: ${currentPaperName}`);
    lines.push(`Generated: ${new Date().toLocaleString()}`);
    lines.push('\n---\n');

    for (const [key, val] of Object.entries(currentResults)) {
        lines.push(modeNames[key] || `# ${key}`);
        lines.push('');
        lines.push(val);
        lines.push('\n---\n');
    }

    const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${currentDownloadName || 'research-analysis'}-${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(url);
}

async function loadHistoryList() {
    const list = document.getElementById('historyList');
    if (!list) return;

    try {
        const response = await fetch('/api/history');
        const data = await readJsonResponse(response, 'The history list could not be loaded.');
        historyItems = Array.isArray(data.items) ? data.items : [];
        renderHistoryList(historyItems);
    } catch (err) {
        list.innerHTML = `<div class="history-empty">${escapeHtml(err.message || 'Unable to load history.')}</div>`;
    }
}

function openHistoryPanel() {
    const modal = document.getElementById('historyModal');
    if (!modal) return;
    modal.style.display = 'flex';
    document.body.classList.add('history-open');
    loadHistoryList();
}

function closeHistoryPanel() {
    const modal = document.getElementById('historyModal');
    if (!modal) return;
    modal.style.display = 'none';
    document.body.classList.remove('history-open');
}

function handleChatKeydown(event) {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    const form = event.target.closest('form');
    if (form && !event.target.value.trim()) return;
    if (form) form.requestSubmit();
}

function prepareChatModel(model) {
    fetch('/api/ollama/prepare', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken()},
        body: JSON.stringify({ model }),
    }).catch(() => {});
}

function renderHistoryList(items) {
    const list = document.getElementById('historyList');
    if (!list) return;

    if (!items.length) {
        list.innerHTML = '<div class="history-empty">No saved history yet. Your next run will appear here automatically.</div>';
        return;
    }

    list.innerHTML = '';
    items.forEach(item => {
        const button = document.createElement('div');
        button.className = `history-item ${item.context_id === currentResearchContextId ? 'active' : ''}`;
        button.tabIndex = 0;
        button.setAttribute('role', 'button');
        button.innerHTML = `
            <div class="history-item-topline">
                <span class="history-item-kind">${escapeHtml(getHistoryKindLabel(item.kind, item.result_type))}</span>
                <span class="history-item-time">${escapeHtml(item.updated_at_label || item.created_at_label || '')}</span>
            </div>
            <div class="history-item-title">${escapeHtml(item.title || 'Untitled session')}</div>
            <div class="history-item-subtitle">${escapeHtml(item.subtitle || '')}</div>
            <div class="history-item-meta">
                <span>${escapeHtml(item.model || '')}</span>
                <span>${Number(item.chat_turn_count || 0)} chats</span>
            </div>
            <button class="history-delete-btn" type="button" title="Delete history">Delete</button>
        `;
        button.addEventListener('click', () => loadHistoryItem(item.context_id));
        button.addEventListener('keydown', event => {
            if (event.key === 'Enter' && event.target === button) loadHistoryItem(item.context_id);
        });
        button.querySelector('.history-delete-btn').addEventListener('click', event => {
            event.stopPropagation();
            deleteHistoryItem(item.context_id, item.title || 'this session');
        });
        list.appendChild(button);
    });
}

async function deleteHistoryItem(contextId, title) {
    if (!contextId || !window.confirm(`Delete ${title}? This cannot be undone.`)) return;
    try {
        const response = await fetch(`/api/history/${encodeURIComponent(contextId)}`, { method: 'DELETE', headers: {'X-CSRF-Token': getCsrfToken()} });
        await readJsonResponse(response, 'The history item could not be deleted.');
        if (currentResearchContextId === contextId) currentResearchContextId = null;
        await loadHistoryList();
    } catch (err) {
        showError(err.message || 'Unable to delete history item.');
    }
}

function getHistoryKindLabel(kind, resultType) {
    if (kind === 'discovery') return 'Discovery';
    if (resultType === 'analysis_full') return 'Full Report';
    return 'Analysis';
}

async function loadHistoryItem(contextId) {
    if (!contextId) return;

    try {
        const response = await fetch(`/api/history/${encodeURIComponent(contextId)}`);
        const data = await readJsonResponse(response, 'The history item could not be opened.');
        if (!data.item) {
            throw new Error('The history item is empty.');
        }
        restoreHistoryItem(data.item);
        await loadHistoryList();
    } catch (err) {
        showError(err.message || 'Unable to open that history item.');
    }
}

function restoreHistoryItem(item) {
    const payload = item.result_payload || {};
    const chatContext = {
        context_id: item.context_id,
        overview: item.overview || (item.context || {}).overview || '',
        parameters: (item.context || {}).parameters || {},
        kind: item.kind || (item.context || {}).kind || 'analysis',
        title: item.title || (item.context || {}).title || 'Research session',
    };

    if (item.kind === 'discovery') {
        showDiscoveryResults({
            ...payload,
            context_id: item.context_id,
            paper_id: item.context_id,
            chat_context: chatContext,
        });
    } else if (item.result_type === 'analysis_full') {
        showAllResults({
            ...payload,
            context_id: item.context_id,
            paper_id: item.context_id,
            chat_context: chatContext,
        });
    } else {
        const entries = Object.entries(payload.results || {});
        const [analysisMode, resultText] = entries[0] || ['summarize', ''];
        currentResults = payload.results || {};
        showSingleResult({
            ...payload,
            result: resultText,
            type: getTabLabel(analysisMode),
            context_id: item.context_id,
            paper_id: item.context_id,
            chat_context: chatContext,
        });
    }

    hydrateChatHistory(item.chat_history || []);
}

function hydrateChatHistory(history) {
    chatHistory = Array.isArray(history)
        ? history.map(turn => ({ role: turn.role, content: turn.content }))
        : [];

    const chatBody = document.getElementById('chatMessages');
    if (!chatBody) return;

    chatBody.innerHTML = '';
    if (!chatHistory.length) {
        appendChatMessage('assistant', getChatWelcomeMessage(currentChatContext));
    } else {
        chatHistory.forEach(turn => appendChatMessage(turn.role, turn.content));
    }

    const note = document.getElementById('chatNote');
    if (note) {
        note.textContent = getChatScopeNote(currentChatContext);
    }
}

function showPaperInsights(chatContext) {
    const insights = document.getElementById('paperInsights');
    const chatSection = document.getElementById('paperChat');
    const overview = document.getElementById('paperOverview');
    const chatPanel = document.getElementById('chatPanel');
    const startButton = document.getElementById('startChatBtn');

    if (!chatContext || !insights) {
        return clearPaperInsights();
    }

    overview.innerHTML = chatContext.overview
        ? `<h3 class="paper-overview-title">Paper Snapshot</h3><div class="paper-overview-body">${formatAssistantResponse(chatContext.overview)}</div>`
        : '<p class="paper-overview-empty">No overview available.</p>';
    insights.style.display = 'grid';
    if (chatPanel) {
        chatPanel.style.display = 'block';
    }
    if (chatSection) {
        chatSection.style.display = 'none';
    }
    if (startButton) startButton.style.display = currentResearchContextId ? 'inline-flex' : 'none';
    currentChatContext = chatContext;
    resetChat(false);
    appendChatMessage('assistant', getChatWelcomeMessage(chatContext));
}

function startResearchChat() {
    const chatPanel = document.getElementById('chatPanel');
    const chatSection = document.getElementById('paperChat');
    const startButton = document.getElementById('startChatBtn');
    if (chatPanel) chatPanel.style.display = 'block';
    if (chatSection) {
        chatSection.style.display = 'flex';
        chatSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    if (startButton) startButton.style.display = 'none';
}

function appendChatMessage(role, message) {
    if (!message) return;
    const chatBody = document.getElementById('chatMessages');
    if (!chatBody) return;
    const bubble = document.createElement('div');
    bubble.className = `chat-message ${role}`;
    bubble.innerHTML = role === 'assistant'
        ? `<div class="chat-message-content">${formatAssistantResponse(message)}</div>`
        : `<div class="chat-message-content chat-user-text">${escapeHtml(message)}</div>`;
    chatBody.appendChild(bubble);
    chatBody.scrollTop = chatBody.scrollHeight;
}

function cleanAssistantText(text) {
    return String(text || '')
        .replace(/\r\n/g, '\n')
        .replace(/^\s{0,3}#{1,6}\s*/gm, '')
        .replace(/\*\*/g, '')
        .replace(/\*/g, '')
        .replace(/^\s*[-•]\s+/gm, '')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
}

function formatAssistantResponse(text) {
    const cleaned = cleanAssistantText(text);
    if (!cleaned) return '';

    const blocks = cleaned.split(/\n\s*\n/).map(block => block.trim()).filter(Boolean);
    const html = blocks.map((block, index) => {
        const lines = block.split('\n').map(line => line.trim()).filter(Boolean);

        if (lines.length === 1 && index === 0 && lines[0].length <= 140) {
            return `<p class="chat-answer-lead">${escapeHtml(lines[0])}</p>`;
        }

        if (lines.every(line => /^\d+\.\s+/.test(line))) {
            return `<ol class="chat-answer-list">${lines.map(line => `<li>${escapeHtml(line.replace(/^\d+\.\s+/, ''))}</li>`).join('')}</ol>`;
        }

        if (lines.length > 1 && lines.every(line => line.length < 120)) {
            return `<ol class="chat-answer-list">${lines.map(line => `<li>${escapeHtml(line.replace(/^\d+\.\s+/, ''))}</li>`).join('')}</ol>`;
        }

        return `<p>${lines.map(line => escapeHtml(line)).join('<br>')}</p>`;
    }).join('');

    return html || `<p>${escapeHtml(cleaned)}</p>`;
}

async function submitChat(event) {
    event.preventDefault();
    if (chatRequestInFlight || !currentResearchContextId) return;

    const input = document.getElementById('chatInput');
    const note = document.getElementById('chatNote');
    const message = (input.value || '').trim();
    if (!message) return;
    appendChatMessage('user', message);
    chatHistory.push({ role: 'user', content: message });
    input.value = '';
    chatRequestInFlight = true;
    if (note) {
        note.innerHTML = `
            <span class="chat-thinking-inline">
                <span class="chat-thinking-dot"></span>
                <span class="chat-thinking-dot"></span>
                <span class="chat-thinking-dot"></span>
                <span class="chat-thinking-text">Thinking...</span>
            </span>
        `;
    }

    try {
        const response = await fetch('/api/paper_chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
            body: JSON.stringify({
                context_id: currentResearchContextId,
                paper_id: currentResearchContextId,
                message,
                history: chatHistory.slice(-6),
                web_search: document.getElementById('copilotWebSearch')?.checked !== false,
                model: document.getElementById('chatModelName')?.value || '',
            }),
        });
        const data = await readJsonResponse(response, 'The chat request failed.');
        if (data.error) {
            if (note) {
                note.textContent = data.error;
            }
            return;
        }
        appendChatMessage('assistant', data.reply);
        chatHistory = Array.isArray(data.history)
            ? data.history.map(turn => ({ role: turn.role, content: turn.content }))
            : [...chatHistory, { role: 'assistant', content: data.reply }];
        if (note) {
            note.textContent = getChatScopeNote(currentChatContext);
        }
        loadHistoryList();
    } catch (err) {
        if (note) {
            note.textContent = err.message || 'Chat request failed. Please try again.';
        }
    } finally {
        chatRequestInFlight = false;
    }
}

async function resetChat(syncServer = true) {
    chatHistory = [];
    const chatBody = document.getElementById('chatMessages');
    if (chatBody) {
        chatBody.innerHTML = '';
    }
    const note = document.getElementById('chatNote');
    if (note) {
        note.textContent = getChatScopeNote(currentChatContext);
    }
    if (syncServer && currentResearchContextId) {
        try {
            await fetch(`/api/history/${encodeURIComponent(currentResearchContextId)}/chat/reset`, {
                method: 'POST',
                headers: {'X-CSRF-Token': getCsrfToken()},
            });
            loadHistoryList();
        } catch (err) {
            if (note) {
                note.textContent = 'Chat was cleared locally, but saved history could not be updated.';
            }
        }
    }
}

function clearPaperInsights() {
    const insights = document.getElementById('paperInsights');
    const chatSection = document.getElementById('paperChat');
    const overview = document.getElementById('paperOverview');
    const chatPanel = document.getElementById('chatPanel');
    const startButton = document.getElementById('startChatBtn');
    if (insights) {
        insights.style.display = 'none';
    }
    if (chatSection) {
        chatSection.style.display = 'none';
    }
    if (overview) {
        overview.innerHTML = '';
    }
    if (chatPanel) {
        chatPanel.style.display = 'none';
    }
    if (startButton) startButton.style.display = 'none';
    currentResearchContextId = null;
    currentChatContext = null;
    resetChat(false);
}

function getChatWelcomeMessage(chatContext) {
    if (!chatContext) {
        return 'Ask about the latest research output and I will answer from the full current context.';
    }
    if (chatContext.kind === 'discovery') {
        return 'Ask me to explain the current discovery output, compare papers, refine the theme, or suggest a stronger next prompt based on the full current research results.';
    }
    return 'Ask me to explain this research output, summarize methods, clarify findings, discuss weaknesses, or suggest a better next prompt based on the full current analysis.';
}

function getChatScopeNote(chatContext) {
    if (!chatContext) {
        return 'Chat is scoped to the latest research results.';
    }
    return chatContext.kind === 'discovery'
        ? 'Chat is scoped to the latest discovered papers, overview, and source notes.'
        : 'Chat is scoped to the current paper and its extracted analysis context.';
}

function getModeLabel(mode) {
    const labels = {
        summarize: 'Paper summary',
        weaknesses: 'Weakness analysis',
        new_ideas: 'New idea generation',
        implementation: 'Implementation roadmap'
    };
    return labels[mode] || mode;
}

function getTabLabel(tab) {
    const labels = {
        summarize: 'Summary',
        weaknesses: 'Weaknesses',
        new_ideas: 'New Ideas',
        implementation: 'Roadmap'
    };
    return labels[tab] || tab;
}

function renderPaperCards(papers) {
    if (!papers.length) {
        return '<p class="paper-empty">No papers found.</p>';
    }

    return papers.map((paper, index) => `
        <article class="paper-card">
            <div class="paper-card-head">
                <span class="paper-rank">${String(index + 1).padStart(2, '0')}</span>
                <span class="paper-source">${escapeHtml(paper.source || 'Source unknown')}</span>
                <span class="paper-year">${escapeHtml(paper.year || 'Year n/a')}</span>
            </div>
            <h3 class="paper-title">${escapeHtml(paper.title || 'Untitled')}</h3>
            <p class="paper-authors">${escapeHtml(paper.authors || 'Authors unavailable')}</p>
            <p class="paper-summary">${escapeHtml(paper.summary || 'No summary available.')}</p>
            <div class="paper-card-foot">
                <span class="paper-query">${escapeHtml(paper.query || '')}</span>
                <a class="paper-link" href="${escapeAttribute(paper.url || '#')}" target="_blank" rel="noopener noreferrer">Open source</a>
            </div>
        </article>
    `).join('');
}

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function escapeAttribute(text) {
    return escapeHtml(text);
}

function sanitizeFilename(text) {
    return String(text || 'research-analysis')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 80) || 'research-analysis';
}
