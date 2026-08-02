const state = {
  view: 'overview',
  interfaces: [],
  companies: {items: [], page: 1, page_size: 50, total: 0, facets: {}},
  companyFilters: {q: '', country: '', exchange: '', status: ''},
  preview: null,
  exportFormat: 'xlsx',
  jobs: [],
  audit: [],
  overviewTimer: null,
  jobTimer: null
};

const AdminAPI = {
  token: () => sessionStorage.getItem('aicmAdminToken') || '',
  async json(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set('X-AICM-Admin-Token', this.token());
    if (options.body && !(options.body instanceof Blob) && typeof options.body !== 'string') {
      headers.set('Content-Type', 'application/json');
      options = {...options, body: JSON.stringify(options.body)};
    }
    const response = await fetch(path, {...options, headers});
    const payload = await response.json().catch(() => ({
      ok: false,
      error: {code: 'invalid_response', message: '服务器返回了无效响应'}
    }));
    if (!response.ok || !payload.ok) {
      const error = new Error(payload.error?.message || `HTTP ${response.status}`);
      error.code = payload.error?.code || 'request_failed';
      error.status = response.status;
      error.details = payload.error?.details || [];
      throw error;
    }
    return payload.data;
  },
  async download(path, filename) {
    const response = await fetch(path, {
      headers: {'X-AICM-Admin-Token': this.token()}
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error?.message || `HTTP ${response.status}`);
    }
    const blob = await response.blob();
    downloadBlob(blob, filename);
  }
};

const $ = selector => document.querySelector(selector);
const $$ = selector => Array.from(document.querySelectorAll(selector));

function navigateWithFlip(target, direction = 'to-admin') {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    location.assign(target);
    return;
  }
  sessionStorage.setItem('aicmFlipDirection', direction);
  document.documentElement.classList.add(direction === 'to-dashboard' ? 'page-flip-out-right' : 'page-flip-out-left');
  setTimeout(() => location.assign(target), 225);
}

function prepareFlipIn() {
  const direction = sessionStorage.getItem('aicmFlipDirection');
  if (!direction) return;
  sessionStorage.removeItem('aicmFlipDirection');
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const name = direction === 'to-dashboard' ? 'page-flip-in-left' : 'page-flip-in-right';
  document.documentElement.classList.add(name);
  setTimeout(() => document.documentElement.classList.remove(name), 500);
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons({attrs: {'stroke-width': 1.7}});
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function displayTime(value) {
  if (!value) return '—';
  const text = String(value);
  if (/^\d{10,}$/.test(text)) {
    return new Date(Number(text) * 1000).toLocaleString('zh-CN', {hour12: false});
  }
  return text.replace('T', ' ').replace('+00:00', ' UTC');
}

function formatNumber(value, digits = 0) {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toLocaleString('zh-CN', {maximumFractionDigits: digits});
}

function formatDuration(seconds) {
  const number = Number(seconds);
  if (!Number.isFinite(number) || number <= 0) return '—';
  if (number < 60) return `${number.toFixed(1)} 秒`;
  return `${Math.floor(number / 60)}分 ${Math.round(number % 60)}秒`;
}

function statusText(status) {
  return ({
    healthy: '正常', warning: '需关注', stale: '已过期', failed: '失败', error: '失败',
    running: '运行中', queued: '排队中', succeeded: '成功', skipped: '已跳过',
    interrupted: '已中断', success: '成功', accepted: '已受理', active: '有效',
    new: '新增', update: '更新', unchanged: '不变', would_disable: '将停用',
    invalid: '无效', duplicate: '重复', conflict: '冲突', removed: '已停用', missing_quote: '无报价',
    stale_quote: '报价过期', metadata_issue: '元数据异常', fresh: '新鲜', missing: '无报价'
  })[status] || status || '未知';
}

function stateLabel(status) {
  const safe = escapeHtml(status || 'neutral');
  return `<span class="state-label ${safe}"><i class="status-dot ${safe}"></i>${escapeHtml(statusText(status))}</span>`;
}

function toast(message, type = '') {
  const node = document.createElement('div');
  node.className = `toast ${type}`.trim();
  node.textContent = message;
  $('#toast-region').appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function withButton(button, task) {
  if (!button || button.disabled) return;
  button.disabled = true;
  const original = button.innerHTML;
  button.innerHTML = '<i data-lucide="loader-circle"></i><span>处理中</span>';
  refreshIcons();
  try {
    return await task();
  } finally {
    button.disabled = false;
    button.innerHTML = original;
    refreshIcons();
  }
}

function showAuth(message = '') {
  $('#admin-auth').hidden = false;
  $('#admin-shell').hidden = true;
  $('#auth-error').textContent = message;
  $('#auth-token').value = sessionStorage.getItem('aicmAdminToken') || '';
  refreshIcons();
}

async function authenticate(token) {
  sessionStorage.setItem('aicmAdminToken', token);
  try {
    await AdminAPI.json('/api/admin/auth/verify', {method: 'POST', body: {}});
    $('#admin-auth').hidden = true;
    $('#admin-shell').hidden = false;
    startConsole();
  } catch (error) {
    sessionStorage.removeItem('aicmAdminToken');
    showAuth(error.message || 'Token 验证失败');
    throw error;
  }
}

function setView(name) {
  state.view = name;
  $$('.view').forEach(view => view.classList.toggle('active', view.dataset.viewPanel === name));
  $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === name));
  $('#admin-nav').classList.remove('open');
  if (name === 'overview') loadOverview();
  if (name === 'interfaces') loadInterfaces();
  if (name === 'companies') loadCompanies();
  if (name === 'jobs') loadJobs();
  if (name === 'users') loadUsers();
  if (name === 'audit') loadAudit();
  refreshIcons();
}

