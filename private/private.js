const $ = selector => document.querySelector(selector);
const $$ = selector => Array.from(document.querySelectorAll(selector));

const api = {
  token: () => sessionStorage.getItem('aicmPersonalToken') || '',
  async call(path, options = {}, tokenOverride = '') {
    const headers = new Headers(options.headers || {});
    headers.set('Authorization', 'Bearer ' + (tokenOverride || this.token()));
    if (options.body && typeof options.body !== 'string') {
      headers.set('Content-Type', 'application/json');
      options = { ...options, body: JSON.stringify(options.body) };
    }
    const response = await fetch(path, { ...options, headers });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error?.message || '请求失败');
    return payload.data;
  },
  async download(path) {
    const response = await fetch(path, { headers: { Authorization: 'Bearer ' + this.token() } });
    if (!response.ok) throw new Error('下载失败');
    return { blob: await response.blob(), disposition: response.headers.get('Content-Disposition') || '' };
  },
};

const esc = value => String(value ?? '')
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;');

let noticeTimer;
function showNotice(message, isError = false) {
  const notice = $('#workspace-notice');
  clearTimeout(noticeTimer);
  notice.textContent = message;
  notice.classList.toggle('is-error', isError);
  notice.hidden = false;
  noticeTimer = setTimeout(() => { notice.hidden = true; }, 5000);
}

async function login(token) {
  sessionStorage.setItem('aicmPersonalToken', token);
  const profile = await api.call('/api/v1/me/profile', {}, token);
  $('#private-auth-error').textContent = '';
  $('#profile-name').textContent = profile.user_id;
  if (profile.support_grant) {
    $('#effective-user-banner').textContent = `临时支持模式 · 当前作用域 ${profile.user_id}`;
    $('#effective-user-banner').hidden = false;
  }
  $('#private-auth').hidden = true;
  $('#private-shell').hidden = false;
  await Promise.all([loadWatchlist(), loadInterfaces()]);
}

async function loadWatchlist() {
  const data = await api.call('/api/v1/me/watchlist');
  $('#personal-watchlist-rows').innerHTML = data.items.map(item => `
    <tr>
      <td><strong>${esc(item.ticker)}</strong></td>
      <td>${esc(item.name || '—')}</td>
      <td>${esc(item.quote?.p ?? '—')}</td>
      <td>${esc(item.quote?.chg ?? '—')}</td>
      <td><button class="danger" type="button" data-remove-ticker="${esc(item.ticker)}">移除</button></td>
    </tr>`).join('') || '<tr><td class="empty" colspan="5">暂无自选股</td></tr>';
}

async function loadInterfaces() {
  const data = await api.call('/api/v1/me/interfaces');
  $('#personal-interface-rows').innerHTML = data.items.map(item => `
    <tr>
      <td><strong>${esc(item.name)}</strong><small>${esc(item.provider)}</small></td>
      <td>${esc(item.monitor_mode)}</td>
      <td>${esc(item.url || '本地检测')}</td>
      <td>${esc(item.interval_minutes)} 分钟</td>
      <td>${esc(item.origin)}</td>
      <td><div class="row-actions">
        <button class="quiet" type="button" data-test-interface="${esc(item.id)}">测试</button>
        ${item.origin === 'custom' ? `<button class="danger" type="button" data-delete-interface="${esc(item.id)}">删除</button>` : ''}
      </div></td>
    </tr>`).join('') || '<tr><td class="empty" colspan="6">暂无监测接口</td></tr>';
}

async function loadAudit() {
  const data = await api.call('/api/v1/me/audit');
  $('#audit-rows').innerHTML = data.items.map(item => `
    <tr><td>${esc(item.created_at)}</td><td>${esc(item.action)}</td><td>${esc(item.result)}</td><td>${esc(item.effective_user_id || '—')}</td></tr>`
  ).join('') || '<tr><td class="empty" colspan="4">暂无审计记录</td></tr>';
}

$('#private-auth-form').addEventListener('submit', event => {
  event.preventDefault();
  const submit = event.submitter;
  const submittedToken = $('#private-token').value.trim();
  submit.disabled = true;
  login(submittedToken)
    .catch(error => {
      $('#private-auth-error').textContent = error.message;
      if (sessionStorage.getItem('aicmPersonalToken') === submittedToken) sessionStorage.removeItem('aicmPersonalToken');
    })
    .finally(() => { submit.disabled = false; });
});

$('#private-lock').addEventListener('click', () => {
  sessionStorage.removeItem('aicmPersonalToken');
  location.reload();
});

$$('nav button').forEach(button => button.addEventListener('click', () => {
  $$('nav button').forEach(item => item.classList.toggle('active', item === button));
  $$('.panel').forEach(panel => panel.classList.toggle('active', panel.id === button.dataset.panel));
  if (button.dataset.panel === 'personal-audit') loadAudit().catch(error => showNotice(error.message, true));
}));

