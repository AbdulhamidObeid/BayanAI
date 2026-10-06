/* Personal browser history. Archived responses are snapshots, never generation
   authority. New questions still use the verified server pipeline and cache.
   Source law: docs/guides/10_immutability_constitution_and_source_lock.md. */
(() => {
  const normalize = value => String(value || '').normalize('NFKD').toLowerCase()
    .replace(/\p{M}/gu, '').replace(/ـ/g, '').replace(/[أإآٱ]/g, 'ا')
    .replace(/ى/g, 'ي').replace(/[^\p{L}\p{N}\s]/gu, ' ').replace(/\s+/g, ' ').trim();
  function nearWord(a, b) {
    if (a.length < 4 || Math.abs(a.length - b.length) > 1) return false;
    let i = 0, j = 0, edits = 0;
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) { i++; j++; continue; }
      if (++edits > 1) return false;
      if (a.length >= b.length) i++;
      if (b.length >= a.length) j++;
    }
    return edits + (i < a.length || j < b.length ? 1 : 0) <= 1;
  }
  function answerText(result) {
    return [result?.localized_text, ...(result?.versions || []).flatMap(v =>
      [v.text, v.scope_notice, ...(v.explanation_segments || []).map(s => s.text)]), result?.error]
      .filter(Boolean).filter((text, index, all) => all.indexOf(text) === index).join('\n');
  }
  function matches(entry, search) {
    const needles = normalize(search).split(' ').filter(Boolean);
    const text = normalize(entry.query + ' ' + answerText(entry.result) + ' ' + (entry.error || ''));
    const words = text.split(' ');
    return needles.length === 0 || needles.some(n => text.includes(n) || words.some(w => nearWord(n, w)));
  }
  // Export the pure search contract for deterministic offline regression checks.
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {normalize, matches, answerText};
    return;
  }
  const el = id => document.getElementById(id);
  const labels = () => QUERY_EXECUTION.history.messages[lang] || QUERY_EXECUTION.history.messages.en;
  const locale = () => lang === 'ar' ? 'ar-SA-u-ca-gregory-nu-latn' : 'en-GB-u-nu-latn';
  let database;
  let rows = [];
  let failed = false;
  let viewing = false;
  let selectedEntry = null;
  function warning() {
    failed = true;
    el('history-storage-warning').hidden = false;
    el('history-storage-warning').textContent = labels().unavailable;
  }
  function db() {
    if (!database) database = new Promise((resolve, reject) => {
      const request = indexedDB.open('bayan-personal-history', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('entries', {keyPath: 'id'});
      request.onsuccess = () => {
        request.result.onversionchange = () => { request.result.close(); database = null; };
        resolve(request.result);
      };
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error('History storage blocked'));
    }).catch(error => { database = null; throw error; });
    return database;
  }
  async function transaction(mode, operation) {
    const connection = await db();
    return new Promise((resolve, reject) => {
      const tx = connection.transaction('entries', mode);
      const request = operation(tx.objectStore('entries'));
      tx.oncomplete = () => resolve(request?.result);
      tx.onabort = tx.onerror = () => reject(tx.error || new Error('History write failed'));
    });
  }
  async function refresh() {
    try { rows = await transaction('readonly', store => store.getAll()); }
    catch (_) { warning(); }
    paint();
  }
  function element(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function tab(name, focus = false) {
    const history = name === 'history';
    el('history-panel').hidden = !history;
    el('ask-panel').hidden = history;
    ['ask', 'history'].forEach(key => {
      el(key + '-tab').setAttribute('aria-selected', String(key === name));
      el(key + '-tab').tabIndex = key === name ? 0 : -1;
    });
    if (focus) el(name + '-tab').focus();
    if (history) refresh();
  }
  function open(entry) {
    if (window.bayanQuestions?.isActive()) return;
    selectedEntry = entry;
    const dialog = el('history-dialog');
    dialog.dir = lang === 'ar' ? 'rtl' : 'ltr';
    el('history-dialog-question').textContent = entry.query;
    el('history-dialog-date').textContent = labels().saved + ' · ' + new Date(entry.createdAt).toLocaleString(locale());
    const body = el('history-dialog-answer'); body.replaceChildren();
    if (entry.error) body.append(element('p', 'history-warning', entry.error));
    else {
      const result = entry.result;
      const versions = result.versions?.length ? result.versions : [{language:result.target_language || lang, text:result.localized_text || ''}];
      versions.forEach(version => {
        const heading = element('h3', 'sec-label', OUTPUT_MESSAGES[version.language]?.name || version.language.toUpperCase());
        const answer = element('div', 'answer-card');
        answer.lang = version.language;
        answer.dir = ['ar','ur'].includes(version.language) ? 'rtl' : 'ltr';
        showPublishedPassages(answer, version, result.citations || [], result.source_cards?.[version.language] || [], 'history-source-');
        body.append(heading, answer);
      });
    }
    el('history-dialog-close').textContent = labels().close;
    el('history-dialog-to-search').textContent = labels().openOnSearch;
    dialog.showModal();
  }
  function openOnSearch(entry) {
    if (window.bayanQuestions?.isActive()) return;
    tab('ask');
    el('q').value = entry.query;
    if (entry.request) {
      ['target-lang', 'cultural-context'].forEach((id, index) => {
        el(id).value = entry.request[index === 0 ? 'target_language' : 'cultural_context'];
        el(id).dispatchEvent(new Event('change'));
      });
    }
    clearAnswer();
    if (entry.result && !entry.error) render(entry.result);
    else showErr(entry.error || labels().pending);
    const note = el('history-answer-note');
    note.replaceChildren(element('span', '', labels().saved + ' · ' + new Date(entry.createdAt).toLocaleString(locale())));
    const back = element('button', 'history-text-button', labels().back);
    back.onclick = () => tab('history');
    note.append(back); note.hidden = false;
    viewing = true;
  }
  function paint() {
    const t = labels();
    el('ask-tab').textContent = t.ask;
    ['title', 'subtitle', 'clear'].forEach(id => el('history-' + id).textContent = t[id]);
    el('history-search').dir = lang === 'ar' ? 'rtl' : 'ltr';
    el('history-search').placeholder = t.search;
    el('history-search').setAttribute('aria-label', t.search);
    el('history-sort').setAttribute('aria-label', t.newest + ' / ' + t.oldest);
    el('history-sort').options[0].textContent = t.newest;
    el('history-sort').options[1].textContent = t.oldest;
    el('history-clear').disabled = rows.length === 0;
    if (failed) el('history-storage-warning').textContent = t.unavailable;
    const search = el('history-search').value;
    const filtered = rows.filter(r => matches(r, search)).sort((a, b) =>
      (a.createdAt - b.createdAt || a.id.localeCompare(b.id)) * (el('history-sort').value === 'oldest' ? 1 : -1));
    const list = el('history-list'); list.replaceChildren();
    if (!filtered.length) {
      const empty = element('div', 'history-empty');
      empty.append(element('div', 'history-empty-icon', search ? '⌕' : '◷'),
        element('h3', '', search ? t.noMatches : t.empty), element('p', '', search ? t.noMatchesHint : t.emptyHint));
      list.append(empty); return;
    }
    let lastDay;
    const today = new Date().toDateString();
    const yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);
    filtered.forEach(entry => {
      const date = new Date(entry.createdAt), day = date.toDateString();
      if (day !== lastDay) {
        const title = day === today ? t.today : day === yesterday.toDateString() ? t.yesterday : date.toLocaleDateString(locale(), {year:'numeric', month:'long', day:'numeric'});
        list.append(element('h3', 'history-day', title)); lastDay = day;
      }
      const canOpen = Boolean(entry.result) && !window.bayanQuestions?.isActive();
      const card = element('article', 'history-card' + (canOpen ? ' is-openable' : ''));
      if (canOpen) {
        card.onclick = event => {
          if (!event.target.closest('button, a') && !window.getSelection()?.toString()) open(entry);
        };
      }
      const meta = element('div', 'history-meta');
      const time = element('time', '', date.toLocaleString(locale(), {day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'}));
      time.dateTime = date.toISOString();
      const status = entry.error ? t.error : entry.result ? t.completed : entry.id === window.bayanQuestions?.activeHistoryId() ? t.queued : t.interrupted;
      meta.append(time, element('span', 'history-language', (entry.request?.target_language || entry.result?.target_language || '').toUpperCase()),
        element('span', 'history-state' + (entry.error ? ' is-error' : ''), status));
      const question = element('h4', 'history-question', entry.query); question.dir = 'auto';
      const preview = element('p', 'history-preview', entry.error || entry.result?.localized_text || entry.result?.versions?.[0]?.text || t.pending); preview.dir = 'auto';
      const actions = element('div', 'history-actions');
      const show = element('button', 'history-open', t.open);
      show.disabled = !canOpen;
      show.onclick = event => { event?.stopPropagation(); open(entry); };
      const remove = element('button', 'history-text-button', t.delete);
      remove.setAttribute('aria-label', t.delete + ': ' + entry.query);
      remove.onclick = async event => {
        event?.stopPropagation();
        try { await transaction('readwrite', store => store.delete(entry.id)); await refresh(); }
        catch (_) { warning(); }
      };
      actions.append(show, remove);
      if (entry.result?.answer_cache_reused) actions.append(element('span', 'history-reused', t.reused));
      card.append(meta, question, preview, actions); list.append(card);
    });
  }
  async function start(id, request) {
    try {
      await transaction('readwrite', store => store.put({id, query:request.query, request, createdAt:Date.now()}));
      await refresh();
    } catch (_) { warning(); }
  }
  async function complete(id, result, error) {
    try {
      let entry = id ? await transaction('readonly', store => store.get(id)) : null;
      // A deleted entry stays deleted. Older pending sessions can still be archived.
      if (id && !entry) return;
      entry ||= {id:crypto.randomUUID(), query:result?.query || el('q').value, createdAt:Date.now()};
      await transaction('readwrite', store => store.put({...entry, result, error, completedAt:Date.now()}));
      await refresh();
    } catch (_) { warning(); }
  }
  window.bayanHistory = {start, complete, refresh, tab,
    resetView() { el('history-answer-note').hidden = true; viewing = false; tab('ask'); },
  };
  ['ask', 'history'].forEach(key => {
    el(key + '-tab').onclick = () => tab(key);
    el(key + '-tab').onkeydown = event => {
      if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) {
        event.preventDefault();
        tab(event.key === 'Home' ? 'ask' : event.key === 'End' ? 'history' : key === 'ask' ? 'history' : 'ask', true);
      }
    };
  });
  el('history-dialog-close').onclick = () => el('history-dialog').close();
  el('history-dialog').onclick = event => {
    if (event.target !== el('history-dialog')) return;
    const rect = el('history-dialog').getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) el('history-dialog').close();
  };
  el('history-dialog').onclose = () => { selectedEntry = null; el('history-dialog-answer').replaceChildren(); };
  el('history-dialog-to-search').onclick = () => {
    if (!selectedEntry || window.bayanQuestions?.isActive()) return;
    const entry = selectedEntry;
    el('history-dialog').close();
    openOnSearch(entry);
  };
  el('history-search').oninput = paint;
  el('history-sort').onchange = paint;
  el('history-clear').onclick = async () => {
    if (!confirm(labels().confirmClear)) return;
    try {
      await transaction('readwrite', store => store.clear());
      if (viewing) { clearAnswer(); el('result').style.display = 'none'; el('history-answer-note').hidden = true; viewing = false; }
      await refresh();
    } catch (_) { warning(); }
  };
  window.addEventListener('focus', refresh);
  refresh();
})();