async function loadOverview() {
  try {
    const [overview, interfaces] = await Promise.all([
      AdminAPI.json('/api/admin/overview'),
      AdminAPI.json('/api/admin/interfaces')
    ]);
    state.interfaces = interfaces.items || [];
    renderOverview(overview);
    $('#last-updated').textContent = `更新于 ${displayTime(overview.generated_at)}`;
    $('#nav-company-count').textContent = overview.catalog?.active || '';
    $('#nav-interface-count').textContent = overview.attention?.count || '';
    $('#nav-job-count').textContent = (overview.active_jobs || []).length || '';
    const serviceState = overview.service?.status || 'neutral';
    $('#global-health').innerHTML = `<i class="status-dot ${escapeHtml(serviceState)}"></i>${serviceState === 'healthy' ? '服务在线' : '服务需关注'}`;
  } catch (error) {
    handleApiError(error, '总览读取失败');
  }
}

function renderOverview(data) {
  const metrics = [
    ['服务状态', statusText(data.service?.status), data.service?.status || 'neutral', '8911 在线'],
    ['有效公司', formatNumber(data.catalog?.active), '', '当前有效池'],
    ['新鲜报价', formatNumber(data.quotes?.fresh), data.quotes?.status || '', `${formatNumber(data.quotes?.coverage_pct, 1)}% 覆盖`],
    ['待关注事项', formatNumber(data.attention?.count), data.attention?.count ? 'warning' : 'healthy', data.history?.status === 'failed' ? '历史任务失败' : '持续监测']
  ];
  $('#overview-metrics').innerHTML = metrics.map(([label, value, status, note]) => `
    <div class="metric"><span>${escapeHtml(label)}</span><strong class="${escapeHtml(status)}">${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></div>
  `).join('');

  const severityOrder = {failed: 0, error: 0, stale: 1, warning: 1, running: 2, healthy: 3};
  const rows = [...state.interfaces].sort((a, b) => (severityOrder[a.status] ?? 4) - (severityOrder[b.status] ?? 4)).slice(0, 5);
  $('#overview-interface-rows').innerHTML = rows.map(interfaceRow).join('') || emptyRow(5, '暂无接口状态');

  const attention = data.attention?.items || [];
  $('#attention-total').textContent = `${formatNumber(data.attention?.count)} 项`;
  $('#attention-list').innerHTML = attention.length ? attention.map(item => `
    <article class="attention-item"><header><strong class="${item.severity === 'error' ? 'danger-text' : ''}">${escapeHtml(item.message)}</strong><span>${formatNumber(item.count)} 项</span></header><p>${item.id === 'missing_quotes' ? '系统会继续保留有效报价，并在后续轮次自动重试缺失公司。' : '现有历史文件未被清空，可在任务记录中查看并手动重试。'}</p></article>
  `).join('') : '<div class="empty-state">当前没有需要人工处理的事项</div>';
  bindInterfaceActions($('#overview-interface-rows'));
  refreshIcons();
}

