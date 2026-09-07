const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function app() {
  const nodes = new Map();
  const context = vm.createContext({
    document: {
      addEventListener() {},
      querySelector(selector) {
        if (!nodes.has(selector)) nodes.set(selector, {
          insertAdjacentHTML(position, html) { this.innerHTML = html + this.innerHTML; },
          innerHTML: '', textContent: '', value: '', checked: false,
          classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
        });
        return nodes.get(selector);
      },
      querySelectorAll() { return []; },
    },
    window: { addEventListener() {} },
    console, setTimeout, clearTimeout, URLSearchParams,
  });
  const source = fs.readFileSync(path.join(__dirname, '../../frontend/app.js'), 'utf8');
  // Execute the real functions without starting the application/network at the bottom.
  vm.runInContext(source.replace(/bindEvents\(\);\s*loadOverview\(\)\.catch\(showError\);\s*$/, ''), context);
  vm.runInContext(`
    var errors = [], requests = [];
    showError = (error) => errors.push(error.message);
    postJson = async (url, body) => { requests.push({url, body}); return {}; };
    var collection = (id) => ({collection: {collection_id: id, name: id, member_count: 0}, counts: {current: 0, alias: 0, missing: 0}, items: []});
    var plan = (ids) => ({summary: {counts: {}, total: ids.length, downloadable: ids.length}, items: ids.map(entity_id => ({entity_id, title: entity_id, source: 'open'}))});
  `, context);
  return (code) => vm.runInContext(code, context);
}

test('switching collections clears old selection and empty select-all', () => {
  const run = app();
  run(`renderCollectionDetail(collection('A')); renderDownloadPlan(plan(['a'])); state.planSelection.add('a'); $('#select-all-papers').checked = true; renderCollectionDetail(collection('B'));`);
  assert.equal(run('state.planSelection.size'), 0);
  assert.equal(run(`$('#select-all-papers').checked`), false);
});

test('same-collection refresh keeps valid selection but prunes removed members', () => {
  const run = app();
  run(`renderCollectionDetail(collection('A')); renderDownloadPlan(plan(['a', 'b'])); state.planSelection.add('a'); state.planSelection.add('b'); renderDownloadPlan(plan(['a']));`);
  assert.equal(run(`[...state.planSelection].join(',')`), 'a');
});

test('cleanup rejects out-of-plan IDs without sending a delete request', async () => {
  const run = app();
  await run(`(async () => { renderCollectionDetail(collection('B')); renderDownloadPlan(plan([])); state.planSelection.add('a'); await runCleanup('all'); })()`);
  assert.equal(run('requests.length'), 0);
  assert.equal(run('errors.length'), 1);
});

test('pipeline rejects stale selection before making a request', async () => {
  const run = app();
  await run(`(async () => { renderCollectionDetail(collection('B')); renderDownloadPlan(plan([])); state.planSelection.add('a'); await runPipeline(); })()`);
  assert.equal(run('requests.length'), 0);
  assert.match(run('errors[0]'), /范围/);
});

test('late download plan response cannot overwrite another collection', async () => {
  const run = app();
  await run(`(async () => {
    renderCollectionDetail(collection('A'));
    var resolveA;
    api = () => new Promise(resolve => { resolveA = resolve; });
    var pending = loadDownloadPlan();
    renderCollectionDetail(collection('B'));
    renderDownloadPlan(plan(['b']));
    resolveA(plan(['a']));
    await pending;
  })()`);
  assert.match(run(`$('#download-plan').innerHTML`), /data-entity="b"/);
  assert.doesNotMatch(run(`$('#download-plan').innerHTML`), /data-entity="a"/);
});

test('late collection detail response cannot switch back to earlier click', async () => {
  const run = app();
  await run(`(async () => {
    var resolveA;
    api = (url) => url === '/api/collections/A' ? new Promise(resolve => { resolveA = resolve; }) : Promise.resolve(collection('B'));
    loadDownloadPlan = async () => {};
    var pending = openCollection('A');
    await openCollection('B');
    resolveA(collection('A'));
    await pending;
  })()`);
  assert.equal(run('state.currentCollectionId'), 'B');
});

