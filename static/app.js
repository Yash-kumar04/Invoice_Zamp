/**
 * Zamp Invoice Decision Engine - Frontend Application
 * Vanilla JS SPA handling pipeline streaming, dashboard analytics,
 * review queue, master data, and rules configuration.
 */

// ── Application State ───────────────────────────────────────────────────────
const state = {
  activeTab: 'run',
  activeRunId: null,
  activeSampleId: null,
  eventSource: null,
  samples: [],
  kpis: null,
  runs: [],
  reviewModal: {
    isOpen: false,
    runId: null,
    action: null,
  },
};

// 12 Pipeline Stage Metadata per SPEC B5
const PIPELINE_STAGES = [
  { id: 1, name: 'Ingest', desc: 'SHA-256 integrity, page count, and text layer detection' },
  { id: 2, name: 'Extract', desc: 'Gemini structured extraction with fallback model chain' },
  { id: 3, name: 'Verify extraction', desc: 'Required fields, arithmetic balance, and text grounding' },
  { id: 4, name: 'Normalise', desc: 'Vendor name, invoice number key, PO key, and net amounts' },
  { id: 5, name: 'Vendor check', desc: 'Master vendor match, active status, tax ID, bank account, and remit domain' },
  { id: 6, name: 'Duplicate check', desc: 'Exact hash, normalized invoice number, and fuzzy amount/date duplicates' },
  { id: 7, name: 'PO match', desc: 'Direct reference or single open PO matching vendor ownership' },
  { id: 8, name: 'Lines and 3-way', desc: 'Line item mapping (SKU/description), unit prices, and GRN receipts' },
  { id: 9, name: 'Amounts', desc: 'PO open balance consumption, tax calculation, and 2% / $100 tolerance' },
  { id: 10, name: 'Policy', desc: 'Auto-approve threshold check ($75,000 gross limit)' },
  { id: 11, name: 'Decide and explain', desc: 'Deterministic precedence resolution and explanation generation' },
  { id: 12, name: 'Act', desc: 'Post to ledger (if approved), hold for review, or draft vendor email' },
];

const DEMO_ORDER = ['S0', 'S1', 'E1a', 'E1b', 'E2', 'E4', 'E3'];

// ── Utility Functions ───────────────────────────────────────────────────────

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  const bgColors = {
    info: 'bg-slate-900 text-white',
    success: 'bg-emerald-600 text-white',
    warning: 'bg-amber-600 text-white',
    error: 'bg-rose-600 text-white',
  };

  toast.className = `pointer-events-auto px-4 py-2.5 rounded-lg shadow-lg text-xs font-medium flex items-center space-x-2 transition-all transform translate-y-2 opacity-0 duration-200 ${bgColors[type] || bgColors.info}`;
  toast.innerHTML = `
    <span>${message}</span>
  `;

  container.appendChild(toast);
  requestAnimationFrame(() => {
    toast.classList.remove('translate-y-2', 'opacity-0');
  });

  setTimeout(() => {
    toast.classList.add('opacity-0', 'translate-y-2');
    setTimeout(() => toast.remove(), 200);
  }, 4000);
}

function formatCurrency(val, curr = 'USD') {
  if (val === null || val === undefined || isNaN(val)) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: curr || 'USD',
  }).format(val);
}

function displayVal(val) {
  if (val === null || val === undefined) return '—';
  const str = String(val).trim();
  if (!str || str.toLowerCase() === 'null' || str.toLowerCase() === 'none' || str.toLowerCase() === 'none printed') {
    return '—';
  }
  return str;
}

function getFlaggedFields(decision) {
  const flagged = new Set();
  if (!decision || !decision.checks) return flagged;

  for (const c of decision.checks) {
    if (c.result !== 'fail' && c.result !== 'warn') continue;
    const cid = c.check_id || c.id || '';
    const ev = c.evidence || {};

    if (cid === 'EXT-01') {
      const missing = ev.missing_fields || [];
      if (missing.includes('invoice_number')) flagged.add('invoice_number');
      if (missing.includes('invoice_date')) flagged.add('invoice_date');
      if (missing.includes('vendor_name')) flagged.add('vendor');
      if (missing.includes('total_amount')) flagged.add('total_gross');
    } else if (cid === 'EXT-02') {
      if (ev.field) {
        if (ev.field === 'vendor_name') flagged.add('vendor');
        else if (ev.field === 'invoice_number') flagged.add('invoice_number');
        else if (ev.field === 'invoice_date') flagged.add('invoice_date');
        else if (ev.field === 'total_amount') flagged.add('total_gross');
        else flagged.add(ev.field);
      }
    } else if (cid === 'EXT-03') {
      flagged.add('invoice_date');
    } else if (cid === 'EXT-04') {
      flagged.add('due_date');
    } else if (['VEN-01', 'VEN-02', 'VEN-03'].includes(cid)) {
      flagged.add('vendor');
    } else if (cid === 'VEN-04') {
      flagged.add('bank_last4');
    } else if (cid === 'VEN-05') {
      flagged.add('remit_email');
    } else if (['DUP-01', 'DUP-02'].includes(cid)) {
      flagged.add('invoice_number');
    } else if (['PO-01', 'PO-02', 'PO-03'].includes(cid)) {
      flagged.add('po_reference');
    } else if (cid === 'LINE-01' || cid === '3W-01') {
      flagged.add('lines');
    } else if (cid === 'BAL-01') {
      flagged.add('net');
    } else if (cid === 'TAX-01') {
      flagged.add('tax');
    } else if (cid === 'AMT-01') {
      flagged.add('total_gross');
      flagged.add('net');
    } else if (cid === 'POL-01') {
      flagged.add('total_gross');
    }
  }
  return flagged;
}

function formatDate(isoStr) {
  if (!isoStr) return '—';
  try {
    const d = new Date(isoStr);
    return isNaN(d.getTime()) ? isoStr : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return isoStr;
  }
}

function getStatusBadgeHtml(status, severity) {
  const s = (status || '').toUpperCase();
  const sev = (severity || '').toUpperCase();

  let statusClass = 'bg-slate-100 text-slate-700 border-slate-200';
  let label = s;

  if (s === 'APPROVED') {
    statusClass = 'bg-emerald-50 text-emerald-700 border-emerald-200';
    label = 'Approved';
  } else if (s === 'NEEDS_REVIEW') {
    statusClass = 'bg-amber-50 text-amber-700 border-amber-200';
    label = 'Needs Review';
  } else if (s === 'NEEDS_INFO') {
    statusClass = 'bg-slate-100 text-slate-700 border-slate-300';
    label = 'Waiting on Vendor';
  } else if (s === 'REJECTED') {
    statusClass = 'bg-rose-50 text-rose-700 border-rose-200';
    label = 'Rejected';
  } else if (s === 'ERROR') {
    statusClass = 'bg-red-50 text-red-700 border-red-200';
    label = 'Error';
  } else if (s === 'RUNNING') {
    statusClass = 'bg-blue-50 text-blue-700 border-blue-200';
    label = 'Processing';
  }

  let html = `<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border ${statusClass}">${label}</span>`;

  if (sev === 'CRITICAL') {
    html += ` <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold bg-purple-100 text-purple-800 border border-purple-300">CRITICAL</span>`;
  }

  return html;
}

// ── Tab Navigation ──────────────────────────────────────────────────────────

function initNavigation() {
  const tabButtons = document.querySelectorAll('#tab-nav .tab-btn');
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.getAttribute('data-tab');
      switchTab(tab);
    });
  });

  const resetBtn = document.getElementById('btn-reset-demo');
  if (resetBtn) {
    resetBtn.addEventListener('click', handleResetDemo);
  }
}

function switchTab(tabName) {
  state.activeTab = tabName;

  // Update button styling
  document.querySelectorAll('#tab-nav .tab-btn').forEach(btn => {
    const isCurrent = btn.getAttribute('data-tab') === tabName;
    if (isCurrent) {
      btn.className = 'tab-btn px-3 py-2 rounded-md text-sm font-medium transition-colors text-blue-600 bg-blue-50';
    } else {
      btn.className = 'tab-btn px-3 py-2 rounded-md text-sm font-medium transition-colors text-slate-600 hover:text-slate-900 hover:bg-slate-100 flex items-center space-x-1.5';
    }
  });

  // Switch view visibility
  document.querySelectorAll('.tab-view').forEach(view => {
    view.classList.add('hidden');
  });

  const targetView = document.getElementById(`view-${tabName}`);
  if (targetView) {
    targetView.classList.remove('hidden');
  }

  // Load relevant data for tab
  if (tabName === 'dashboard') {
    loadDashboard();
  } else if (tabName === 'review') {
    loadReviewQueue();
  } else if (tabName === 'rules') {
    loadRulesAndAssumptions();
  } else if (tabName === 'data') {
    loadMasterData();
  }
}