function interfaceRow(item) {
  const latency = item.latency_ms ? `${formatNumber(item.latency_ms, 0)} ms` : '—';
  return `<tr>
    <td><div class="table-service"><i class="status-dot ${escapeHtml(item.status)}"></i><div><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.provider)}</small></div></div></td>
    <td>${stateLabel(item.status)}</td>
    <td>${escapeHtml(item.coverage || '—')}<small class="table-sub">${escapeHtml(latency)}</small></td>
    <td>${escapeHtml(displayTime(item.last_success))}</td>
    <td class="align-right"><button class="table-action" data-test-interface="${escapeHtml(item.id)}" type="button">测试</button></td>
  </tr>`;
}

function emptyRow(columns, text) {
  return `<tr><td colspan="${columns}"><div class="empty-state">${escapeHtml(text)}</div></td></tr>`;
}

async function loadInterfaces() {
  try {
    const data = await AdminAPI.json('/api/admin/interfaces');
    state.interfaces = data.items || [];
    renderInterfaces();
  } catch (error) {
    handleApiError(error, '接口状态读取失败');
  }
}

function renderInterfaces() {
  const category = $('#interface-category').value;
  const rows = state.interfaces.filter(item => !category || item.category === category);
  const failed = state.interfaces.filter(item => !['healthy', 'running'].includes(item.status)).length;
  $('#interface-summary').textContent = `共 ${state.interfaces.length} 项 · ${failed} 项需关注`;
  $('#interface-rows').innerHTML = rows.map(item => `<tr>
    <td><div class="table-service"><i class="status-dot ${escapeHtml(item.status)}"></i><div><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.provider)}</small></div></div></td>
    <td>${stateLabel(item.status)}</td><td>${escapeHtml(item.purpose)}</td><td>${escapeHtml(item.coverage || '—')}</td><td>${escapeHtml(item.frequency || '—')}</td><td>${escapeHtml(displayTime(item.last_success))}</td><td class="align-right"><button class="table-action" data-test-interface="${escapeHtml(item.id)}" type="button">测试</button></td>
  </tr>`).join('') || emptyRow(7, '没有符合条件的接口');
  bindInterfaceActions($('#interface-rows'));
}

function bindInterfaceActions(container) {
  container.querySelectorAll('[data-test-interface]').forEach(button => {
    button.addEventListener('click', () => testInterfaces([button.dataset.testInterface], button));
  });
}

async function testInterfaces(interfaceIds, button) {
  await withButton(button, async () => {
    try {
      const job = await AdminAPI.json('/api/admin/interfaces/test', {
        method: 'POST', body: {interface_ids: interfaceIds}
      });
      toast('接口测试已开始');
      pollJob(job.job_id);
    } catch (error) {
      handleApiError(error, '接口测试启动失败');
    }
  });
}

async function pollJob(jobId) {
  clearTimeout(state.jobTimer);
  try {
    const job = await AdminAPI.json(`/api/admin/jobs/${encodeURIComponent(jobId)}`);
    if (['queued', 'running'].includes(job.status)) {
      state.jobTimer = setTimeout(() => pollJob(jobId), 2000);
      return;
    }
    toast(`${job.kind === 'interface_test' ? '接口测试' : '刷新任务'}：${statusText(job.status)}`, job.status === 'succeeded' ? 'success' : job.status === 'failed' ? 'error' : '');
    loadOverview();
    if (state.view === 'interfaces') loadInterfaces();
    if (state.view === 'jobs') loadJobs();
  } catch (error) {
    handleApiError(error, '任务状态读取失败');
  }
}

function companyQuery() {
  const params = new URLSearchParams({
    page: String(state.companies.page || 1),
    page_size: String(state.companies.page_size || 50)
  });
  for (const [key, value] of Object.entries(state.companyFilters)) {
    if (value) params.set(key, value);
  }
  return params;
}