test('valid cleanup still submits exactly the selected IDs', async () => {
  const run = app();
  await run(`(async () => {
    renderCollectionDetail(collection('A')); renderDownloadPlan(plan(['a', 'b'])); state.planSelection.add('b');
    loadDownloadPlan = async () => {};
    await runCleanup('pdf');
  })()`);
  assert.equal(run(`requests[0].url`), '/api/cleanup');
  assert.equal(run(`requests[0].body.entity_ids.join(',')`), 'b');
  assert.equal(run('state.planSelection.size'), 0);
});

test('same-collection refresh blocks actions until a current plan is ready', async () => {
  const run = app();
  await run(`(async () => {
    renderCollectionDetail(collection('A')); renderDownloadPlan(plan(['a'])); state.planSelection.add('a');
    renderCollectionDetail(collection('A'));
    await runCleanup('all');
  })()`);
  assert.equal(run('requests.length'), 0);
  assert.match(run('errors[0]'), /范围/);
});

test('older refresh of the same collection cannot overwrite the newer plan', async () => {
  const run = app();
  await run(`(async () => {
    renderCollectionDetail(collection('A'));
    var resolveOld;
    api = () => new Promise(resolve => { resolveOld = resolve; });
    var oldRequest = loadDownloadPlan();
    api = async () => plan(['new']);
    await loadDownloadPlan();
    resolveOld(plan(['old']));
    await oldRequest;
  })()`);
  assert.match(run(`$('#download-plan').innerHTML`), /data-entity="new"/);
  assert.doesNotMatch(run(`$('#download-plan').innerHTML`), /data-entity="old"/);
});

test('finishing an old cleanup does not clear the new collection selection', async () => {
  const run = app();
  await run(`(async () => {
    renderCollectionDetail(collection('A')); renderDownloadPlan(plan(['a'])); state.planSelection.add('a');
    var finish;
    postJson = () => new Promise(resolve => { finish = resolve; });
    var oldCleanup = runCleanup('pdf');
    renderCollectionDetail(collection('B')); renderDownloadPlan(plan(['b'])); state.planSelection.add('b');
    finish({}); await oldCleanup;
  })()`);
  assert.equal(run(`[...state.planSelection].join(',')`), 'b');
});

test('ingest confirmation locks only its card and leaves chat usable', async () => {
  const run = app();
  await run(`(async () => {
    chatState.current = {conversation_id: 'A'};
    askConfirm = async () => true;
    let finish; waitTask = () => new Promise(resolve => { finish = resolve; });
    postJson = async () => { requests.push({url: 'ingest'}); return {task_id: 't1'}; }; api = async () => ({messages: []}); loadChats = async () => {}; renderChatMessages = () => {};
    const card = {dataset: {}, disabled: false, textContent: '确认处理'};
    const pending = confirmIngest('paper', card);
    const duplicate = confirmIngest('paper', card);
    if ($('#chat-input').disabled || $('#chat-send').disabled) throw new Error('chat locked');
    if (!card.disabled || card.dataset.submitting !== '1') throw new Error('card not locked');
    await new Promise(resolve => setTimeout(resolve, 0));
    finish({status: 'done'}); await pending; await duplicate;
  })()`);
  assert.equal(run('requests.length'), 1);
});

test('old chat ingest completion does not overwrite the active conversation', async () => {
  const run = app();
  await run(`(async () => {
    chatState.current = {conversation_id: 'A'}; askConfirm = async () => true;
    let finish; waitTask = () => new Promise(resolve => { finish = resolve; });
    postJson = async () => ({task_id: 't1'}); api = async () => { throw new Error('old chat fetched'); }; loadChats = async () => {};
    const pending = confirmIngest('paper', {dataset: {}, disabled: false, textContent: ''});
    await new Promise(resolve => setTimeout(resolve, 0));
    chatState.current = {conversation_id: 'B'}; finish({status: 'done'}); await pending;
  })()`);
  assert.equal(run('errors.length'), 0);
});

