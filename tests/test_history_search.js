/* Run with node --test tests/test_history_search.js. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {matches} = require('../src/web/static/js/history.js');
const entry = {query:'ما معنى التَّوحيد؟', result:{localized_text:'Explanation of a concept.',
  versions:[{text:'A second language version', scope_notice:'A limited scope', explanation_segments:[{text:'Useful detail'}]}]}};
test('Arabic search ignores diacritics, letter variants and partial words', () => {
  assert.ok(matches(entry, 'توحيد'));
  assert.ok(matches(entry, 'معني'));
  assert.ok(matches(entry, 'توح'));
});
test('search covers answers, versions, scope and segments with spelling tolerance', () => {
  for (const word of ['CONCEPT', 'concpt', 'conxept', 'concepts', 'second', 'limited', 'detail']) assert.ok(matches(entry, word), word);
  assert.equal(matches(entry, 'unrelated'), false);
});
test('multiple search words include entries matching any one word', () => {
  assert.ok(matches(entry, 'unrelated concept'));
  assert.ok(matches(entry, ''));
  assert.equal(matches(entry, 'unrelated elephant'), false);
});
test('unfinished requests and literal markup are safe searchable data', () => {
  assert.ok(matches({query:'<img onerror=alert(1)>', error:'Connection interrupted'}, 'connection'));
  assert.equal(matches({query:'Pending'}, 'concept'), false);
});

const fs = require('node:fs');
const vm = require('node:vm');
function browserHarness(records = new Map(), storageFails = false, interfaceLang = 'en') {
  const nodes = new Map();
  function node() {
    return {children:[], hidden:false, style:{}, value:'', options:[{},{}],
      setAttribute(){}, append(...children){this.children.push(...children);},
      replaceChildren(...children){this.children = children;}, focus(){}, dispatchEvent(){},
      showModal(){this.open = true;}, close(){this.open = false; this.onclose?.();}};
  }
  function get(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); }
  get('history-sort').value = 'newest';
  const indexedDB = {open() {
    const req = {};
    setImmediate(() => {
      if (storageFails) {req.error = Error('Storage denied'); req.onerror(); return;}
      req.result = {createObjectStore(){}, close(){}, transaction() {
        const tx = {objectStore() {return {
          getAll(){return operation(() => [...records.values()]);},
          get(id){return operation(() => records.get(id));},
          put(entry){return operation(() => {records.set(entry.id, structuredClone(entry));});},
          delete(id){return operation(() => records.delete(id));},
          clear(){return operation(() => records.clear());},
        };}};
        function operation(work) {const result = {}; setImmediate(() => {result.result = work(); tx.oncomplete();}); return result;}
        return tx;
      }};
      req.onsuccess();
    });
    return req;
  }};
  const rendered = [];
  const passages = [];
  const window = {addEventListener(){}, getSelection:() => null, bayanQuestions:{isActive:() => false, activeHistoryId:() => null}};
  const context = vm.createContext({window, indexedDB, crypto:require('node:crypto').webcrypto,
    QUERY_EXECUTION:JSON.parse(fs.readFileSync('configs/query_execution.json')), lang:interfaceLang,
    document:{getElementById:get, createElement:node, querySelector:() => get('hero')}, confirm:() => true,
    Event:class Event {}, OUTPUT_MESSAGES:{en:{name:'English'}, ar:{name:'العربية'}},
    showPublishedPassages(...args){passages.push(args);},
    clearAnswer(){}, showErr(){}, render(result){rendered.push(result);}});
  vm.runInContext(fs.readFileSync('src/web/static/js/history.js', 'utf8'), context);
  return {api:window.bayanHistory, nodes, records, rendered, passages, get};
}
test('full answers and settings persist across browser sessions, without bearer credentials', async () => {
  const first = browserHarness();
  const request = {query:'Question', target_language:'en', cultural_context:'western', share_response:false};
  await first.api.start('history-id', request);
  await first.api.complete('history-id', {query:'Question', versions:[{text:'Full answer', source_urls:['https://example.test']}], answer_status:'PARTIAL'}, null);
  const second = browserHarness(first.records);
  await second.api.refresh();
  assert.equal(second.records.get('history-id').result.versions[0].text, 'Full answer');
  assert.equal(second.records.get('history-id').request.cultural_context, 'western');
  assert.ok(second.records.get('history-id').completedAt >= second.records.get('history-id').createdAt);
  assert.equal(second.records.get('history-id').token, undefined);
});
test('deleting history does not recreate it when an in-flight question finishes', async () => {
  const h = browserHarness();
  await h.api.start('id', {query:'Pending'});
  await h.nodes.get('history-clear').onclick();
  await h.api.complete('id', {query:'Pending', localized_text:'Complete'}, null);
  assert.equal(h.records.size, 0);
});
test('storage denial reports a warning without throwing or blocking questions', async () => {
  const h = browserHarness(new Map(), true);
  await h.api.start('id', {query:'Question'});
  await h.api.complete('id', {query:'Question'}, null);
  assert.equal(h.nodes.get('history-storage-warning').hidden, false);
  assert.match(h.nodes.get('history-storage-warning').textContent, /could not be saved/);
});
test('history orders questions both ways and removes only the selected entry', async () => {
  const records = new Map([
    ['older', {id:'older', query:'Older question', createdAt:1000, result:{localized_text:'A'}}],
    ['newer', {id:'newer', query:'Newer question', createdAt:2000, result:{localized_text:'B'}}],
  ]);
  const h = browserHarness(records);
  await h.api.refresh();
  const cards = () => h.nodes.get('history-list').children.filter(n => n.className.startsWith('history-card'));
  const questions = () => cards().map(c => c.children.find(n => n.className === 'history-question').textContent);
  assert.deepEqual(questions(), ['Newer question', 'Older question']);
  h.nodes.get('history-sort').value = 'oldest';
  h.nodes.get('history-sort').onchange();
  assert.deepEqual(questions(), ['Older question', 'Newer question']);
  await cards()[0].children.find(n => n.className === 'history-actions').children[1].onclick();
  assert.equal(records.has('older'), false);
  assert.equal(records.has('newer'), true);
});

test('switching tabs keeps the hero visible and Arabic UI dates use Western digits', async () => {
  const h = browserHarness(new Map([['id', {id:'id', query:'Question', createdAt:1700000000000, result:{localized_text:'Answer'}}]]), false, 'ar');
  await h.api.refresh();
  h.api.tab('history');
  assert.equal(h.nodes.get('hero')?.hidden || false, false);
  h.api.tab('ask');
  assert.equal(h.nodes.get('hero')?.hidden || false, false);
  const card = h.nodes.get('history-list').children.find(n => n.className.startsWith('history-card'));
  const time = card.children[0].children[0].textContent;
  assert.match(time, /[0-9]/);
  assert.doesNotMatch(time, /[٠-٩۰-۹]/);
  assert.equal(h.nodes.has('history-count'), false);
  assert.equal(h.nodes.has('history-local'), false);
  assert.equal(h.nodes.get('history-search').dir, 'rtl');
  assert.equal(h.nodes.has('history-badge'), false);
});
test('clicking a card opens a popup without changing the search page and ignores nested actions', async () => {
  const result = {localized_text:'Saved answer'};
  const h = browserHarness(new Map([['id', {id:'id', query:'Question', createdAt:1700000000000, result}]]));
  await h.api.refresh();
  const card = h.nodes.get('history-list').children.find(n => n.className.startsWith('history-card'));
  h.api.tab('history');
  card.onclick({target:{closest:() => null}});
  assert.equal(h.nodes.get('history-dialog').open, true);
  assert.equal(h.nodes.get('history-panel').hidden, false);
  assert.equal(h.nodes.get('q')?.value || '', '');
  assert.equal(h.rendered.length, 0);
  assert.equal(h.passages[0][1].text, 'Saved answer');
  assert.equal(h.passages[0][4], 'history-source-');
  card.onclick({target:{closest:() => ({tagName:'BUTTON'})}});
  assert.equal(h.passages.length, 1);
});

test('popup transfer explicitly restores the saved query, settings and complete answer', async () => {
  const result = {target_language:'ar', versions:[{language:'ar',text:'Arabic answer'},{language:'en',text:'English answer'}]};
  const h = browserHarness(new Map([['id', {id:'id',query:'Saved question',createdAt:1700000000000,result,
    request:{target_language:'ar',cultural_context:'western'}}]]));
  await h.api.refresh(); h.api.tab('history');
  const card = h.nodes.get('history-list').children.find(n => n.className.startsWith('history-card'));
  card.children.find(n => n.className === 'history-actions').children[0].onclick();
  assert.equal(h.passages.length, 2);
  h.nodes.get('history-dialog-to-search').onclick();
  assert.equal(h.nodes.get('history-dialog').open, false);
  assert.equal(h.nodes.get('history-dialog-answer').children.length, 0);
  assert.equal(h.nodes.get('history-panel').hidden, true);
  assert.equal(h.nodes.get('q').value, 'Saved question');
  assert.equal(h.nodes.get('target-lang').value, 'ar');
  assert.equal(h.nodes.get('cultural-context').value, 'western');
  assert.equal(h.rendered[0], result);
});
test('closing popup leaves the search draft and history view intact', async () => {
  const h = browserHarness(new Map([['id', {id:'id',query:'Saved',createdAt:1000,result:{localized_text:'Answer'}}]]));
  await h.api.refresh(); h.api.tab('history');
  h.get('q').value = 'Unsent draft';
  const card = h.nodes.get('history-list').children.find(n => n.className.startsWith('history-card'));
  card.onclick({target:{closest:() => null}});
  h.nodes.get('history-dialog-close').onclick();
  assert.equal(h.nodes.get('history-dialog').open, false);
  assert.equal(h.nodes.get('history-panel').hidden, false);
  assert.equal(h.rendered.length, 0);
  assert.equal(h.nodes.get('history-search').dir, 'ltr');
  assert.equal(h.nodes.get('q').value, 'Unsent draft');
});