async function loadCompanies(resetPage = false) {
  if (resetPage) state.companies.page = 1;
  try {
    const data = await AdminAPI.json(`/api/admin/companies?${companyQuery()}`);
    state.companies = data;
    renderCompanies();
  } catch (error) {
    handleApiError(error, '公司名录读取失败');
  }
}

function fillFacet(select, values, current) {
  const first = select.options[0]?.outerHTML || '<option value="">全部</option>';
  select.innerHTML = first + (values || []).map(item => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.value)} (${item.count})</option>`).join('');
  select.value = current || '';
}

function renderCompanies() {
  const data = state.companies;
  fillFacet($('#company-country'), data.facets?.countries, state.companyFilters.country);
  fillFacet($('#company-exchange'), data.facets?.exchanges, state.companyFilters.exchange);
  fillFacet($('#company-status'), data.facets?.statuses, state.companyFilters.status);
  $('#company-result-count').textContent = `共 ${formatNumber(data.total)} 家 · 当前显示 ${data.items.length} 家`;
  const maxPage = Math.max(1, Math.ceil(data.total / data.page_size));
  $('#company-page-label').textContent = `第 ${data.page} / ${maxPage} 页`;
  $('#company-prev').disabled = data.page <= 1;
  $('#company-next').disabled = data.page >= maxPage;
  $('#nav-company-count').textContent = data.total || '';
  $('#company-rows').innerHTML = (data.items || []).map(row => {
    const change = row.change === null || row.change === undefined ? '—' : `${Number(row.change) >= 0 ? '+' : ''}${formatNumber(row.change, 2)}%`;
    const changeClass = Number(row.change) >= 0 ? 'healthy' : 'failed';
    return `<tr>
      <td><strong>${escapeHtml(row.t)}</strong><small class="table-sub">${escapeHtml(row.exchange)}</small></td>
      <td><div class="company-name" title="${escapeHtml(row.name)}">${escapeHtml(row.name || '—')}</div></td>
      <td>${escapeHtml(row.country || '—')}<small class="table-sub">${escapeHtml(row.city || '')}</small></td>
      <td>${escapeHtml(row.seg || '—')}</td><td>${escapeHtml(row.source || '—')}</td><td>${stateLabel(row.status)}</td>
      <td class="align-right"><span class="state-label ${changeClass}">${change}</span><small class="table-sub">${row.latest_price === null || row.latest_price === undefined ? '' : formatNumber(row.latest_price, 3)}</small></td>
      <td class="align-right"><div class="row-actions"><button class="table-action neutral" data-edit-company="${escapeHtml(row.t)}" type="button">编辑</button><button class="table-action ${row.enabled ? 'danger' : ''}" data-toggle-company="${escapeHtml(row.t)}" data-enabled="${row.enabled ? 'false' : 'true'}" type="button">${row.enabled ? '停用' : '启用'}</button></div></td>
    </tr>`;
  }).join('') || emptyRow(8, '没有符合条件的公司');
  $('#company-rows').querySelectorAll('[data-edit-company]').forEach(button => button.addEventListener('click', () => openCompanyEditor(button.dataset.editCompany)));
  $('#company-rows').querySelectorAll('[data-toggle-company]').forEach(button => button.addEventListener('click', () => toggleCompany(button.dataset.toggleCompany, button.dataset.enabled === 'true', button)));
}

function openCompanyEditor(ticker) {
  const row = state.companies.items.find(item => item.t === ticker);
  if (!row) return;
  $('#edit-ticker').value = row.t || '';
  $('#edit-name').value = row.name || '';
  $('#edit-country').value = row.country || '';
  $('#edit-city').value = row.city || '';
  $('#edit-lat').value = row.lat ?? '';
  $('#edit-lon').value = row.lon ?? '';
  $('#edit-seg').value = row.seg || '';
  $('#edit-chain').value = row.chain || '';
  $('#edit-chain-key').value = row.chain_key || '';
  $('#company-form-error').textContent = '';
  $('#company-dialog').showModal();
}

async function saveCompany(button) {
  const ticker = $('#edit-ticker').value;
  const updates = {
    name: $('#edit-name').value.trim(), country: $('#edit-country').value.trim(),
    city: $('#edit-city').value.trim(), seg: $('#edit-seg').value.trim(),
    chain: $('#edit-chain').value.trim(), chain_key: $('#edit-chain-key').value.trim()
  };
  const lat = $('#edit-lat').value;
  const lon = $('#edit-lon').value;
  if (lat !== '') updates.lat = Number(lat);
  if (lon !== '') updates.lon = Number(lon);
  await withButton(button, async () => {
    try {
      await AdminAPI.json('/api/admin/companies/update', {method: 'POST', body: {ticker, updates}});
      $('#company-dialog').close();
      toast(`${ticker} 已更新`, 'success');
      loadCompanies();
    } catch (error) {
      $('#company-form-error').textContent = error.message;
    }
  });
}

async function toggleCompany(ticker, enabled, button) {
  const verb = enabled ? '启用' : '停用';
  if (!window.confirm(`确认${verb} ${ticker}？`)) return;
  await withButton(button, async () => {
    try {
      await AdminAPI.json('/api/admin/companies/toggle', {method: 'POST', body: {ticker, enabled}});
      toast(`${ticker} 已${verb}`, 'success');
      loadCompanies();
      loadOverview();
    } catch (error) {
      handleApiError(error, `${verb}失败`);
    }
  });
}

async function previewImport(file) {
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) {
    toast('文件超过 10 MB 限制', 'error');
    return;
  }
  try {
    const preview = await AdminAPI.json(`/api/admin/import/preview?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST', body: file
    });
    state.preview = preview;
    renderImportPreview();
    toast('预检完成', 'success');
  } catch (error) {
    handleApiError(error, '导入预检失败');
  }
}