// ── Reset Demo Data ─────────────────────────────────────────────────────────

async function handleResetDemo() {
  const confirmed = confirm(
    'Reset all demo data?\n\nThis will clear all processed invoice runs, reset PO remaining balances and goods receipt counters to their initial demo state.'
  );
  if (!confirmed) return;

  try {
    const res = await fetch('/api/reset', { method: 'POST' });
    if (!res.ok) throw new Error('Reset failed');
    showToast('Demo data and run history have been reset.', 'success');

    // Reload active tab
    if (state.activeTab === 'run') {
      resetRunView();
      loadSamples();
    } else if (state.activeTab === 'dashboard') {
      loadDashboard();
    } else if (state.activeTab === 'review') {
      loadReviewQueue();
    } else if (state.activeTab === 'data') {
      loadMasterData();
    }
    updateReviewBadge();
  } catch (err) {
    showToast(`Failed to reset demo data: ${err.message}`, 'error');
  }
}

// ── Tab 1: Run Pipeline & Live Streaming ────────────────────────────────────

function initRunView() {
  initTimeline();
  loadSamples();
  initDropZone();
}

function initTimeline() {
  const container = document.getElementById('stage-timeline');
  if (!container) return;

  container.innerHTML = PIPELINE_STAGES.map(stage => `
    <div id="stage-item-${stage.id}" class="stage-item relative pl-8 pb-3 group">
      <!-- Status Icon Bubble -->
      <div id="stage-icon-${stage.id}" class="absolute -left-0.5 top-0.5 w-7 h-7 rounded-full bg-slate-100 border-2 border-slate-300 flex items-center justify-center text-xs font-semibold text-slate-500 z-10 transition-colors">
        ${stage.id}
      </div>

      <!-- Stage Card -->
      <div class="bg-slate-50/50 hover:bg-slate-50 rounded-lg p-2.5 border border-slate-200 transition-colors">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-2">
            <span class="text-xs font-semibold text-slate-800">${stage.name}</span>
            <span id="stage-duration-${stage.id}" class="hidden text-[10px] text-slate-400 font-mono"></span>
          </div>
          <button id="stage-toggle-${stage.id}" class="hidden text-[11px] text-blue-600 hover:text-blue-800 focus:outline-hidden">
            Details ▾
          </button>
        </div>
        <p id="stage-summary-${stage.id}" class="text-[11px] text-slate-500 mt-0.5 leading-relaxed">
          ${stage.desc}
        </p>

        <!-- Collapsible Evidence/Findings Drawer -->
        <div id="stage-details-${stage.id}" class="hidden mt-2 pt-2 border-t border-slate-200 text-[11px] font-mono bg-white p-2 rounded-sm text-slate-700 overflow-x-auto max-h-48"></div>
      </div>
    </div>
  `).join('');

  // Wire up toggles
  PIPELINE_STAGES.forEach(stage => {
    const btn = document.getElementById(`stage-toggle-${stage.id}`);
    const details = document.getElementById(`stage-details-${stage.id}`);
    if (btn && details) {
      btn.addEventListener('click', () => {
        const isHidden = details.classList.contains('hidden');
        if (isHidden) {
          details.classList.remove('hidden');
          btn.textContent = 'Hide ▴';
        } else {
          details.classList.add('hidden');
          btn.textContent = 'Details ▾';
        }
      });
    }
  });
}

function resetRunView() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  state.activeRunId = null;

  document.getElementById('active-run-id-label').textContent = 'No active run';
  document.getElementById('live-indicator').classList.add('hidden');
  document.getElementById('decision-card-container').classList.add('hidden');
  document.getElementById('decision-card-container').innerHTML = '';

  const modelBadge = document.getElementById('model-source-badge');
  if (modelBadge) modelBadge.classList.add('hidden');

  const fieldsContainer = document.getElementById('extracted-fields-container');
  if (fieldsContainer) {
    fieldsContainer.innerHTML = '<p class="text-slate-400 text-center py-6">Extracted data will appear here once extracted by the model.</p>';
  }

  initTimeline();
}

async function loadSamples() {
  const container = document.getElementById('sample-chips-container');
  if (!container) return;

  try {
    const res = await fetch('/api/samples');
    if (!res.ok) throw new Error('Failed to load demo samples');
    const data = await res.json();
    state.samples = data.samples || [];

    // Sort according to canonical demo order
    const sampleMap = new Map(state.samples.map(s => [s.id, s]));
    const ordered = DEMO_ORDER.map(id => sampleMap.get(id)).filter(Boolean);

    container.innerHTML = ordered.map(s => `
      <div data-sample-id="${s.id}" class="sample-chip p-2.5 rounded-lg border border-slate-200 hover:border-blue-400 hover:bg-blue-50/40 cursor-pointer transition-all flex items-center justify-between group">
        <div class="flex items-center space-x-2.5 overflow-hidden min-w-0 flex-1">
          <span class="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-bold bg-slate-100 text-slate-700 group-hover:bg-blue-600 group-hover:text-white transition-colors shrink-0">
            ${s.id}
          </span>
          <div class="min-w-0 flex-1">
            <p class="text-xs font-semibold text-slate-800 truncate">${escapeHtml(s.label || s.id)}</p>
            <p class="text-[11px] text-slate-500 truncate">${escapeHtml(s.vendor_name)}</p>
          </div>
        </div>
        <div class="text-right shrink-0 ml-2">
          <span class="text-xs font-mono font-medium text-slate-800 whitespace-nowrap block">${formatCurrency(s.total_amount)}</span>
        </div>
        <svg class="w-4 h-4 ml-1.5 text-slate-400 group-hover:text-blue-600 transform group-hover:translate-x-0.5 transition-all shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
        </svg>
      </div>
    `).join('');

    // Attach click listeners to chips
    container.querySelectorAll('.sample-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const sId = chip.getAttribute('data-sample-id');
        startSampleRun(sId);
      });
    });
  } catch (err) {
    container.innerHTML = `<p class="text-xs text-rose-500 py-2">Error loading samples: ${err.message}</p>`;
  }
}

function initDropZone() {
  const dropZone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');
  if (!dropZone || !fileInput) return;

  dropZone.addEventListener('click', () => fileInput.click());

  ['dragenter', 'dragover'].forEach(eventName => {
    dropZone.addEventListener(eventName, e => {
      e.preventDefault();
      dropZone.classList.add('border-blue-500', 'bg-blue-50');
    });
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, e => {
      e.preventDefault();
      dropZone.classList.remove('border-blue-500', 'bg-blue-50');
    });
  });

  dropZone.addEventListener('drop', e => {
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      handleFileUpload(files[0]);
    }
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) {
      handleFileUpload(fileInput.files[0]);
    }
  });
}

async function handleFileUpload(file) {
  // Validate file size and type client-side
  if (file.size > 10 * 1024 * 1024) {
    showToast('File size exceeds the 10 MB limit.', 'error');
    return;
  }
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['pdf', 'png', 'jpg', 'jpeg'].includes(ext)) {
    showToast('Invalid format. Only PDF, PNG, and JPEG documents are permitted.', 'error');
    return;
  }

  // Preview local file in right panel
  const previewContainer = document.getElementById('document-preview-container');
  const previewOpenLink = document.getElementById('preview-open-link');
  const objectUrl = URL.createObjectURL(file);

  if (ext === 'pdf') {
    previewContainer.innerHTML = `<iframe src="${objectUrl}" class="w-full h-full border-0"></iframe>`;
  } else {
    previewContainer.innerHTML = `<img src="${objectUrl}" class="max-w-full max-h-full object-contain mx-auto" alt="Uploaded Document" />`;
  }
  previewOpenLink.href = objectUrl;
  previewOpenLink.classList.remove('hidden');

  resetRunView();
  state.activeFile = file;
  state.activeSampleId = null;
  document.getElementById('active-run-id-label').textContent = 'Uploading & Initializing...';
  document.getElementById('live-indicator').classList.remove('hidden');

  try {
    const formData = new FormData();
    formData.append('file', file);

    const res = await fetch('/api/runs', {
      method: 'POST',
      body: formData,
    });

    if (res.status === 429) {
      showToast('Rate limit exceeded: maximum 20 runs per IP per hour.', 'warning');
      document.getElementById('live-indicator').classList.add('hidden');
      return;
    }

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to submit file');
    }

    const { run_id } = await res.json();
    state.activeRunId = run_id;
    document.getElementById('active-run-id-label').textContent = run_id;
    connectEventSource(run_id);
  } catch (err) {
    showToast(`Upload error: ${err.message}`, 'error');
    document.getElementById('live-indicator').classList.add('hidden');
  }
}