test('new assistant response hydrates confirmation cards immediately', () => {
  const run = app();
  run(`
    var hydration = [];
    messageHtml = () => '<div>draft</div>';
    renderMathInElementIfPresent = () => {};
    scrollChat = () => {};
    hydrateIngestCards = () => hydration.push('ingest');
    hydrateOrganizationCards = () => hydration.push('organization');
    replacePending({}, 'query');
  `);
  assert.equal(run('hydration.join(",")'), 'ingest,organization');
});

test('restored applied organization card has saved result and no submit button', () => {
  const run = app();
  const html = run(`organizationMatrixHtml({operation:'assign',status:'applied',scope:['p1','p2'],result:{added:1,already_present:0,details:[{entity_id:'p1',status:'added'}]}})`);
  assert.match(html, /已完成/);
  assert.match(html, /实际新增 1/);
  assert.doesNotMatch(html, /organization-apply/);
});

test('catalog selection bar stays hidden outside the catalog', () => {
  const run = app();
  run(`var hidden; var activeCatalog = false; state.selection.add('p1');
    $('#selection-bar').classList.toggle = (name, value) => hidden = value;
    $('#papers-view').classList.contains = () => activeCatalog;
    $('#library-view').classList.contains = () => true;
    updateSelectionBar();`);
  assert.equal(run('hidden'), true);
  run('activeCatalog = true; updateSelectionBar();');
  assert.equal(run('hidden'), false);
});

test('a stale picker response cannot replace the newly chosen target', async () => {
  const run = app();
  await run(`(async () => {
    var resolvePlan;
    postJson = () => new Promise(resolve => resolvePlan = resolve);
    pickerState.request = 1; pickerState.ids = ['p1'];
    var pending = preparePickerDraft();
    pickerState.request = 2; pickerState.run = null;
    resolvePlan({run_id:'old'}); await pending;
  })()`);
  assert.equal(run('pickerState.run'), null);
});

test('translation labels do not contain the picker controls', () => {
  const html = fs.readFileSync(path.join(__dirname, '../../frontend/index.html'), 'utf8');
  for (const label of html.matchAll(/<label\b([^>]*)>([\s\S]*?)<\/label>/g)) {
    if (/id="picker-(existing|new-name)"/.test(label[2])) assert.doesNotMatch(label[1], /data-i18n-en/);
  }
});

function languageLayer(nodes = []) {
  const context = vm.createContext({window:{}, document:{readyState:'loading',addEventListener(){},querySelectorAll(){return nodes;}},localStorage:{getItem(){return 'zh';}}});
  const source = fs.readFileSync(path.join(__dirname, '../../frontend/i18n.js'), 'utf8');
  vm.runInContext(source.replace('window.I18N = {','window.I18N = { translateTextNode,'),context);
  return context.window.I18N;
}

test('language switching preserves a dynamically loaded release value', () => {
  const node = {textContent:'加载中…',hasAttribute(key){return key==='data-i18n-en';},getAttribute(){return 'Loading…';}};
  const i18n = languageLayer([node]);
  i18n.applyStatic('zh'); node.textContent='qa'; i18n.applyStatic('en'); i18n.applyStatic('zh');
  assert.equal(node.textContent,'qa');
});

test('reverse translation restores only text actually translated by this layer', () => {
  const i18n = languageLayer();
  const title = {nodeValue:'Fixture Paper 3'};
  i18n.translateTextNode(title,false);
  assert.equal(title.nodeValue,'Fixture Paper 3');
  const chrome = {nodeValue:'查看摘要'};
  i18n.translateTextNode(chrome,true); assert.equal(chrome.nodeValue,'View abstract');
  i18n.translateTextNode(chrome,false); assert.equal(chrome.nodeValue,'查看摘要');
});

test('research navigation activates its real view container', () => {
  const run = app();
  assert.equal(run('VIEW_GROUPS.research.join()'), 'research-view');
});