function renderImportPreview() {
  const preview = state.preview;
  if (!preview) return;
  $('#import-preview').hidden = false;
  $('#import-preview-file').textContent = `${preview.filename} · 预检有效期 30 分钟`;
  const labels = {new: '新增', update: '更新', unchanged: '不变', would_disable: '将停用', invalid: '无效', duplicate: '重复'};
  $('#import-summary').innerHTML = Object.entries(labels).map(([key, label]) => `<div class="summary-item"><span>${label}</span><strong>${formatNumber(preview.summary?.[key] || 0)}</strong></div>`).join('');
  $('#import-preview-rows').innerHTML = (preview.rows || []).slice(0, 500).map(row => {
    const fields = Object.keys(row.values || {}).filter(key => !['ticker', 'enabled'].includes(key));
    return `<tr><td>${row.row || '—'}</td><td><strong>${escapeHtml(row.ticker || '—')}</strong></td><td>${stateLabel(row.classification)}</td><td>${escapeHtml(fields.join('、') || '—')}</td><td>${escapeHtml((row.errors || []).join('；') || '—')}</td></tr>`;
  }).join('') || emptyRow(5, '文件中没有可预检的数据行');
  $$('.step-line li').forEach((item, index) => item.classList.toggle('active', index <= 1));
}

function downloadPreviewReport() {
  if (!state.preview) return;
  const lines = [['row', 'ticker', 'classification', 'errors']];
  for (const row of state.preview.rows || []) {
    lines.push([row.row, row.ticker, row.classification, (row.errors || []).join('; ')]);
  }
  const csv = lines.map(row => row.map(value => `"${String(value ?? '').replaceAll('"', '""')}"`).join(',')).join('\n');
  downloadBlob(new Blob(['\ufeff' + csv], {type: 'text/csv;charset=utf-8'}), 'aicm_import_preview.csv');
}

async function applyImport(mode, confirmation, button) {
  if (!state.preview) return;
  await withButton(button, async () => {
    try {
      const result = await AdminAPI.json('/api/admin/import/apply', {
        method: 'POST',
        body: {preview_id: state.preview.preview_id, mode, confirmation}
      });
      state.preview = null;
      $('#import-preview').hidden = true;
      $('#import-file').value = '';
      if ($('#replace-dialog').open) $('#replace-dialog').close();
      $$('.step-line li').forEach(item => item.classList.add('active'));
      toast(`导入完成：应用 ${result.applied} 行`, 'success');
      loadOverview();
    } catch (error) {
      if (mode === 'replace') $('#replace-error').textContent = error.message;
      else handleApiError(error, '安全合并失败');
    }
  });
}