async function startSampleRun(sampleId) {
  state.activeSampleId = sampleId;
  const sample = state.samples.find(s => s.id === sampleId);

  // Update preview panel immediately
  const previewContainer = document.getElementById('document-preview-container');
  const previewOpenLink = document.getElementById('preview-open-link');
  const fileUrl = `/api/samples/${sampleId}/file`;

  previewContainer.innerHTML = `<iframe src="${fileUrl}" class="w-full h-full border-0" title="Invoice Preview"></iframe>`;
  previewOpenLink.href = fileUrl;
  previewOpenLink.classList.remove('hidden');

  resetRunView();
  state.activeSampleId = sampleId;
  state.activeFile = null;
  document.getElementById('active-run-id-label').textContent = `Starting ${sampleId}...`;
  document.getElementById('live-indicator').classList.remove('hidden');

  try {
    const formData = new FormData();
    formData.append('sample_id', sampleId);

    const res = await fetch('/api/runs', {
      method: 'POST',
      body: formData,
    });

    if (res.status === 429) {
      showToast('Rate limit exceeded: maximum 20 runs per IP per hour. Please wait a moment.', 'warning');
      document.getElementById('live-indicator').classList.add('hidden');
      return;
    }

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to start run');
    }

    const { run_id } = await res.json();
    state.activeRunId = run_id;
    document.getElementById('active-run-id-label').textContent = `${run_id} (${sampleId})`;
    connectEventSource(run_id);
  } catch (err) {
    showToast(`Error: ${err.message}`, 'error');
    document.getElementById('live-indicator').classList.add('hidden');
  }
}

function connectEventSource(runId) {
  if (state.eventSource) {
    state.eventSource.close();
  }

  const es = new EventSource(`/api/runs/${runId}/stream`);
  state.eventSource = es;

  es.addEventListener('started', e => {
    try {
      const evt = JSON.parse(e.data);
      updateStageStarted(evt.stage_number);
    } catch (err) {
      console.error('Error handling started event:', err);
    }
  });

  es.addEventListener('finished', e => {
    try {
      const evt = JSON.parse(e.data);
      updateStageFinished(evt);
    } catch (err) {
      console.error('Error handling finished event:', err);
    }
  });

  es.addEventListener('done', e => {
    try {
      const decision = JSON.parse(e.data);
      handleRunDone(decision);
    } catch (err) {
      console.error('Error handling done event:', err);
    } finally {
      es.close();
      state.eventSource = null;
      document.getElementById('live-indicator').classList.add('hidden');
    }
  });

  es.onerror = async err => {
    console.warn('SSE stream closed or encountered error:', err);
    es.close();
    state.eventSource = null;
    document.getElementById('live-indicator').classList.add('hidden');

    // Fetch the saved run status before rendering the final card
    try {
      const res = await fetch(`/api/runs/${runId}`);
      if (res.ok) {
        const detail = await res.json();
        handleRunDone(detail);
      }
    } catch (fetchErr) {
      console.error('Failed to fetch run status on stream disconnect:', fetchErr);
    }
  };
}

function updateStageStarted(stageNo) {
  const icon = document.getElementById(`stage-icon-${stageNo}`);
  if (icon) {
    icon.className = 'absolute -left-0.5 top-0.5 w-7 h-7 rounded-full bg-blue-50 border-2 border-blue-600 flex items-center justify-center text-xs font-semibold text-blue-600 z-10';
    icon.innerHTML = `
      <svg class="animate-spin-slow h-3.5 w-3.5 text-blue-600" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
      </svg>
    `;
  }
}

function updateStageFinished(evt) {
  const stageNo = evt.stage_number;
  const icon = document.getElementById(`stage-icon-${stageNo}`);
  const summaryEl = document.getElementById(`stage-summary-${stageNo}`);
  const durationEl = document.getElementById(`stage-duration-${stageNo}`);
  const toggleBtn = document.getElementById(`stage-toggle-${stageNo}`);
  const detailsEl = document.getElementById(`stage-details-${stageNo}`);

  const data = evt.data || {};
  const result = data.result || 'pass';
  const duration = evt.duration_ms || data.duration_ms || 0;
  const summary = data.summary || evt.stage_name;

  if (summaryEl) summaryEl.textContent = summary;
  if (durationEl) {
    durationEl.textContent = `${duration.toFixed(0)} ms`;
    durationEl.classList.remove('hidden');
  }

  // Update status icon
  if (icon) {
    if (result === 'pass') {
      icon.className = 'absolute -left-0.5 top-0.5 w-7 h-7 rounded-full bg-emerald-100 border-2 border-emerald-500 flex items-center justify-center text-emerald-700 z-10';
      icon.innerHTML = `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" /></svg>`;
    } else if (result === 'warn') {
      icon.className = 'absolute -left-0.5 top-0.5 w-7 h-7 rounded-full bg-amber-100 border-2 border-amber-500 flex items-center justify-center text-amber-700 z-10';
      icon.innerHTML = `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>`;
    } else if (result === 'fail') {
      icon.className = 'absolute -left-0.5 top-0.5 w-7 h-7 rounded-full bg-rose-100 border-2 border-rose-500 flex items-center justify-center text-rose-700 z-10';
      icon.innerHTML = `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>`;
    } else {
      icon.className = 'absolute -left-0.5 top-0.5 w-7 h-7 rounded-full bg-slate-100 border-2 border-slate-300 flex items-center justify-center text-slate-400 z-10';
      icon.innerHTML = `<span class="text-xs">―</span>`;
    }
  }

  // Populate findings/evidence
  if (data.findings && Object.keys(data.findings).length > 0) {
    if (toggleBtn) toggleBtn.classList.remove('hidden');
    if (detailsEl) {
      detailsEl.innerHTML = `<pre class="text-[10px] whitespace-pre-wrap">${escapeHtml(JSON.stringify(data.findings, null, 2))}</pre>`;
    }
  }

  // If Stage 2 finished, populate the extracted fields panel
  if (stageNo === 2 && data.findings) {
    renderExtractedFields(data.findings);
  }
}

