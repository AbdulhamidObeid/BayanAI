/* Recover the same private question across dropped connections and page reloads.
   Also drives the animated progress steps and dhikr rotation during loading.      */
(() => {
  const storageKey = 'bayan_pending_question';
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  let active = false;
  let activeHistoryId = null;
  const labels = () => QUERY_EXECUTION.messages[lang] || QUERY_EXECUTION.messages.en;
  const progress = state => {
    if (state === 'running') {
      document.getElementById('question-progress').textContent = '';
      return;
    }
    document.getElementById('question-progress').textContent = labels()[state] || '';
  };
  const save = pending => {
    try {
      if (pending) sessionStorage.setItem(storageKey, JSON.stringify(pending));
      else sessionStorage.removeItem(storageKey);
    } catch (_) { /* Private browsing may disable storage; the live request still works. */ }
  };
  async function request(pending, method) {
    const controller = new AbortController();
    // Only the connection is retried. Aborting it never cancels the server job.
    const timer = setTimeout(() => controller.abort(), QUERY_EXECUTION.transport_timeout_ms);
    try {
      const response = await fetch('/api/query-job', {
        method, signal: controller.signal, cache: 'no-store',
        headers: {'Content-Type': 'application/json', 'X-Query-ID': pending.token},
        ...(method === 'POST' ? {body: JSON.stringify(pending.body)} : {}),
      });
      return {response, data: await response.json()};
    } finally { clearTimeout(timer); }
  }

  /* ── Animated pipeline steps shown while loading ──────────────── */
  const PIPELINE_STEPS = {
    ar: [
      { label: 'فحص السؤال',                   minMs: 0     },
      { label: 'فهم السؤال وتصنيفه',            minMs: 2500  },
      { label: 'البحث واسترجاع المصادر',         minMs: 7000  },
      { label: 'تقييم الأدلة وانتقاء المصادر',   minMs: 16000 },
      { label: 'صياغة الشرح الموثق',             minMs: 26000 },
      { label: 'مراجعة الألفاظ والمصطلحات',       minMs: 40000 },
      { label: 'اعتماد الإجابة وإعداد العرض',     minMs: 55000 },
    ],
    en: [
      { label: 'Screening question',             minMs: 0     },
      { label: 'Classifying question',           minMs: 2500  },
      { label: 'Searching authenticated sources',minMs: 7000  },
      { label: 'Selecting evidence',             minMs: 16000 },
      { label: 'Composing verified answer',      minMs: 26000 },
      { label: 'Checking terminology',           minMs: 40000 },
      { label: 'Finalising & presenting',        minMs: 55000 },
    ],
  };

  const ADHKAR = {
    ar: [
      'سُبْحَانَ اللَّهِ وَبِحَمْدِهِ سُبْحَانَ اللَّهِ الْعَظِيمِ',
      'لا إِلَهَ إِلاَّ اللَّهُ وَحْدَهُ لا شَرِيكَ لَهُ، لَهُ الْمُلْكُ وَلَهُ الْحَمْدُ، وَهُوَ عَلَى كُلِّ شَيْءٍ قَدِيرٌ',
      'سُبْحَانَ اللَّهِ، وَالْحَمْدُ لِلَّهِ، وَلا إِلَهَ إِلاَّ اللَّهُ، وَاللَّهُ أَكْبَرُ',
      'اللَّهُمَّ صَلِّ وَسَلِّمْ عَلَى نَبِيِّنَا مُحَمَّدٍ',
      'أَسْتَغْفِرُ اللَّهَ الْعَظِيمَ وَأَتُوبُ إِلَيْهِ',
      'حَسْبِيَ اللَّهُ لا إِلَهَ إِلاَّ هُوَ عَلَيْهِ تَوَكَّلْتُ وَهُوَ رَبُّ الْعَرْشِ الْعَظِيمِ',
      'اللَّهُمَّ إِنِّي أَسْأَلُكَ الْعِلْمَ النَّافِعَ وَالرِّزْقَ الطَّيِّبَ وَالْعَمَلَ الْمُتَقَبَّلَ',
      'سُبْحَانَكَ اللَّهُمَّ وَبِحَمْدِكَ، أَشْهَدُ أَنْ لا إِلَهَ إِلاَّ أَنْتَ، أَسْتَغْفِرُكَ وَأَتُوبُ إِلَيْكَ',
      'رَبِّ زِدْنِي عِلْمًا',
      'بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ',
    ],
    en: [
      'SubḥānAllāh — Glory be to Allah',
      'Allāhu Akbar — Allah is the Greatest',
      'Al-ḥamdu lillāh — All praise is for Allah',
      'Lā ilāha illAllāh — There is no god but Allah',
      'AstaghfirAllāh — I seek Allah\'s forgiveness',
      'Ḥasbiyallāhu lā ilāha illā hū — Allah is sufficient for me',
      'Allāhumma ṣalli \'alā Muḥammad — Prayers upon the Prophet ﷺ',
      'Rabb zidnī \'ilmā — My Lord, increase me in knowledge',
      'SubḥānAllāh wa biḥamdih, SubḥānAllāhil \'Aẓīm',
    ],
  };

  let stepTimer = null;
  let dhikrTimer = null;
  let dhikrIndex = 0;
  let loadStart = 0;

  function stepLabel(l) { return PIPELINE_STEPS[l] || PIPELINE_STEPS.en; }
  function dhikrList(l) { return ADHKAR[l] || ADHKAR.en; }

  function startLoadingAnimations() {
    loadStart = Date.now();
    const progressEl = document.getElementById('question-progress');
    const dhikrEl   = document.getElementById('loading-dhikr');
    const stepEl    = document.getElementById('loading-step');
    const steps = stepLabel(lang);
    const adhkar = dhikrList(lang);
    dhikrIndex = Math.floor(Math.random() * adhkar.length);

    // ── Step label: advance through pipeline steps based on elapsed time ──
    let stepIndex = 0;
    function advanceStep() {
      const elapsed = Date.now() - loadStart;
      // Find the furthest step whose minMs has been reached
      let next = 0;
      for (let i = 0; i < steps.length; i++) {
        if (elapsed >= steps[i].minMs) next = i;
      }
      if (next !== stepIndex) {
        stepIndex = next;
        if (stepEl) {
          stepEl.style.opacity = '0';
          setTimeout(() => {
            stepEl.textContent = steps[stepIndex].label;
            stepEl.style.opacity = '1';
          }, 200);
        }
      }
      stepTimer = setTimeout(advanceStep, 800);
    }
    if (stepEl) {
      stepEl.textContent = steps[0].label;
      stepEl.style.opacity = '1';
      stepTimer = setTimeout(advanceStep, 800);
    }

    // ── Dhikr rotation: change every 5 seconds ──
    function rotateDhikr() {
      dhikrIndex = (dhikrIndex + 1) % adhkar.length;
      if (dhikrEl) {
        dhikrEl.style.opacity = '0';
        setTimeout(() => {
          dhikrEl.textContent = adhkar[dhikrIndex];
          dhikrEl.style.opacity = '1';
        }, 400);
      }
      dhikrTimer = setTimeout(rotateDhikr, 5000);
    }
    if (dhikrEl) {
      dhikrEl.textContent = adhkar[dhikrIndex];
      dhikrEl.style.opacity = '1';
      dhikrTimer = setTimeout(rotateDhikr, 5000);
    }
  }

  function stopLoadingAnimations() {
    clearTimeout(stepTimer);
    clearTimeout(dhikrTimer);
    stepTimer = null;
    dhikrTimer = null;
    const stepEl  = document.getElementById('loading-step');
    const dhikrEl = document.getElementById('loading-dhikr');
    if (stepEl)  { stepEl.textContent = '';  stepEl.style.opacity = '0'; }
    if (dhikrEl) { dhikrEl.textContent = ''; dhikrEl.style.opacity = '0'; }
  }

  async function follow(pending, historySaved) {
    if (active) return;
    active = true;
    activeHistoryId = pending.historyId;
    window.bayanHistory?.resetView();
    const btn = document.getElementById('btn-send');
    btn.disabled = true;
    document.getElementById('lbl-send').textContent = T[lang].sending;
    document.getElementById('loading').classList.add('on');
    document.getElementById('result').classList.remove('on');
    document.getElementById('result').style.display = 'none';
    document.getElementById('examples').style.display = 'none';
    clearAnswer();
    document.getElementById('question-progress').textContent = '';
    startLoadingAnimations();
    let failures = 0;
    try {
      while (true) {
        let response, data;
        try {
          ({response, data} = await request(pending, pending.accepted ? 'GET' : 'POST'));
        } catch (_) {
          progress('reconnecting');
          await pause(Math.min(QUERY_EXECUTION.retry_max_seconds,
            QUERY_EXECUTION.retry_initial_seconds * 2 ** Math.min(failures++, 10)) * 1000);
          continue;
        }
        if (response.status === 429 || response.status >= 500) {
          progress('reconnecting');
          await pause(Math.min(QUERY_EXECUTION.retry_max_seconds,
            QUERY_EXECUTION.retry_initial_seconds * 2 ** Math.min(failures++, 10)) * 1000);
          continue;
        }
        failures = 0;
        if (!response.ok) {
          save(null);
          const error = response.status === 404 ? labels().expired :
            (typeof data.detail === 'string' ? data.detail : T[lang].error);
          await historySaved;
          await window.bayanHistory?.complete(pending.historyId, null, error);
          showErr(error);
          return;
        }
        if (!pending.accepted) {
          pending = {token: pending.token, accepted: true, historyId: pending.historyId};
          save(pending); // Remove the browser's temporary question copy after acceptance.
        } else if (data.status === 'completed') {
          const error = data.status_code >= 400 ? (data.result.error || T[lang].error) : null;
          await historySaved;
          await window.bayanHistory?.complete(pending.historyId, data.result, error);
          if (error) showErr(error);
          else {
            document.getElementById('q').value = data.result.query || '';
            render(data.result);
          }
          save(null);
          return;
        } else progress(data.status);
        await pause(QUERY_EXECUTION.poll_seconds * 1000);
      }
    } finally {
      active = false;
      activeHistoryId = null;
      btn.disabled = false;
      document.getElementById('lbl-send').textContent = T[lang].send;
      document.getElementById('loading').classList.remove('on');
      document.getElementById('question-progress').textContent = '';
      stopLoadingAnimations();
      window.bayanHistory?.refresh();
    }
  }
  window.bayanQuestions = {
    isActive: () => active,
    activeHistoryId: () => activeHistoryId,
    async submit() {
      const query = document.getElementById('q').value.trim();
      if (active || !query) return;
      const token = Array.from(crypto.getRandomValues(new Uint8Array(32)),
        value => value.toString(16).padStart(2, '0')).join('');
      const pending = {token, historyId: crypto.randomUUID(), accepted: false, body: {
        query,
        target_language: document.getElementById('target-lang').value,
        cultural_context: document.getElementById('cultural-context').value,
        share_response: false,
      }};
      save(pending);
      // Save history alongside generation, without delaying server acceptance.
      const historySaved = window.bayanHistory?.start(pending.historyId, pending.body);
      return follow(pending, historySaved);
    },
  };
  try {
    const pending = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
    if (pending && /^[a-f0-9]{64}$/.test(pending.token)) follow(pending);
  } catch (_) { save(null); }
})();