async function runExport(button) {
  const scope = $('input[name="export-scope"]:checked').value;
  const params = new URLSearchParams({
    format: state.exportFormat,
    scope,
    include_quotes: $('#export-quotes').checked ? 'true' : 'false'
  });
  if (scope === 'filtered') {
    for (const [key, value] of Object.entries(state.companyFilters)) if (value) params.set(key, value);
  }
  await withButton(button, async () => {
    try {
      const stamp = new Date().toISOString().slice(0, 19).replaceAll(/[-:T]/g, '');
      await AdminAPI.download(`/api/admin/export?${params}`, `aicm_companies_${scope}_${stamp}.${state.exportFormat}`);
      toast('导出文件已生成', 'success');
    } catch (error) {
      handleApiError(error, '导出失败');
    }
  });
}

async function startRefresh(kind, button) {
  if (kind === 'history' && !window.confirm('历史窗口刷新耗时较长，且可能受数据源限流影响。确认启动？')) return;
  await withButton(button, async () => {
    try {
      const job = await AdminAPI.json('/api/admin/jobs/start', {method: 'POST', body: {kind}});
      toast(job.already_running ? '同类任务已在运行' : '刷新任务已开始');
      pollJob(job.job_id);
      loadJobs();
    } catch (error) {
      handleApiError(error, '刷新任务启动失败');
    }
  });
}

async function loadJobs() {
  const params = new URLSearchParams();
  if ($('#job-kind').value) params.set('kind', $('#job-kind').value);
  if ($('#job-status').value) params.set('status', $('#job-status').value);
  try {
    const data = await AdminAPI.json(`/api/admin/jobs?${params}`);
    state.jobs = data.items || [];
    $('#nav-job-count').textContent = state.jobs.filter(job => ['queued', 'running'].includes(job.status)).length || '';
    $('#job-rows').innerHTML = state.jobs.map(job => `<tr><td><strong>${escapeHtml(job.kind)}</strong><small class="table-sub">${escapeHtml(job.job_id)}</small></td><td>${escapeHtml(job.trigger)}</td><td>${stateLabel(job.status)}</td><td>${escapeHtml(displayTime(job.started_at || job.created_at))}</td><td>${escapeHtml(formatDuration(job.elapsed_s))}</td><td title="${escapeHtml(job.error || '')}">${escapeHtml(job.error || summarizeResult(job.result))}</td><td class="align-right"><button class="table-action neutral" data-job-detail="${escapeHtml(job.job_id)}" type="button">详情</button></td></tr>`).join('') || emptyRow(7, '暂无任务记录');
    $('#job-rows').querySelectorAll('[data-job-detail]').forEach(button => button.addEventListener('click', () => openJobDetail(button.dataset.jobDetail)));
  } catch (error) {
    handleApiError(error, '任务记录读取失败');
  }
}

function summarizeResult(result) {
  if (!result || !Object.keys(result).length) return '—';
  return Object.entries(result).slice(0, 3).map(([key, value]) => `${key}: ${typeof value === 'object' ? '已记录' : value}`).join(' · ');
}

function openJobDetail(jobId) {
  const job = state.jobs.find(item => item.job_id === jobId);
  if (!job) return;
  $('#job-detail-title').textContent = `${job.kind} · ${statusText(job.status)}`;
  $('#job-detail').innerHTML = `<dl><dt>任务 ID</dt><dd>${escapeHtml(job.job_id)}</dd><dt>触发方式</dt><dd>${escapeHtml(job.trigger)}</dd><dt>开始时间</dt><dd>${escapeHtml(displayTime(job.started_at))}</dd><dt>结束时间</dt><dd>${escapeHtml(displayTime(job.finished_at))}</dd><dt>耗时</dt><dd>${escapeHtml(formatDuration(job.elapsed_s))}</dd><dt>错误</dt><dd>${escapeHtml(job.error || '—')}</dd></dl><pre class="job-log">${escapeHtml((job.log_tail || []).join('\n') || '没有日志输出')}</pre>`;
  $('#job-dialog').showModal();
}

