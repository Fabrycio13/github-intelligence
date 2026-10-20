'use strict';
const message = document.querySelector('#message');
const listing = document.querySelector('#repositories');
const statuses = {pending:'Na fila', running:'Em andamento', succeeded:'Concluída', partial:'Parcial', failed:'Falhou'};
let busy = false;
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function notify(text, error=false) { message.textContent = text; message.dataset.error = String(error); }
async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Não foi possível concluir a operação.');
  return data;
}
function time(value) { return value ? new Date(value).toLocaleString('pt-BR') : 'Ainda não registrada'; }
function inputDate(value) { return new Date(value).toISOString().slice(0,16); }
async function openReport(id, name) {
  try {
    const report = await request('/api/reports/' + encodeURIComponent(id));
    document.querySelector('#report-title').textContent = 'Relatório · ' + name;
    document.querySelector('#report-content').textContent = report.content;
    document.querySelector('#report-status').textContent = (report.complete ? 'Concluído dentro da cobertura declarada' : 'Relatório parcial') + ' · Gerado em ' + time(report.generated_at);
    const section = document.querySelector('#report');
    section.hidden = false;
    section.scrollIntoView({behavior: 'smooth', block:'start'});
  } catch (error) { notify(error.message, true); }
}
function render(repo, serverBusy) {
  const article = el('article');
  article.append(el('h3', repo.full_name));
  const dl = el('dl');
  const pairs = [
    ['Monitoramento', repo.status === 'active' ? 'Ativo' : 'Inativo'],
    ['Última coleta', statuses[repo.last_status] || 'Nenhuma coleta'],
    ['Início', time(repo.last_started_at)],
    ['Conclusão', time(repo.last_finished_at)],
    ['Intervalo (UTC)', repo.window_start ? repo.window_start + ' → ' + repo.window_end : 'Ainda não registrado']
  ];
  for (const [label, value] of pairs) dl.append(el('dt', label), el('dd', value));
  article.append(dl);
  const actions = el('div', undefined, 'actions');
  if (repo.report_id) {
    const open = el('button', 'Abrir relatório salvo'); open.type = 'button';
    open.addEventListener('click', () => openReport(repo.report_id, repo.full_name)); actions.append(open);
  } else { actions.append(el('p', 'Sem relatório salvo para a última coleta.')); }
  article.append(actions);
  const form = el('form');
  const startLabel = el('label', 'Início inclusivo (UTC)');
  const start = el('input'); start.type = 'datetime-local'; start.required = true; start.name = 'since';
  const now = new Date();
  start.value = inputDate(repo.window_start || new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(),1)));
  const endLabel = el('label', 'Fim exclusivo (UTC)');
  const end = el('input'); end.type = 'datetime-local'; end.required = true; end.name = 'until';
  end.value = inputDate(now); end.max = inputDate(now);
  startLabel.append(start); endLabel.append(end);
  const update = el('button', 'Atualizar agora'); update.type = 'submit';
  update.disabled = busy || serverBusy || repo.status !== 'active' || repo.installation_status !== 'active' || repo.last_status === 'running' || repo.last_status === 'pending';
  form.append(startLabel, endLabel, update);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    const since = new Date(start.value + 'Z'); const until = new Date(end.value + 'Z');
    if (!(since < until) || until > new Date()) { notify('Use início anterior ao fim, sem datas futuras.',true); return; }
    if (!window.confirm('Consultar o GitHub e gravar uma nova coleta de ' + repo.full_name + ' no Supabase?\nIntervalo UTC: ' + since.toISOString() + ' até ' + until.toISOString())) return;
    busy = true;
    document.querySelectorAll('form button').forEach(button => button.disabled = true);
    notify('Coletando ' + repo.full_name + '… Aguarde; não feche o painel.');
    let result;
    let failure;
    try {
      result = await request('/api/update', {
        method:'POST', headers: {'Content-Type':'application/json', 'X-Request-Token':document.querySelector('meta[name="request-token"]').content},
        body:JSON.stringify({repository_id:repo.id, since:since.toISOString(), until:until.toISOString()})
      });
    } catch (error) { failure = error; }
    finally { busy = false; document.querySelector('#inspect-repository').disabled = false; }
    await load();
    notify(failure ? failure.message : result.message, !!failure);
  });
  article.append(form);
  return article;
}
async function load() {
  if (busy) return;
  const reload = document.querySelector('#reload'); reload.disabled = true;
  try {
    const data = await request('/api/repositories');
    listing.replaceChildren(...data.repositories.map(repo => render(repo, data.updating)));
    notify(data.repositories.length ? 'Dados do Supabase. Nenhuma coleta automática.' : 'Nenhum repositório provisionado neste projeto.');
  } catch (error) { notify(error.message,true); }
  finally { reload.disabled = false; }
}
document.querySelector('#reload').addEventListener('click', load);
document.querySelector('#close-report').addEventListener('click', () => document.querySelector('#report').hidden = true);
const onboardingSection = document.querySelector('#onboarding');
const inspectForm = document.querySelector('#inspect-form');
const linkInput = document.querySelector('#repository-link');
const inspectButton = document.querySelector('#inspect-repository');
const onboardingMessage = document.querySelector('#onboarding-message');
const onboardingResult = document.querySelector('#onboarding-result');
document.querySelector('#add-repository').addEventListener('click', () => {
  onboardingSection.hidden = !onboardingSection.hidden;
  document.querySelector('#add-repository').setAttribute('aria-expanded', String(!onboardingSection.hidden));
  if (!onboardingSection.hidden) linkInput.focus();
});
linkInput.addEventListener('input', () => {
  onboardingResult.replaceChildren(); onboardingMessage.textContent = ''; inspectButton.textContent = 'Verificar acesso';
});
function post(path, payload) {
  return request(path, {method:'POST', headers:{'Content-Type':'application/json',
    'X-Request-Token':document.querySelector('meta[name="request-token"]').content}, body:JSON.stringify(payload)});
}
function showInspection(result, link) {
  onboardingResult.replaceChildren();
  if (result.status === 'authorization_required') {
    onboardingMessage.textContent = result.message || 'A App ainda não tem acesso. Autorize no GitHub e depois verifique novamente.';
    const url = new URL(result.authorization_url);
    if (url.origin !== 'https://github.com' || !/^\/apps\/[a-z0-9-]+\/installations\/new$/.test(url.pathname)) throw new Error('Endereço de autorização inválido.');
    const authorize = el('a', 'Autorizar no GitHub'); authorize.href = url.href; authorize.target = '_blank'; authorize.rel = 'noopener noreferrer';
    onboardingResult.append(authorize);
    inspectButton.textContent = 'Já autorizei — verificar novamente';
    return;
  }
  if (result.status !== 'ready') throw new Error('Resposta de verificação inesperada.');
  onboardingMessage.textContent = result.full_name + ' · Acesso confirmado pela instalação ' + result.installation_id + '. ' + (result.message || 'Confirme para vincular ao projeto.');
  const confirm = el('button', 'Confirmar vínculo'); confirm.type = 'button'; confirm.disabled = !result.can_bind;
  confirm.addEventListener('click', async () => {
    if (busy || !window.confirm('Vincular ' + result.full_name + ' a este projeto? Nenhuma coleta será iniciada.')) return;
    busy = true; confirm.disabled = true; inspectButton.disabled = true; linkInput.disabled = true;
    onboardingMessage.textContent = 'Revalidando acesso e salvando vínculo…';
    try {
      const expected = {full_name:result.full_name, github_id:result.github_id,
        app_id:result.app_id, installation_id:result.installation_id};
      const bound = await post('/api/repositories/bind', {link, expected});
      if (bound.status === 'bound') {
        onboardingResult.replaceChildren(); onboardingMessage.textContent = bound.message;
        busy = false; await load();
      } else { showInspection(bound, link); }
    } catch (error) { onboardingMessage.textContent = error.message; confirm.disabled = false; }
    finally { busy = false; inspectButton.disabled = false; linkInput.disabled = false; }
  });
  onboardingResult.append(confirm);
}
inspectForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (busy || !inspectForm.reportValidity()) return;
  const link = linkInput.value;
  busy = true; inspectButton.disabled = true; linkInput.disabled = true; onboardingResult.replaceChildren();
  onboardingMessage.textContent = 'Verificando acesso no GitHub…';
  try { showInspection(await post('/api/repositories/inspect', {link}), link); }
  catch (error) { onboardingMessage.textContent = error.message; }
  finally { busy = false; inspectButton.disabled = false; linkInput.disabled = false; }
});
load();