function renderExtractedFields(findings, decision = null) {
  const container = document.getElementById('extracted-fields-container');
  const badge = document.getElementById('model-source-badge');
  if (!container) return;

  let f = findings || {};
  if (Object.keys(f).length === 0 && decision) {
    if (decision.extraction?.fields) f = decision.extraction.fields;
    else if (decision.extraction && typeof decision.extraction === 'object') f = decision.extraction;
    else if (decision.extraction_json) {
      try {
        f = typeof decision.extraction_json === 'string' ? JSON.parse(decision.extraction_json) : decision.extraction_json;
      } catch (e) { }
    }
  }
  const source = decision?.extraction?.source || f.source || f._source;
  const model = decision?.extraction?.model || f.model || f._model;

  if (badge) {
    badge.classList.remove('hidden');
    if (source === 'PDF text extraction') {
      badge.className = 'text-[11px] px-2 py-0.5 rounded-full font-medium bg-indigo-50 text-indigo-700 border border-indigo-200';
      badge.textContent = 'PDF text extraction';
    } else if (source === 'cached') {
      badge.className = 'text-[11px] px-2 py-0.5 rounded-full font-medium bg-amber-50 text-amber-700 border border-amber-200';
      badge.textContent = `${model || 'fixture'} (cached)`;
    } else if (source === 'live') {
      badge.className = 'text-[11px] px-2 py-0.5 rounded-full font-medium bg-blue-50 text-blue-700 border border-blue-200';
      badge.textContent = `${model || 'gemini'} (live)`;
    } else if (source === 'failed') {
      badge.className = 'text-[11px] px-2 py-0.5 rounded-full font-medium bg-rose-50 text-rose-700 border border-rose-200';
      badge.textContent = 'Extraction failed';
    } else {
      badge.className = 'text-[11px] px-2 py-0.5 rounded-full font-medium bg-slate-100 text-slate-600 border border-slate-300';
      badge.textContent = 'Source unavailable';
    }
  }

  const flagged = getFlaggedFields(decision);

  const vendorVal = decision?.vendor?.name || decision?.vendor_name || f.vendor_name;
  const invNumVal = decision?.invoice_number || f.invoice_number;
  const invDateVal = f.invoice_date || decision?.invoice_date;
  const dueDateVal = f.due_date || decision?.due_date;
  const poRefVal = decision?.po?.po_number || decision?.po_number || f.po_reference;
  const curr = decision?.currency || f.currency || 'USD';
  const netVal = decision?.net ?? f.net ?? f.subtotal;
  const taxVal = f.tax_amount !== null && f.tax_amount !== undefined
    ? f.tax_amount
    : (decision?.gross !== undefined && decision?.net !== undefined ? decision.gross - decision.net : null);
  const grossVal = decision?.gross ?? f.total_amount;
  const bankVal = f.bank_account_last4 ? `•••• ${f.bank_account_last4}` : null;
  const remitVal = f.vendor_email;
  const lines = f.line_items || [];
  const linesCountVal = lines.length > 0 ? `${lines.length} line(s)` : null;

  const rows = [
    { key: 'vendor', label: 'Vendor', val: displayVal(vendorVal) },
    { key: 'invoice_number', label: 'Invoice #', val: displayVal(invNumVal) },
    { key: 'invoice_date', label: 'Invoice Date', val: displayVal(invDateVal) },
    { key: 'due_date', label: 'Due Date', val: displayVal(dueDateVal) },
    { key: 'po_reference', label: 'PO Reference', val: displayVal(poRefVal) },
    { key: 'net', label: 'Net Amount', val: netVal !== null && netVal !== undefined ? formatCurrency(netVal, curr) : '—' },
    { key: 'tax', label: 'Tax Amount', val: taxVal !== null && taxVal !== undefined ? formatCurrency(taxVal, curr) : '—' },
    { key: 'total_gross', label: 'Total Gross', val: grossVal !== null && grossVal !== undefined ? formatCurrency(grossVal, curr) : '—' },
    { key: 'currency', label: 'Currency', val: displayVal(curr) },
    { key: 'bank_last4', label: 'Bank Last 4', val: displayVal(bankVal) },
    { key: 'remit_email', label: 'Remit Email', val: displayVal(remitVal) },
    { key: 'lines', label: 'Line Items Count', val: displayVal(linesCountVal) },
  ];

  let linesHtml = '';
  if (lines.length > 0) {
    const isLinesFlagged = flagged.has('lines');
    const tableBorder = isLinesFlagged ? 'border-amber-300 bg-amber-50/40' : 'border-slate-200 bg-slate-50/50';
    linesHtml = `
      <div class="mt-3 border ${tableBorder} rounded-lg p-2.5">
        <div class="flex items-center justify-between mb-2">
          <span class="text-[11px] font-semibold ${isLinesFlagged ? 'text-amber-900' : 'text-slate-700'}">Extracted Lines</span>
          ${isLinesFlagged ? '<span class="text-[10px] text-amber-700 bg-amber-100 px-1.5 py-0.2 rounded font-semibold">Flagged</span>' : ''}
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-[11px] text-left">
            <thead>
              <tr class="text-slate-400 border-b border-slate-200">
                <th class="pb-1 font-medium">Description</th>
                <th class="pb-1 text-center font-medium">Qty</th>
                <th class="pb-1 text-right font-medium">Price</th>
                <th class="pb-1 text-right font-medium">Total</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
              ${lines.map(l => `
                <tr>
                  <td class="py-1 text-slate-800 pr-2">${escapeHtml(l.description || '—')}</td>
                  <td class="py-1 text-slate-600 text-center font-mono">${displayVal(l.quantity)}</td>
                  <td class="py-1 text-slate-600 text-right font-mono">${l.unit_price !== undefined && l.unit_price !== null ? formatCurrency(l.unit_price, curr) : '—'}</td>
                  <td class="py-1 text-slate-900 text-right font-mono font-medium">${l.line_total !== undefined && l.line_total !== null ? formatCurrency(l.line_total, curr) : (l.total !== undefined && l.total !== null ? formatCurrency(l.total, curr) : '—')}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  container.innerHTML = `
    <div class="divide-y divide-slate-100 border border-slate-200 rounded-lg overflow-hidden bg-white">
      ${rows.map(r => {
    const isFlagged = flagged.has(r.key);
    const valClass = isFlagged
      ? 'bg-amber-100 text-amber-900 border border-amber-300 font-semibold px-1.5 py-0.5 rounded shadow-xs'
      : 'text-slate-800 font-mono';
    const labelClass = isFlagged ? 'text-amber-800 font-semibold' : 'text-slate-500 font-medium';
    return `
          <div class="px-3 py-1.5 flex items-center justify-between">
            <span class="${labelClass}">${r.label}</span>
            <span class="text-right truncate max-w-[200px] ${valClass}">
              ${escapeHtml(r.val)}
            </span>
          </div>
        `;
  }).join('')}
    </div>
    ${linesHtml}
  `;
}

function handleRunDone(decision) {
  renderDecisionCard(decision);
  let fields = decision?.extraction?.fields || decision?.extraction;
  if (!fields && decision?.extraction_json) {
    try {
      fields = typeof decision.extraction_json === 'string' ? JSON.parse(decision.extraction_json) : decision.extraction_json;
    } catch (e) {
      fields = {};
    }
  }
  fields = fields || {};
  renderExtractedFields(fields, decision);

  const cardContainer = document.getElementById('decision-card-container');
  if (cardContainer) {
    cardContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  updateReviewBadge();
}

function renderDecisionCard(decision) {
  const container = document.getElementById('decision-card-container');
  if (!container) return;

  const status = (decision.status || 'NEEDS_REVIEW').toUpperCase();
  const severity = (decision.severity || 'LOW').toUpperCase();
  let headline = decision.headline;
  if (status === 'RUNNING') {
    headline = 'Processing invoice';
  } else if (status === 'ERROR') {
    headline = 'Extraction unavailable — please retry.';
  } else if (!headline) {
    headline = 'Invoice Processed';
  }
  const reasons = decision.reasons || [];
  const nextAction = status === 'RUNNING' ? 'Processing in progress...' : (decision.next_action || 'Review findings');
  const gross = decision.gross !== undefined && decision.gross !== null ? formatCurrency(decision.gross, decision.currency) : '—';
  const net = decision.net !== undefined && decision.net !== null ? formatCurrency(decision.net, decision.currency) : '—';
  const payBy = decision.pay_by ? formatDate(decision.pay_by) : '—';
  const totalMs = decision.processing_time_ms ? `${decision.processing_time_ms} ms` : '—';

  // Stop spinners when in ERROR state
  if (status === 'ERROR') {
    for (let i = 1; i <= 12; i++) {
      const icon = document.getElementById(`stage-icon-${i}`);
      if (icon && icon.innerHTML.includes('animate-spin')) {
        icon.className = 'absolute -left-0.5 top-0.5 w-7 h-7 rounded-full bg-rose-100 border-2 border-rose-500 flex items-center justify-center text-rose-700 z-10';
        icon.innerHTML = `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>`;
      }
    }
  }

  // Specific handling for Call Script (E4 Bank Change)
  let scriptHtml = '';
  if (severity === 'CRITICAL') {
    scriptHtml = `
      <div class="mt-4 p-3.5 rounded-lg bg-purple-50 border border-purple-200 space-y-2">
        <div class="flex items-center space-x-2">
          <span class="px-2 py-0.5 rounded text-[10px] font-bold bg-purple-700 text-white uppercase tracking-wider">
            CRITICAL · Internal Call Script
          </span>
          <span class="text-xs text-purple-900 font-semibold">Vendor Impersonation / Bank Tampering Check</span>
        </div>
        <p class="text-xs text-purple-950 font-medium">
          Do NOT pay to the bank account printed on this invoice without independent phone verification:
        </p>
        <blockquote class="text-xs italic bg-white p-2.5 rounded border border-purple-200 text-purple-900 space-y-1">
          <p>“Hello, this is Accounts Payable at our company. We received an invoice requesting payment to a new bank account ending in <strong>${decision.extraction?.fields?.bank_account_last4 || '9027'}</strong>.</p>
          <p>Per company security policy, I am calling our verified contact number on file to confirm whether this banking change was legitimately authorized by your finance department.”</p>
        </blockquote>
        <button onclick="navigator.clipboard.writeText(this.previousElementSibling.innerText); showToast('Call script copied to clipboard', 'info');" class="inline-flex items-center text-xs text-purple-700 hover:text-purple-900 font-medium">
          Copy Call Script
        </button>
      </div>
    `;
  }

  // Specific handling for Email Draft (NEEDS_INFO, e.g. E3 missing info)
  let emailHtml = '';
  if (status === 'NEEDS_INFO' && decision.email_draft) {
    const draft = decision.email_draft;
    emailHtml = `
      <div class="mt-4 p-3.5 rounded-lg bg-slate-50 border border-slate-200 space-y-2">
        <div class="flex items-center justify-between">
          <span class="text-xs font-semibold text-slate-800">Draft Vendor Email</span>
          <button id="btn-copy-email" class="text-xs text-blue-600 hover:text-blue-800 font-medium">
            Copy Email Draft
          </button>
        </div>
        <div class="text-xs bg-white p-3 rounded border border-slate-200 space-y-1.5 font-sans">
          <p><span class="text-slate-400">To:</span> <span class="font-mono text-slate-700">${escapeHtml(draft.to || 'vendor-ap@example.com')}</span></p>
          <p><span class="text-slate-400">Subject:</span> <span class="font-semibold text-slate-800">${escapeHtml(draft.subject || 'Invoice Verification Needed')}</span></p>
          <div class="pt-2 border-t border-slate-100 text-slate-700 whitespace-pre-line leading-relaxed font-mono text-[11px]">
            ${escapeHtml(draft.body || '')}
          </div>
        </div>
      </div>
    `;
  }

  // Action buttons for review queue
  let actionButtonsHtml = '';
  if (status === 'NEEDS_REVIEW') {
    actionButtonsHtml = `
      <div class="flex items-center space-x-3 pt-3 border-t border-slate-100">
        <button id="btn-card-approve" class="flex-1 py-2 px-3 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-xs font-semibold shadow-xs transition-colors">
          Approve Invoice
        </button>
        <button id="btn-card-reject" class="flex-1 py-2 px-3 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-xs font-semibold shadow-xs transition-colors">
          Reject Invoice
        </button>
      </div>
    `;
  }

  // Error retry card
  if (status === 'ERROR') {
    actionButtonsHtml = `
      <div class="pt-3 border-t border-slate-100">
        <button id="btn-card-retry" class="w-full py-2 px-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-semibold shadow-xs transition-colors">
          Retry Run
        </button>
      </div>
    `;
  }

  const extSource = decision?.extraction?.source;
  const extModel = decision?.extraction?.model;
  let sourceBadgeHtml = '';
  if (extSource === 'PDF text extraction') {
    sourceBadgeHtml = `<span class="inline-flex items-center text-[10px] px-2 py-0.5 rounded-full font-medium bg-indigo-50 text-indigo-700 border border-indigo-200">PDF text extraction</span>`;
  } else if (extSource === 'cached') {
    sourceBadgeHtml = `<span class="inline-flex items-center text-[10px] px-2 py-0.5 rounded-full font-medium bg-amber-50 text-amber-700 border border-amber-200">${escapeHtml(extModel || 'fixture')} (cached)</span>`;
  } else if (extSource === 'live') {
    sourceBadgeHtml = `<span class="inline-flex items-center text-[10px] px-2 py-0.5 rounded-full font-medium bg-blue-50 text-blue-700 border border-blue-200">${escapeHtml(extModel || 'gemini')} (live)</span>`;
  } else if (extSource === 'failed') {
    sourceBadgeHtml = `<span class="inline-flex items-center text-[10px] px-2 py-0.5 rounded-full font-medium bg-rose-50 text-rose-700 border border-rose-200">Extraction failed</span>`;
  } else if (extSource === 'Source unavailable' || (!extSource && decision.seeded)) {
    sourceBadgeHtml = `<span class="inline-flex items-center text-[10px] px-2 py-0.5 rounded-full font-medium bg-slate-100 text-slate-600 border border-slate-300">Source unavailable</span>`;
  }

  container.innerHTML = `
    <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-md space-y-4">
      <div class="flex items-start justify-between">
        <div class="space-y-1">
          <div class="flex items-center space-x-2">
            ${getStatusBadgeHtml(status, severity)}
            ${sourceBadgeHtml}
            <span class="text-xs text-slate-400 font-mono">${decision.run_id}</span>
          </div>
          <h2 class="text-base font-bold text-slate-900 mt-1">${escapeHtml(headline)}</h2>
        </div>
        <button id="btn-download-json" title="Download Decision JSON" class="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition-colors">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
        </button>
      </div>

      <!-- Financial & Timing Summary Strip -->
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 py-3 px-3.5 bg-slate-50 rounded-lg text-xs border border-slate-100">
        <div>
          <span class="text-slate-500 block text-[10px] uppercase tracking-wider">Gross Total</span>
          <span class="font-bold text-slate-900 font-mono">${gross}</span>
        </div>
        <div>
          <span class="text-slate-500 block text-[10px] uppercase tracking-wider">Net Amount</span>
          <span class="font-medium text-slate-800 font-mono">${net}</span>
        </div>
        <div>
          <span class="text-slate-500 block text-[10px] uppercase tracking-wider">Pay By</span>
          <span class="font-medium text-slate-800">${payBy}</span>
        </div>
        <div>
          <span class="text-slate-500 block text-[10px] uppercase tracking-wider">Process Time</span>
          <span class="font-medium text-slate-800 font-mono">${totalMs}</span>
        </div>
      </div>

      <!-- Reasons List -->
      ${reasons.length > 0 ? `
        <div class="space-y-1">
          <span class="text-xs font-semibold text-slate-700">Audit Reasons:</span>
          <ul class="text-xs text-slate-600 space-y-1 list-disc list-inside">
            ${reasons.slice(0, 3).map(r => `<li>${escapeHtml(r)}</li>`).join('')}
          </ul>
        </div>
      ` : ''}

      <!-- Next Action Banner -->
      <div class="p-3 bg-blue-50/60 rounded-lg border border-blue-100 flex items-start space-x-2">
        <svg class="w-4 h-4 text-blue-600 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <div class="text-xs text-blue-900">
          <span class="font-semibold">Next Action:</span> ${escapeHtml(nextAction)}
        </div>
      </div>

      <!-- Contextual Call Script & Email Draft -->
      ${scriptHtml}
      ${emailHtml}

      <!-- Interactive Actions -->
      ${actionButtonsHtml}
    </div>
  `;

  container.classList.remove('hidden');

  // Wire buttons inside decision card
  const btnDownload = document.getElementById('btn-download-json');
  if (btnDownload) {
    btnDownload.addEventListener('click', () => downloadDecisionJson(decision));
  }

  const btnCopyEmail = document.getElementById('btn-copy-email');
  if (btnCopyEmail && decision.email_draft) {
    btnCopyEmail.addEventListener('click', () => {
      const text = `To: ${decision.email_draft.to}\nSubject: ${decision.email_draft.subject}\n\n${decision.email_draft.body}`;
      navigator.clipboard.writeText(text);
      showToast('Email draft copied to clipboard', 'success');
    });
  }

  const btnApprove = document.getElementById('btn-card-approve');
  if (btnApprove) {
    btnApprove.addEventListener('click', () => openReviewModal(decision.run_id, 'approve'));
  }

  const btnReject = document.getElementById('btn-card-reject');
  if (btnReject) {
    btnReject.addEventListener('click', () => openReviewModal(decision.run_id, 'reject'));
  }

  const btnRetry = document.getElementById('btn-card-retry');
  if (btnRetry) {
    btnRetry.addEventListener('click', () => {
      if (state.activeSampleId) {
        startSampleRun(state.activeSampleId);
      } else if (state.activeFile) {
        handleFileUpload(state.activeFile);
      }
    });
  }
}

function downloadDecisionJson(decision) {
  const jsonStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(decision, null, 2));
  const a = document.createElement('a');
  a.setAttribute('href', jsonStr);
  a.setAttribute('download', `${decision.run_id || 'invoice'}_decision.json`);
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ── Tab 2: Dashboard Analytics & Run History ────────────────────────────────

async function loadDashboard() {
  await Promise.all([loadKpis(), loadRunsTable()]);
}

async function loadKpis() {
  const container = document.getElementById('kpi-strip');
  if (!container) return;

  try {
    const res = await fetch('/api/kpis');
    if (!res.ok) throw new Error('Failed to fetch KPIs');
    const kpis = await res.json();
    state.kpis = kpis;

    const cards = [
      { label: 'Processed Invoices', val: kpis.invoices_processed, sub: 'Total runs audited' },
      { label: 'Touchless Rate', val: `${kpis.touchless_rate_pct}%`, sub: `${kpis.touchless_count} straight-through` },
      { label: 'Avg Processing Time (incl. AI extraction)', val: `${kpis.avg_processing_time_ms} ms`, sub: `vs ${kpis.manual_benchmark_minutes}m manual benchmark` },
      { label: 'AP Hours Saved', val: `${kpis.hours_saved} hrs`, sub: `At ${kpis.manual_benchmark_minutes}m benchmark` },
      { label: 'Amount at Risk', val: formatCurrency(kpis.amount_at_risk_caught), sub: 'Caught by policy / holds' },
      {
        label: 'Top Rule Triggered',
        val: kpis.top_rules_fired?.[0]?.id || 'None',
        sub: kpis.top_rules_fired?.[0] ? `${kpis.top_rules_fired[0].count} flag(s)` : 'No violations',
      },
    ];

    container.innerHTML = cards.map(c => `
      <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-xs">
        <span class="text-[11px] font-medium text-slate-500 uppercase tracking-wider block">${c.label}</span>
        <div class="text-xl font-bold text-slate-900 mt-1 truncate">${c.val}</div>
        <span class="text-[10px] text-slate-400 mt-0.5 block truncate">${c.sub}</span>
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = `<p class="col-span-full text-xs text-rose-500">Error loading KPIs: ${err.message}</p>`;
  }
}

async function loadRunsTable() {
  const tbody = document.getElementById('runs-table-body');
  const barContainer = document.getElementById('status-bar-container');
  const legendContainer = document.getElementById('status-legend');
  if (!tbody) return;

  try {
    const res = await fetch('/api/runs');
    if (!res.ok) throw new Error('Failed to fetch runs');
    const data = await res.json();
    state.runs = data.runs || [];

    renderStatusBar(state.runs, barContainer, legendContainer);
    renderFilteredRuns();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="9" class="p-4 text-center text-xs text-rose-500">Error loading runs: ${err.message}</td></tr>`;
  }
}

function renderStatusBar(runs, barContainer, legendContainer) {
  if (!barContainer || !legendContainer) return;

  const total = runs.length || 1;
  const counts = {
    APPROVED: 0,
    NEEDS_REVIEW: 0,
    NEEDS_INFO: 0,
    REJECTED: 0,
    ERROR: 0,
  };

  runs.forEach(r => {
    const s = (r.status || 'NEEDS_REVIEW').toUpperCase();
    if (counts[s] !== undefined) counts[s]++;
  });

  const segments = [
    { key: 'APPROVED', label: 'Approved', color: 'bg-emerald-500', count: counts.APPROVED },
    { key: 'NEEDS_REVIEW', label: 'Needs Review', color: 'bg-amber-500', count: counts.NEEDS_REVIEW },
    { key: 'NEEDS_INFO', label: 'Waiting on Vendor', color: 'bg-slate-400', count: counts.NEEDS_INFO },
    { key: 'REJECTED', label: 'Rejected', color: 'bg-rose-500', count: counts.REJECTED },
    { key: 'ERROR', label: 'Error', color: 'bg-red-700', count: counts.ERROR },
  ];

  barContainer.innerHTML = segments.map(seg => {
    const pct = ((seg.count / total) * 100).toFixed(1);
    if (seg.count === 0) return '';
    return `<div style="width: ${pct}%" class="${seg.color} h-full transition-all" title="${seg.label}: ${seg.count} (${pct}%)"></div>`;
  }).join('');

  legendContainer.innerHTML = segments.map(seg => {
    const pct = ((seg.count / total) * 100).toFixed(0);
    return `
      <div class="flex items-center space-x-1.5">
        <span class="w-2.5 h-2.5 rounded-full ${seg.color}"></span>
        <span>${seg.label}: <strong>${seg.count}</strong> (${pct}%)</span>
      </div>
    `;
  }).join('');
}

function renderFilteredRuns() {
  const tbody = document.getElementById('runs-table-body');
  if (!tbody) return;

  const statusFilter = document.getElementById('filter-status')?.value;
  const sourceFilter = document.getElementById('filter-source')?.value;
  const searchFilter = (document.getElementById('filter-search')?.value || '').toLowerCase().trim();

  const filtered = state.runs.filter(r => {
    if (statusFilter && r.status !== statusFilter) return false;
    if (sourceFilter === 'live' && r.seeded) return false;
    if (sourceFilter === 'seeded' && !r.seeded) return false;
    if (searchFilter) {
      const matchInv = (r.invoice_number || '').toLowerCase().includes(searchFilter);
      const matchVendor = (r.vendor_name || r.vendor_id || '').toLowerCase().includes(searchFilter);
      const matchRunId = (r.run_id || '').toLowerCase().includes(searchFilter);
      if (!matchInv && !matchVendor && !matchRunId) return false;
    }
    return true;
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="p-8 text-center text-xs text-slate-400">No matching invoice runs found.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(r => {
    const originLabel = r.seeded ? 'Demo history' : 'Live run';
    const originClass = r.seeded ? 'text-slate-400' : 'text-blue-600 font-semibold';

    return `
      <tr class="hover:bg-slate-50 transition-colors" data-run-id="${r.run_id}">
        <td class="py-3 px-4 font-mono font-medium text-slate-900">${r.run_id}</td>
        <td class="py-3 px-4 text-slate-500 whitespace-nowrap">${formatDate(r.created_at)}</td>
        <td class="py-3 px-4 font-medium text-slate-800">${escapeHtml(r.vendor_name || r.vendor_id || 'Unknown')}</td>
        <td class="py-3 px-4 font-mono text-slate-700">${escapeHtml(r.invoice_number || '—')}</td>
        <td class="py-3 px-4 font-mono text-slate-500">${escapeHtml(r.po_number || '—')}</td>
        <td class="py-3 px-4 font-mono font-medium text-slate-800">${formatCurrency(r.gross, r.currency)}</td>
        <td class="py-3 px-4">${getStatusBadgeHtml(r.status, r.severity)}</td>
        <td class="py-3 px-4"><span class="text-[10px] font-semibold text-slate-500 uppercase">${r.severity || 'LOW'}</span></td>
        <td class="py-3 px-4"><span class="text-[11px] ${originClass}">${originLabel}</span></td>
      </tr>
    `;
  }).join('');

  // Click row to view run in Run View
  tbody.querySelectorAll('tr').forEach(row => {
    row.addEventListener('click', () => {
      const runId = row.getAttribute('data-run-id');
      inspectRunInRunView(runId);
    });
  });
}

// Wire filter events
document.getElementById('filter-status')?.addEventListener('change', renderFilteredRuns);
document.getElementById('filter-source')?.addEventListener('change', renderFilteredRuns);
document.getElementById('filter-search')?.addEventListener('input', renderFilteredRuns);

async function inspectRunInRunView(runId) {
  switchTab('run');
  resetRunView();
  document.getElementById('active-run-id-label').textContent = `${runId} (inspecting)`;

  // Fetch document preview
  const previewContainer = document.getElementById('document-preview-container');
  const previewOpenLink = document.getElementById('preview-open-link');
  const fileUrl = `/api/runs/${runId}/file`;
  previewContainer.innerHTML = `<iframe src="${fileUrl}" class="w-full h-full border-0" title="Invoice Preview"></iframe>`;
  previewOpenLink.href = fileUrl;
  previewOpenLink.classList.remove('hidden');

  // Stream/replay historical events
  connectEventSource(runId);
}

// ── Tab 3: Review Queue ─────────────────────────────────────────────────────

async function loadReviewQueue() {
  const container = document.getElementById('review-queue-container');
  const countBadge = document.getElementById('review-count-badge');
  if (!container) return;

  try {
    const res = await fetch('/api/runs');
    if (!res.ok) throw new Error('Failed to load review items');
    const data = await res.json();
    const runs = data.runs || [];

    // Filter for NEEDS_REVIEW or NEEDS_INFO
    const held = runs.filter(r => r.status === 'NEEDS_REVIEW' || r.status === 'NEEDS_INFO');

    // Sort by severity (CRITICAL > HIGH > MEDIUM > LOW) then by due date
    const severityOrder = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };
    held.sort((a, b) => {
      const sA = severityOrder[(a.severity || 'LOW').toUpperCase()] ?? 4;
      const sB = severityOrder[(b.severity || 'LOW').toUpperCase()] ?? 4;
      if (sA !== sB) return sA - sB;
      return new Date(a.pay_by || a.created_at) - new Date(b.pay_by || b.created_at);
    });

    if (countBadge) {
      countBadge.textContent = `${held.length} item(s) pending`;
    }

    if (held.length === 0) {
      container.innerHTML = `
        <div class="text-center py-12 text-slate-400">
          <svg class="w-10 h-10 mx-auto mb-2 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <p class="text-xs font-medium">Review queue is empty.</p>
          <p class="text-[11px] text-slate-400 mt-0.5">All received invoices have been processed or resolved.</p>
        </div>
      `;
      return;
    }

    container.innerHTML = held.map(r => {
      const dec = r.decision || {};
      const reasons = dec.reasons || [r.headline || 'Action required'];
      const isCritical = (r.severity || '').toUpperCase() === 'CRITICAL';

      return `
        <div class="bg-white rounded-xl border ${isCritical ? 'border-purple-300 shadow-md ring-1 ring-purple-200' : 'border-slate-200 shadow-xs'} p-5 space-y-3">
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div class="flex items-center space-x-2">
              ${getStatusBadgeHtml(r.status, r.severity)}
              <span class="text-xs font-mono font-semibold text-slate-800">${r.run_id}</span>
              <span class="text-xs text-slate-400">·</span>
              <span class="text-xs font-medium text-slate-600">${escapeHtml(r.vendor_name || r.vendor_id || 'Vendor')}</span>
            </div>
            <div class="text-xs text-slate-500 font-mono">
              Due: <span class="font-medium text-slate-700">${r.pay_by ? formatDate(r.pay_by) : 'Immediate'}</span>
            </div>
          </div>

          <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 bg-slate-50/70 p-3 rounded-lg text-xs">
            <div>
              <span class="text-slate-400 text-[10px] uppercase">Invoice #</span>
              <p class="font-mono font-medium text-slate-800">${escapeHtml(r.invoice_number || '—')}</p>
            </div>
            <div>
              <span class="text-slate-400 text-[10px] uppercase">PO Reference</span>
              <p class="font-mono font-medium text-slate-800">${escapeHtml(r.po_number || 'None')}</p>
            </div>
            <div>
              <span class="text-slate-400 text-[10px] uppercase">Gross Total</span>
              <p class="font-mono font-bold text-slate-900">${formatCurrency(r.gross, r.currency)}</p>
            </div>
            <div>
              <span class="text-slate-400 text-[10px] uppercase">Next Action</span>
              <p class="font-medium text-blue-700 truncate">${escapeHtml(r.next_action || 'Review')}</p>
            </div>
          </div>

          <!-- Reasons -->
          <ul class="text-xs text-slate-600 list-disc list-inside space-y-0.5">
            ${reasons.slice(0, 3).map(msg => `<li>${escapeHtml(msg)}</li>`).join('')}
          </ul>

          <!-- Action bar -->
          <div class="pt-3 border-t border-slate-100 flex items-center justify-between">
            <button onclick="inspectRunInRunView('${r.run_id}')" class="text-xs text-blue-600 hover:text-blue-800 font-medium flex items-center space-x-1">
              <span>View Audit Trace</span>
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" /></svg>
            </button>

            <div class="flex items-center space-x-2">
              <button onclick="openReviewModal('${r.run_id}', 'reject')" class="px-3 py-1.5 border border-rose-300 text-rose-700 hover:bg-rose-50 text-xs font-semibold rounded-md shadow-xs transition-colors">
                Reject
              </button>
              <button onclick="openReviewModal('${r.run_id}', 'approve')" class="px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-md shadow-xs transition-colors">
                Approve
              </button>
            </div>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    container.innerHTML = `<p class="text-xs text-rose-500 py-4">Error loading review queue: ${err.message}</p>`;
  }
}

async function updateReviewBadge() {
  const badge = document.getElementById('review-badge');
  if (!badge) return;

  try {
    const res = await fetch('/api/runs');
    if (!res.ok) return;
    const data = await res.json();
    const held = (data.runs || []).filter(r => r.status === 'NEEDS_REVIEW' || r.status === 'NEEDS_INFO');
    if (held.length > 0) {
      badge.textContent = held.length;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
  } catch { }
}

// ── Review Action Modal ─────────────────────────────────────────────────────

function initReviewModal() {
  const cancelBtn = document.getElementById('modal-cancel-btn');
  const confirmBtn = document.getElementById('modal-confirm-btn');

  if (cancelBtn) cancelBtn.addEventListener('click', closeReviewModal);
  if (confirmBtn) confirmBtn.addEventListener('click', submitReviewAction);
}

function openReviewModal(runId, action) {
  state.reviewModal = { isOpen: true, runId, action };
  const modal = document.getElementById('review-modal');
  const title = document.getElementById('modal-title');
  const desc = document.getElementById('modal-desc');
  const note = document.getElementById('review-note-input');
  const confirmBtn = document.getElementById('modal-confirm-btn');

  if (!modal) return;

  if (action === 'approve') {
    title.textContent = `Approve Invoice (${runId})`;
    desc.textContent = 'Approve payment and record in the accounting ledger. Provide an audit reason:';
    confirmBtn.className = 'px-3.5 py-1.5 bg-emerald-600 text-xs font-medium rounded-md text-white hover:bg-emerald-700 shadow-xs';
    confirmBtn.textContent = 'Confirm Approval';
  } else {
    title.textContent = `Reject Invoice (${runId})`;
    desc.textContent = 'Reject invoice. This will notify the vendor and halt payment processing:';
    confirmBtn.className = 'px-3.5 py-1.5 bg-rose-600 text-xs font-medium rounded-md text-white hover:bg-rose-700 shadow-xs';
    confirmBtn.textContent = 'Confirm Rejection';
  }

  if (note) note.value = '';
  modal.classList.remove('hidden');
}

function closeReviewModal() {
  state.reviewModal = { isOpen: false, runId: null, action: null };
  const modal = document.getElementById('review-modal');
  if (modal) modal.classList.add('hidden');
}

async function submitReviewAction() {
  const { runId, action } = state.reviewModal;
  const noteInput = document.getElementById('review-note-input');
  const note = noteInput ? noteInput.value.trim() : '';

  try {
    const res = await fetch(`/api/runs/${runId}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, note, reviewer: 'demo_user' }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Review submission failed');
    }

    showToast(`Invoice ${runId} marked as ${action.toUpperCase()}`, 'success');
    closeReviewModal();

    if (state.activeTab === 'review') {
      loadReviewQueue();
    } else if (state.activeTab === 'dashboard') {
      loadDashboard();
    } else if (state.activeTab === 'run') {
      // Refresh active run decision card
      const detailRes = await fetch(`/api/runs/${runId}`);
      if (detailRes.ok) {
        const updated = await detailRes.json();
        renderDecisionCard(updated);
      }
    }
    updateReviewBadge();
  } catch (err) {
    showToast(`Review error: ${err.message}`, 'error');
  }
}

// ── Tab 4: Rules & Assumptions ──────────────────────────────────────────────

async function loadRulesAndAssumptions() {
  await Promise.all([loadRulesConfig(), loadProcessMap(), loadAssumptions()]);
}

async function loadRulesConfig() {
  const container = document.getElementById('config-rules-table');
  if (!container) return;

  try {
    const res = await fetch('/api/config');
    if (!res.ok) throw new Error('Failed to load rules');
    const data = await res.json();
    const rules = data.rules || {};

    const rows = [
      { param: 'Amount Tolerance Pct', val: `${(rules.amount_tolerance_pct * 100).toFixed(0)}%`, desc: 'Max percentage gross variance permitted against expected PO' },
      { param: 'Amount Tolerance Cap', val: formatCurrency(rules.amount_tolerance_cap_usd), desc: 'Hard dollar cap on permitted gross variance' },
      { param: 'Auto-Approve Limit', val: formatCurrency(rules.auto_approve_limit_usd), desc: 'Invoices exceeding this gross require manual manager review' },
      { param: 'Fuzzy Vendor Name Cutoff', val: `${rules.fuzzy_vendor_name_score_cutoff}%`, desc: 'Rapidfuzz token sort ratio required to match master vendor alias' },
      { param: 'Fuzzy Line Item Cutoff', val: `${rules.fuzzy_line_desc_score_cutoff}%`, desc: 'Rapidfuzz token set ratio required to match PO line item description' },
      { param: 'Duplicate Amount Tolerance', val: formatCurrency(rules.dup_amount_tolerance_usd), desc: 'Dollar window for fuzzy duplicate detection' },
      { param: 'Duplicate Date Window', val: `${rules.dup_date_window_days} days`, desc: 'Calendar window for fuzzy duplicate detection' },
      { param: 'Invoice Max Age', val: `${rules.invoice_max_age_days} days`, desc: 'Invoices dated older than 180 days raise an audit warning' },
      { param: 'Manual Handling Benchmark', val: `${rules.manual_minutes_per_invoice} min`, desc: 'Industry baseline manual processing time per invoice' },
      { param: 'As-Of Anchor Date', val: data.as_of_date || '2026-11-20', desc: 'Deterministic system reference date for audit consistency' },
      { param: 'Active Gemini Models', val: (data.models || []).join(' → '), desc: 'Ordered LLM fallback chain executed in Stage 2' },
    ];

    container.innerHTML = `
      <table class="w-full text-left text-xs text-slate-700">
        <thead class="bg-slate-50 border-b border-slate-200 text-slate-500 font-semibold uppercase text-[10px]">
          <tr>
            <th class="py-2.5 px-3">Rule / Threshold Parameter</th>
            <th class="py-2.5 px-3">Configured Value</th>
            <th class="py-2.5 px-3">Operational Enforcement Policy</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100 font-mono text-xs">
          ${rows.map(r => `
            <tr>
              <td class="py-2 px-3 font-semibold text-slate-800">${r.param}</td>
              <td class="py-2 px-3 text-blue-700 font-bold">${r.val}</td>
              <td class="py-2 px-3 font-sans text-slate-500 text-[11px]">${r.desc}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  } catch (err) {
    container.innerHTML = `<p class="text-xs text-rose-500">Error loading rules config: ${err.message}</p>`;
  }
}

function loadProcessMap() {
  const container = document.getElementById('process-map-container');
  if (!container) return;

  container.innerHTML = PIPELINE_STAGES.map(s => `
    <div class="p-3 rounded-lg border border-slate-200 bg-slate-50/50 space-y-1">
      <div class="flex items-center space-x-2">
        <span class="w-5 h-5 rounded-full bg-blue-100 text-blue-700 font-bold text-[10px] flex items-center justify-center">
          ${s.id}
        </span>
        <h4 class="text-xs font-semibold text-slate-800">${s.name}</h4>
      </div>
      <p class="text-[11px] text-slate-500 leading-snug">${s.desc}</p>
    </div>
  `).join('');
}

function loadAssumptions() {
  const container = document.getElementById('assumptions-container');
  if (!container) return;

  const assumptions = [
    { id: 'A1', title: 'Single Currency (USD)', desc: 'All transactions and accounting entries operate in USD; non-USD currencies are held for specialist review.' },
    { id: 'A2', title: '8-Minute Manual Benchmark', desc: 'Baseline AP specialist time spent per invoice is 8 minutes, used for computing productivity savings.' },
    { id: 'A3', title: 'Amount Variance Tolerance', desc: 'Gross variance tolerance is min(2% of expected gross, $100); amounts within tolerance approve cleanly.' },
    { id: 'A4', title: '$75,000 Auto-Approve Cap', desc: 'Invoices exceeding $75,000 gross require human AP manager sign-off regardless of 3-way match.' },
    { id: 'A5', title: 'Zero-Tolerance Banking Change', desc: 'Any remit bank account mismatch blocks payment, flagged CRITICAL with mandatory phone verification.' },
    { id: 'A6', title: 'Implied PO Match Advisory', desc: 'When no PO is printed on the invoice, a matching single open PO is suggested as review advisory only.' },
    { id: 'A7', title: 'Approved-Only Ledger Posting', desc: 'Only APPROVED invoices consume PO balance, deplete GRN goods-received quantities, and write to ledger.' },
    { id: 'A8', title: 'Goods vs Service PO Receipts', desc: 'Goods POs strictly require warehouse Goods Received Notes (GRN); service POs do not.' },
    { id: 'A9', title: 'Spelled-Out Date Parsing', desc: 'Dates are read with explicit month spellings (e.g. 12 Oct 2026); ambiguous dd/mm formats require review.' },
    { id: 'A10', title: 'Synthetic Demonstration Data', desc: 'All master records and sample documents are synthetic; vendor correspondence drafts are never auto-sent.' },
    { id: 'A11', title: '180-Day Invoice Age Warning', desc: 'Invoices dated older than 180 days prior to the system anchor date trigger an audit warning.' },
    { id: 'A12', title: 'Normalized Number Duplication', desc: 'Duplicates are detected across alphanumeric normalized keys (CR-000457 = CR 457 = cr457), not raw file hashes.' },
  ];

  container.innerHTML = assumptions.map(a => `
    <div class="p-3 rounded-lg border border-slate-200 bg-white space-y-1">
      <div class="flex items-center space-x-2">
        <span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-slate-100 text-slate-700">${a.id}</span>
        <h4 class="text-xs font-semibold text-slate-800">${a.title}</h4>
      </div>
      <p class="text-[11px] text-slate-500 leading-relaxed">${a.desc}</p>
    </div>
  `).join('');
}

// ── Tab 5: Master Data ──────────────────────────────────────────────────────

async function loadMasterData() {
  const vendorsContainer = document.getElementById('master-vendors-container');
  const posContainer = document.getElementById('master-pos-container');
  if (!vendorsContainer || !posContainer) return;

  try {
    const res = await fetch('/api/master');
    if (!res.ok) throw new Error('Failed to load master records');
    const { vendors, purchase_orders } = await res.json();

    // Render Vendors Table
    vendorsContainer.innerHTML = `
      <table class="w-full text-left text-xs text-slate-700">
        <thead class="bg-slate-50 border-b border-slate-200 text-slate-500 font-semibold uppercase text-[10px]">
          <tr>
            <th class="py-2.5 px-3">Vendor ID</th>
            <th class="py-2.5 px-3">Legal Name</th>
            <th class="py-2.5 px-3">Status</th>
            <th class="py-2.5 px-3">Tax ID</th>
            <th class="py-2.5 px-3">Bank Last 4</th>
            <th class="py-2.5 px-3">Terms</th>
            <th class="py-2.5 px-3">Callback Phone & Domain</th>
            <th class="py-2.5 px-3">Authorized Aliases</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100 text-xs">
          ${vendors.map(v => `
            <tr>
              <td class="py-2.5 px-3 font-mono font-bold text-slate-900">${v.id}</td>
              <td class="py-2.5 px-3 font-semibold text-slate-800">${escapeHtml(v.name)}</td>
              <td class="py-2.5 px-3">
                <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${v.status === 'active' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-rose-50 text-rose-700 border border-rose-200'}">
                  ${v.status.toUpperCase()}
                </span>
              </td>
              <td class="py-2.5 px-3 font-mono text-slate-600">${escapeHtml(v.tax_id || '—')}</td>
              <td class="py-2.5 px-3 font-mono text-slate-600">•••• ${v.bank_account_last4 || '—'}</td>
              <td class="py-2.5 px-3 text-slate-600">${escapeHtml(v.payment_terms || '—')}</td>
              <td class="py-2.5 px-3 text-[11px] text-slate-500">
                ${v.phone ? `<span>${escapeHtml(v.phone)}</span><br>` : ''}
                ${v.remit_email_domain ? `<span class="text-blue-600">${escapeHtml(v.remit_email_domain)}</span>` : ''}
              </td>
              <td class="py-2.5 px-3 text-[11px] text-slate-500">
                ${(v.aliases || []).map(a => `<span class="inline-block bg-slate-100 px-1.5 py-0.5 rounded mr-1 mb-0.5">${escapeHtml(a)}</span>`).join('')}
              </td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;

    // Render Purchase Orders Table
    posContainer.innerHTML = `
      <div class="space-y-4">
        ${purchase_orders.map(po => {
      const remNet = po.remaining_net !== undefined ? po.remaining_net : po.total_net;
      const pctConsumed = po.total_net > 0 ? Math.min(100, (((po.total_net - remNet) / po.total_net) * 100)).toFixed(0) : 0;

      return `
            <div class="border border-slate-200 rounded-lg p-4 bg-white space-y-3">
              <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-100 pb-2.5">
                <div class="flex items-center space-x-2.5">
                  <span class="font-mono font-bold text-sm text-slate-900">${po.po_number}</span>
                  <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-blue-50 text-blue-700 border border-blue-200">
                    Vendor: ${po.vendor_id}
                  </span>
                  ${po.grn_needed ? '<span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-50 text-amber-700 border border-amber-200">GRN Required</span>' : '<span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-slate-100 text-slate-600">Service PO</span>'}
                </div>
                <div class="flex items-center space-x-4 text-xs font-mono">
                  <span>Gross: <strong>${formatCurrency(po.total_gross)}</strong></span>
                  <span>Open Net: <strong class="text-emerald-700">${formatCurrency(remNet)}</strong></span>
                </div>
              </div>

              <!-- Consumption Progress Bar -->
              <div class="space-y-1">
                <div class="flex justify-between text-[11px] text-slate-500">
                  <span>Balance Consumed</span>
                  <span>${pctConsumed}% (${formatCurrency(po.total_net - remNet)} of ${formatCurrency(po.total_net)} net)</span>
                </div>
                <div class="h-2 w-full bg-slate-100 rounded-full overflow-hidden">
                  <div style="width: ${pctConsumed}%" class="h-full bg-blue-600 rounded-full transition-all"></div>
                </div>
              </div>

              <!-- Lines Table -->
              <div class="overflow-x-auto">
                <table class="w-full text-left text-[11px] text-slate-600">
                  <thead class="bg-slate-50 text-slate-400 uppercase text-[9px]">
                    <tr>
                      <th class="py-1.5 px-2">Line #</th>
                      <th class="py-1.5 px-2">SKU</th>
                      <th class="py-1.5 px-2">Description</th>
                      <th class="py-1.5 px-2">Quantity</th>
                      <th class="py-1.5 px-2">Unit Price</th>
                      <th class="py-1.5 px-2">Line Net</th>
                      <th class="py-1.5 px-2">GRN Received</th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-slate-100 font-mono">
                    ${(po.lines || []).map(l => `
                      <tr>
                        <td class="py-1.5 px-2 text-slate-400">${l.line_number}</td>
                        <td class="py-1.5 px-2 font-semibold text-slate-800">${escapeHtml(l.sku || '—')}</td>
                        <td class="py-1.5 px-2 font-sans text-slate-700">${escapeHtml(l.description)}</td>
                        <td class="py-1.5 px-2">${l.quantity}</td>
                        <td class="py-1.5 px-2">${formatCurrency(l.unit_price)}</td>
                        <td class="py-1.5 px-2 font-semibold text-slate-900">${formatCurrency(l.line_total)}</td>
                        <td class="py-1.5 px-2 text-emerald-700 font-bold">${l.quantity_received !== null && l.quantity_received !== undefined ? l.quantity_received : 'N/A'}</td>
                      </tr>
                    `).join('')}
                  </tbody>
                </table>
              </div>
            </div>
          `;
    }).join('')}
      </div>
    `;
  } catch (err) {
    vendorsContainer.innerHTML = `<p class="text-xs text-rose-500">Error: ${err.message}</p>`;
  }
}

// ── HTML Escape Helper ──────────────────────────────────────────────────────

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ── Application Initialization ──────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  initNavigation();
  initRunView();
  initReviewModal();
  updateReviewBadge();
});