async function loadAudit() {
  const params = new URLSearchParams();
  if ($('#audit-action').value.trim()) params.set('action', $('#audit-action').value.trim());
  if ($('#audit-result').value) params.set('result', $('#audit-result').value);
  if ($('#audit-from').value) params.set('date_from', $('#audit-from').value);
  if ($('#audit-to').value) params.set('date_to', $('#audit-to').value);
  try {
    const data = await AdminAPI.json(`/api/admin/audit?${params}`);
    state.audit = data.items || [];
    $('#audit-rows').innerHTML = state.audit.map(item => `<tr><td>${escapeHtml(displayTime(item.timestamp))}</td><td><strong>${escapeHtml(item.action)}</strong></td><td>${stateLabel(item.result)}</td><td>${escapeHtml(item.ticker || item.job_id || item.preview_id || '—')}</td><td>${formatNumber(item.affected_count)}</td><td>${escapeHtml(item.message || item.error || '—')}</td></tr>`).join('') || emptyRow(6, '没有符合条件的审计记录');
  } catch (error) {
    handleApiError(error, '审计记录读取失败');
  }
}

function handleApiError(error, prefix) {
  if (error.status === 403) {
    sessionStorage.removeItem('aicmAdminToken');
    clearInterval(state.overviewTimer);
    showAuth('Token 已失效，请重新验证');
    return;
  }
  toast(`${prefix}：${error.message}`, 'error');
}

async function loadUsers() {
  try {
    const data = await AdminAPI.json('/api/admin/users');
    $('#nav-user-count').textContent = data.total || '';
    $('#user-rows').innerHTML = (data.items || []).map(user => `<tr><td><strong>${escapeHtml(user.display_name)}</strong><small class="table-sub">${escapeHtml(user.user_id)}</small></td><td>${escapeHtml(user.role)}</td><td>${stateLabel(user.status === 'active' ? 'healthy' : 'failed')}</td><td>${formatNumber(user.token_count)}</td><td>${escapeHtml(displayTime(user.last_activity))}</td><td class="align-right">${user.role === 'owner' ? '—' : `<button class="table-action danger" data-revoke-user="${escapeHtml(user.user_id)}" type="button">撤销 Token</button>`}</td></tr>`).join('') || emptyRow(6, '暂无用户');
    $('#user-rows').querySelectorAll('[data-revoke-user]').forEach(button => button.addEventListener('click', async () => {
      if (!confirm('确认撤销该用户的全部 Token？')) return;
      await AdminAPI.json(`/api/admin/users/${button.dataset.revokeUser}/revoke`, {method:'POST', body:{}}); toast('用户 Token 已撤销','success'); loadUsers();
    }));
  } catch (error) { handleApiError(error, '用户列表读取失败'); }
}

async function saveUser() {
  try {
    const data = await AdminAPI.json('/api/admin/users', {method:'POST', body:{display_name:$('#new-user-name').value.trim(), role:$('#new-user-role').value}});
    $('#user-dialog').close(); $('#issued-token').value = data.token; $('#issued-token-dialog').showModal(); loadUsers();
  } catch (error) { $('#user-form-error').textContent = error.message; }
}