$('#add-ticker-form').addEventListener('submit', async event => {
  event.preventDefault();
  const submit = event.submitter;
  submit.disabled = true;
  try {
    await api.call('/api/v1/me/watchlist/add', {
      method:'POST',
      body: { items: [{ ticker: $('#new-ticker').value.trim(), name: $('#new-ticker-name').value.trim() }] },
    });
    event.target.reset();
    await loadWatchlist();
    showNotice('自选股已添加。');
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    submit.disabled = false;
  }
});

$('#personal-watchlist-rows').addEventListener('click', async event => {
  const button = event.target.closest('[data-remove-ticker]');
  if (!button) return;
  const ticker = button.dataset.removeTicker;
  if (!confirm(`确认从个人自选股移除 ${ticker}？`)) return;
  button.disabled = true;
  try {
    await api.call('/api/v1/me/watchlist/remove', { method:'POST', body: { tickers: [ticker] } });
    await loadWatchlist();
    showNotice(`${ticker} 已移除。`);
  } catch (error) {
    button.disabled = false;
    showNotice(error.message, true);
  }
});

$('#personal-interface-rows').addEventListener('click', async event => {
  const testButton = event.target.closest('[data-test-interface]');
  const deleteButton = event.target.closest('[data-delete-interface]');
  const button = testButton || deleteButton;
  if (!button) return;
  button.disabled = true;
  try {
    if (testButton) {
      const id = testButton.dataset.testInterface;
      const result = await api.call(`/api/v1/me/interfaces/${encodeURIComponent(id)}/test`, { method:'POST', body:{} });
      const history = await api.call(`/api/v1/me/interfaces/${encodeURIComponent(id)}/history?limit=1`);
      showNotice(`测试结果：${result.status}，耗时 ${result.latency_ms} ms，历史 ${history.total} 条。`, result.status !== 'healthy');
    } else {
      const id = deleteButton.dataset.deleteInterface;
      if (!confirm('确认删除这个个人接口？')) return;
      await api.call(`/api/v1/me/interfaces/${encodeURIComponent(id)}`, { method:'DELETE' });
      await loadInterfaces();
      showNotice('个人接口已删除。');
    }
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
});

const interfaceDialog = $('#interface-dialog');
function closeInterfaceDialog() {
  interfaceDialog.close();
  $('#interface-form-error').textContent = '';
}

function syncInterfaceMode() {
  const isHttp = $('#interface-mode').value === 'http';
  $('#interface-url').required = isHttp;
  $('#interface-url').disabled = !isHttp;
}

function syncInterfaceAuth() {
  const authType=$('#interface-auth-type').value;
  const hasSecret=authType!=='none';
  $('#interface-secret-wrap').hidden=!hasSecret;
  $('#interface-secret').required=hasSecret;
  const needsIdentity=authType==='api_key'||authType==='basic';
  $('#interface-auth-identity-wrap').hidden=!needsIdentity;
  $('#interface-auth-identity').required=needsIdentity;
}

$('#add-interface').addEventListener('click', () => {
  $('#interface-form').reset();
  $('#interface-interval').value = '15';
  $('#interface-timeout').value = '8';
  syncInterfaceMode();
  syncInterfaceAuth();
  interfaceDialog.showModal();
  $('#interface-name').focus();
});
$('#close-interface-dialog').addEventListener('click', closeInterfaceDialog);
$('#cancel-interface').addEventListener('click', closeInterfaceDialog);
$('#interface-mode').addEventListener('change', syncInterfaceMode);
$('#interface-auth-type').addEventListener('change', syncInterfaceAuth);

$('#interface-form').addEventListener('submit', async event => {
  event.preventDefault();
  const submit = $('#save-interface');
  submit.disabled = true;
  $('#interface-form-error').textContent = '';
  const mode = $('#interface-mode').value;
  let created;
  try {
    created=await api.call('/api/v1/me/interfaces', {
      method:'POST',
      body: {
        name: $('#interface-name').value.trim(),
        provider: $('#interface-provider').value.trim(),
        category: 'provider',
        purpose: '自定义健康监测',
        monitor_mode: mode,
        method: 'GET',
        url: mode === 'http' ? $('#interface-url').value.trim() : undefined,
        interval_minutes: Number($('#interface-interval').value),
        timeout_seconds: Number($('#interface-timeout').value),
      },
    });
    const authType=$('#interface-auth-type').value;
    if (authType!=='none') {
      const secret=$('#interface-secret').value;
      const identity=$('#interface-auth-identity').value.trim();
      const credential=authType==='bearer' ? {auth_type:authType,token:secret}
        : authType==='api_key' ? {auth_type:authType,header:identity,value:secret}
        : {auth_type:authType,username:identity,password:secret};
      await api.call(`/api/v1/me/interfaces/${encodeURIComponent(created.id)}/credentials`, {method:'POST',body:credential});
    }
    closeInterfaceDialog();
    await loadInterfaces();
    showNotice('监测接口已创建。');
  } catch (error) {
    if (created?.id) await api.call(`/api/v1/me/interfaces/${encodeURIComponent(created.id)}`, {method:'DELETE'}).catch(()=>{});
    $('#interface-form-error').textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

function saveDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

$('#download-interface-template').addEventListener('click', async () => {
  try {
    const result = await api.download('/api/v1/me/interfaces/template?variant=example');
    saveDownload(result.blob, 'interface_template_example.xlsx');
  } catch (error) { showNotice(error.message, true); }
});

$('#export-interfaces').addEventListener('click', async () => {
  try {
    const result = await api.download('/api/v1/me/interfaces/export?format=json');
    saveDownload(result.blob, 'private_interfaces.json');
  } catch (error) { showNotice(error.message, true); }
});

let currentPreviewId = '';
$('#preview-interface-import').addEventListener('click', async () => {
  const file = $('#interface-import-file').files[0];
  if (!file) { showNotice('请先选择导入文件。', true); return; }
  const button = $('#preview-interface-import'); button.disabled = true;
  try {
    const response = await fetch(`/api/v1/me/interfaces/import/preview?filename=${encodeURIComponent(file.name)}`, {
      method:'POST', headers:{ Authorization:'Bearer ' + api.token() }, body:file,
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error?.message || '预览失败');
    currentPreviewId = payload.data.preview_id;
    $('#interface-import-summary').textContent = `文件：${payload.data.filename}\n待导入：${payload.data.summary.rows} 条\n预览编号：${currentPreviewId}`;
    $('#apply-interface-import').disabled = false;
  } catch (error) { showNotice(error.message, true); }
  finally { button.disabled = false; }
});

$('#apply-interface-import').addEventListener('click', async () => {
  if (!currentPreviewId) return;
  const button = $('#apply-interface-import'); button.disabled = true;
  try {
    const result = await api.call('/api/v1/me/interfaces/import/apply', { method:'POST', body:{ preview_id:currentPreviewId } });
    currentPreviewId = '';
    $('#interface-import-summary').textContent = `已成功导入 ${result.applied} 条个人接口。`;
    await loadInterfaces();
    showNotice('接口台账导入完成。');
  } catch (error) { showNotice(error.message, true); button.disabled = false; }
});

$('#refresh-audit').addEventListener('click', () => loadAudit().catch(error => showNotice(error.message, true)));

let currentSupportGrantId = '';
$('#support-grant-form').addEventListener('submit', async event => {
  event.preventDefault();
  const scopes = $$('input[name="support-scope"]:checked').map(item => item.value);
  if (!scopes.length) { showNotice('至少选择一个授权范围。', true); return; }
  const button = event.submitter; button.disabled = true;
  const hours = Number($('#support-duration').value);
  const durationMs = (hours === 24 ? (24 * 60 - 1) : hours * 60) * 60 * 1000;
  try {
    const result = await api.call('/api/v1/me/support-grants', {
      method:'POST', body:{
        administrator_id:'owner', scopes, reason:$('#support-reason').value.trim(),
        expires_at:new Date(Date.now() + durationMs).toISOString(),
      },
    });
    currentSupportGrantId = result.grant_id;
    $('#support-grant-result').textContent = `授权编号：${result.grant_id}\n范围：${result.scopes.join('、')}\n到期：${result.expires_at}`;
    $('#revoke-support-grant').disabled = false;
    showNotice('临时支持授权已创建。');
  } catch (error) { showNotice(error.message, true); }
  finally { button.disabled = false; }
});

$('#revoke-support-grant').addEventListener('click', async () => {
  if (!currentSupportGrantId) return;
  const button = $('#revoke-support-grant'); button.disabled = true;
  try {
    await api.call(`/api/v1/me/support-grants/${encodeURIComponent(currentSupportGrantId)}/revoke`, { method:'POST', body:{} });
    $('#support-grant-result').textContent = `授权 ${currentSupportGrantId} 已撤销。`;
    currentSupportGrantId = '';
    showNotice('临时支持授权已撤销。');
  } catch (error) { showNotice(error.message, true); button.disabled = false; }
});

localStorage.removeItem('aicmPersonalToken');
const existing = sessionStorage.getItem('aicmPersonalToken');
if (existing) login(existing).catch(() => {
  if (sessionStorage.getItem('aicmPersonalToken') === existing) sessionStorage.removeItem('aicmPersonalToken');
});