function bindEvents() {
  $('#auth-form').addEventListener('submit', event => {
    event.preventDefault();
    withButton($('#auth-submit'), () => authenticate($('#auth-token').value.trim()).catch(() => {}));
  });
  $('#toggle-token').addEventListener('click', () => {
    const input = $('#auth-token');
    input.type = input.type === 'password' ? 'text' : 'password';
  });
  $('#auth-return').addEventListener('click', () => navigateWithFlip('/', 'to-dashboard'));
  $('#return-dashboard').addEventListener('click', () => navigateWithFlip('/', 'to-dashboard'));
  $('#lock-admin').addEventListener('click', () => {
    sessionStorage.removeItem('aicmAdminToken');
    clearInterval(state.overviewTimer);
    showAuth();
  });
  $('#mobile-nav-toggle').addEventListener('click', () => $('#admin-nav').classList.toggle('open'));
  $('#create-user').addEventListener('click', () => { $('#new-user-name').value=''; $('#user-form-error').textContent=''; $('#user-dialog').showModal(); });
  $('#save-user').addEventListener('click', saveUser);
  $('#copy-issued-token').addEventListener('click', () => navigator.clipboard.writeText($('#issued-token').value).then(() => toast('Token 已复制','success')));
  $$('.nav-item').forEach(item => item.addEventListener('click', () => setView(item.dataset.view)));
  $$('[data-open-view]').forEach(item => item.addEventListener('click', () => setView(item.dataset.openView)));
  $('#overview-test-all').addEventListener('click', event => testInterfaces(null, event.currentTarget));
  $('#interfaces-test-all').addEventListener('click', event => testInterfaces(null, event.currentTarget));
  $('#overview-refresh-live').addEventListener('click', event => startRefresh('live', event.currentTarget));
  $('#interface-category').addEventListener('change', renderInterfaces);

  let searchTimer;
  $('#company-search').addEventListener('input', event => {
    state.companyFilters.q = event.target.value.trim();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadCompanies(true), 300);
  });
  for (const [selector, key] of [['#company-country', 'country'], ['#company-exchange', 'exchange'], ['#company-status', 'status']]) {
    $(selector).addEventListener('change', event => { state.companyFilters[key] = event.target.value; loadCompanies(true); });
  }
  $('#company-clear-filters').addEventListener('click', () => {
    state.companyFilters = {q: '', country: '', exchange: '', status: ''};
    $('#company-search').value = '';
    loadCompanies(true);
  });
  $('#company-page-size').addEventListener('change', event => { state.companies.page_size = Number(event.target.value); loadCompanies(true); });
  $('#company-prev').addEventListener('click', () => { state.companies.page -= 1; loadCompanies(); });
  $('#company-next').addEventListener('click', () => { state.companies.page += 1; loadCompanies(); });
  $('#save-company').addEventListener('click', event => saveCompany(event.currentTarget));

  $('#download-blank-template').addEventListener('click', event => withButton(event.currentTarget, () => AdminAPI.download('/api/admin/templates/companies.xlsx?variant=blank', 'AI算力链上市公司导入模板_空白.xlsx').catch(error => handleApiError(error, '模板下载失败'))));
  $('#download-example-template').addEventListener('click', event => withButton(event.currentTarget, () => AdminAPI.download('/api/admin/templates/companies.xlsx?variant=example', 'AI算力链上市公司导入模板_示例.xlsx').catch(error => handleApiError(error, '模板下载失败'))));
  $('#import-file').addEventListener('change', event => previewImport(event.target.files[0]));
  const dropzone = $('#import-dropzone');
  dropzone.addEventListener('dragover', event => { event.preventDefault(); dropzone.classList.add('dragover'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone.addEventListener('drop', event => { event.preventDefault(); dropzone.classList.remove('dragover'); previewImport(event.dataTransfer.files[0]); });
  $('#download-preview-report').addEventListener('click', downloadPreviewReport);
  $('#apply-merge').addEventListener('click', event => applyImport('merge', '', event.currentTarget));
  $('#replace-catalog').addEventListener('click', () => { $('#replace-confirmation').value = ''; $('#replace-error').textContent = ''; $('#replace-dialog').showModal(); });
  $('#confirm-replace').addEventListener('click', event => applyImport('replace', $('#replace-confirmation').value, event.currentTarget));

  $$('#export-format button').forEach(button => button.addEventListener('click', () => {
    state.exportFormat = button.dataset.format;
    $$('#export-format button').forEach(item => item.classList.toggle('active', item === button));
  }));
  $('#run-export').addEventListener('click', event => runExport(event.currentTarget));

  $('#jobs-refresh-live').addEventListener('click', event => startRefresh('live', event.currentTarget));
  $('#jobs-refresh-history').addEventListener('click', event => startRefresh('history', event.currentTarget));
  $('#reload-jobs').addEventListener('click', loadJobs);
  $('#job-kind').addEventListener('change', loadJobs);
  $('#job-status').addEventListener('change', loadJobs);
  $('#reload-audit').addEventListener('click', loadAudit);
}

function startConsole() {
  clearInterval(state.overviewTimer);
  setView(state.view || 'overview');
  state.overviewTimer = setInterval(() => {
    if (!$('#admin-shell').hidden) loadOverview();
  }, 15000);
  refreshIcons();
}

async function initialize() {
  prepareFlipIn();
  bindEvents();
  refreshIcons();
  const token = sessionStorage.getItem('aicmAdminToken');
  if (!token) {
    showAuth();
    return;
  }
  try {
    await authenticate(token);
  } catch (_) {
    $('#auth-token').focus();
  }
}

initialize();
