# بَيان · Bayan AI

**مصادر إسلامية موثقة، وشرح يناسب السائل.**

[العربية](#العربية) · [English](#english) · **[افتح بَيان](https://bayanai.onrender.com/)**

## العربية

بَيان تطبيق ويب يسترجع المصادر الإسلامية المعتمدة، ويحلل السؤال، ثم يقدم شرحاً مراجعاً يناسب مستوى السائل ولغته والجمهور الذي يختاره. تظهر نصوص المصادر المنشورة مستقلة عن شرح الذكاء الاصطناعي، مع إحالات وروابط مباشرة للتحقق.

![واجهة بَيان — إجابة حقيقية موثقة بالمصادر](docs/evidence/knowledge-beginner-website.jpg)

### ماذا يقدم بَيان؟

- **مستوى المعرفة:** شرح تأسيسي للمبتدئ، وتفصيل أعمق لمن لديه معرفة سابقة، وفق صياغة السؤال.
- **التوطين الثقافي:** يكيّف مدخل الشرح للجمهور المختار، مع الحفاظ على المعنى الموثق.
- **تعدد اللغات:** إجابة عربية؛ وإجابة إنجليزية مع نسخة عربية؛ ولغات الإخراج الأخرى المدعومة مع نسختين عربية وإنجليزية.
- **مراجعة الأدلة:** يسترجع المصادر، ويفحص صلتها بالسؤال وتغطيتها له، ويراجع الشرح والمصطلحات.
- **الفتوى الشخصية:** يحيل الحالات الفردية إلى المختصين.
- **السجل:** يحفظ الأسئلة والإجابات في المتصفح نفسه، مع البحث والحذف.

### ملفات المشروع والعرض

| الملف | الرابط |
|---|---|
| العرض التقديمي المعتمد | [BayanAI_Pitch.pdf](docs/pitch_deck/BayanAI_Pitch.pdf) |
| فيديو العرض المعتمد | [شاهد الفيديو في Google Drive](https://drive.google.com/drive/folders/1c98Vzm2Pys6o9yc2JVSXn22shmvREeG0) |
| أدلة من الموقع الفعلي | [الصور ومصفوفة الاختبارات](docs/evidence/Bayan_Website_Proof.zip) |
| شرح فئات الاختبار | [مصفوفة الاختبارات الاثني عشر](docs/evidence/TEST_MATRIX.md) |

في التشغيل المحلي المسجل يوم 6 أكتوبر 2026، اجتاز بَيان **12 من 12 فئة و41 من 41 تحققاً**، باستخدام 24 مدخلاً مختلفاً ودون إعادة استخدام إجابات سابقة. هذه نتيجة تشغيل محلي، وليست درجة تحكيم أو اعتماداً شرعياً مستقلاً أو ضماناً لكل سؤال.

### 1. تثبيت المشروع

ثبّت **Python 3.12** وGit، ثم نفّذ:

```bash
git clone https://github.com/AbdulhamidObeid/BayanAI.git
cd BayanAI
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

على Windows، أنشئ البيئة باستخدام `py -3.12 -m venv .venv`، ثم فعّلها في PowerShell باستخدام `.venv\Scripts\Activate.ps1`، وانسخ الملف باستخدام `Copy-Item .env.example .env`.

### 2. إعداد مفتاح Gemini 3.8 Flash

1. افتح [صفحة المفاتيح في Google AI Studio](https://aistudio.google.com/apikey).
2. سجّل الدخول، واختر أو أنشئ مشروع Google، ثم أنشئ مفتاح API.
3. تأكد من إتاحة [Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash) في مشروعك، وراجع الحصة والفوترة.
4. افتح ملف `.env` المحلي وضع مفتاحك:

```dotenv
GEMINI_API_KEY=your_private_key_here
```

معرّف النموذج المستخدم هو **`gemini-3.8-flash`**، وإعداداته في `configs/model_routing.json`. **لا ترفع المفتاح أو ملف `.env` إلى GitHub، ولا تضع المفتاح في كود المتصفح.** [تعليمات Google الرسمية](https://ai.google.dev/gemini-api/docs/api-key).

من يشغّل نسخته من GitHub يستخدم مفتاحه الخاص. الموقع المستضاف يستخدم مفتاح الخادم المضبوط في إعدادات الاستضافة؛ الواجهة الحالية لا تتضمن إدخال مفتاح لكل زائر. استضافة الخادم مجانية ضمن حدود خطة Render، واستخدام Gemini يخضع لحصة وفوترة مشروع Google.

### 3. تشغيل بَيان

```bash
python -m uvicorn src.web.app:app --host 127.0.0.1 --port 8000
```

افتح **http://127.0.0.1:8000/**. اختر لغة الإجابة والجمهور، واكتب السؤال ثم اضغط إرسال. مرجع API في `/docs`، وفحص الاتصال بمزود النموذج في `/api/health`.

يتطلب التشغيل اتصالاً بالإنترنت للوصول إلى Google والناشرين المعتمدين. عند التشغيل الأول تُجلب الأدلة ويُبنى فهرس خاص، وقد تحتاج الإجابة إلى وقت للاسترجاع والمراجعة. لا يتضمن المستودع قواعد البيانات الخاصة أو الإجابات المخزنة؛ النسخة الجديدة تسترجع الأدلة من مصادرها.

لتحديث نصوص المصادر الأصلية اختيارياً:

```bash
PYTHONPATH=. python scripts/build_source_index.py
```

### 4. تجربة نقاط القوة

| التجربة | السؤال والإعدادات |
|---|---|
| مستوى المبتدئ | `أنا لا أعرف شيئاً عن الإسلام، ما معنى التوحيد؟ اشرح لي ببساطة.` — العربية، عام |
| معرفة سابقة | `أعرف معنى التوحيد إجمالاً، فما الفرق بين توحيد الربوبية والألوهية والأسماء والصفات؟` — العربية، عام |
| التوطين حسب الجمهور | `ما معنى الدعوة إلى الإسلام، وكيف نفهمها مع مبدأ عدم الإكراه في الدين؟` — العربية، قارن الجمهور العام والغربي |
| تعدد اللغات | `لماذا يتجه المسلمون إلى الكعبة في الصلاة؟ هل يعبدون الكعبة؟` — اختر الإنجليزية للإجابة |

### 5. التحقق والاختبارات

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/test_answer_presentation.py tests/test_api_health.py tests/test_query_jobs.py tests/test_terminology_preserver.py -q
```

للتقييم الكامل، استخدم `python scripts/evaluate_acceptance.py --mode offline` أو `--mode live`. الوضع المباشر يستدعي المزود فعلياً ويستهلك الحصة. الاختبارات دون اتصال تفحص عمل البرمجيات، ولا تعني اعتماد صحة المحتوى شرعياً. نتائج أي تشغيل مسجل تخص نسخة الكود والإعدادات التي استُخدمت فيه.

### كيف يعمل النظام؟

السؤال واللغة والجمهور ← فحص الحالات الشخصية وتصنيف A/B/C/D ← تحليل مستوى المعرفة وأجزاء السؤال ← استرجاع المصادر المعتمدة ← تقييم الأدلة والتغطية ← كتابة شرح مستند إلى الأدلة ومراجعته منفصلاً ← فحص المصطلحات وإبقاء نصوص الناشر دون تغيير ← إجابة موثقة وسجل في المتصفح.

### دليل المجلدات

| المسار | الغرض |
|---|---|
| `src/web/` | واجهة الويب، مسارات FastAPI وسجل المتصفح |
| `src/core/` | تحليل السؤال، الاسترجاع، فحص الأدلة واستعادة الطلبات |
| `src/agents/` | توطين الشرح وحماية المصطلحات |
| `configs/` | سياسات المصادر والنماذج والمصطلحات والعرض |
| `scripts/` | التشغيل، فهرسة النصوص الأصلية والتقييم |
| `tests/` | اختبارات البرمجيات وعينات استجابات الناشرين |
| `docs/` | الأدلة والعرض والفيديو والصور |
| `render.yaml` | إعداد استضافة Python مجانية من فرع `main` |

### الخصوصية والتشغيل

يبقى سجل الأسئلة والإجابات في المتصفح حتى يحذفه المستخدم. يحتفظ الخادم بالطلبات لاستعادة المعالجة، وبالنتائج الخاصة لمدة ساعة بعد اكتمالها. تُنشأ المفاتيح الخاصة وملفات التخزين والفهرسة في `data/private/` أو المسار `BAYAN_PRIVATE_STORE`، ولا تُنشر في GitHub.

تتوقف خدمة Render المجانية بعد الخمول، وقد يتأخر أول فتح للموقع. ملفات الخادم مؤقتة وتُفقد عند إعادة التشغيل أو النشر؛ يشمل ذلك الطوابير والذاكرة المؤقتة ومفتاح توقيع إيصالات المشاركة. يبقى سجل المتصفح في متصفحه. يتطلب حفظ سجلات الخادم طويل الأجل تخزيناً دائماً.

بَيان أداة معلومات مبنية على المصادر وتستخدم الذكاء الاصطناعي، ويُحيل الفتاوى الفردية إلى المختصين. البصمة الرقمية تثبت سلامة المحتوى من التغيير، ولا تثبت صحته الشرعية.

### المصادر والتراخيص

الكود الأصلي مرخص وفق [MIT](LICENSE). تبقى حقوق نصوص الناشرين والخطوط والشعارات والمكونات الخارجية لأصحابها، ولكل منها شروطه. [المصادر والتراخيص](docs/SOURCES_AND_LICENSES.md) · [دليل GitHub](docs/GITHUB_GUIDE.md) · [دليل الاستضافة](docs/DEPLOYMENT.md).

إدارة المشروع: [عبد الحميد عبيد](https://github.com/AbdulhamidObeid).

---

## English

**Trusted Islamic sources. An explanation shaped for the reader.**

Bayan retrieves accredited Islamic sources, analyses the question, and produces a reviewed explanation suited to the reader’s knowledge, requested language and selected audience. Published source passages remain separate from AI commentary, with direct citations.

**[Open Bayan](https://bayanai.onrender.com/)**

## What Bayan does

- **Knowledge level:** introductory explanations for beginners and deeper explanations for readers with prior knowledge.
- **Regional localization:** adapts the explanation’s starting point to the selected audience while retaining the cited meaning.
- **Multilingual answers:** Arabic; English with Arabic companion; other supported output languages with Arabic and English companions.
- **Evidence review:** retrieves sources, checks relevance and coverage, reviews the explanation and preserves religious terminology.
- **Personal rulings:** refers individual fatwa questions to qualified specialists.
- **History:** saves answers in the current browser, with search and deletion.

## See the product

| Deliverable | File |
|---|---|
| Approved pitch deck | [BayanAI_Pitch.pdf](docs/pitch_deck/BayanAI_Pitch.pdf) |
| Approved demo video | [Watch on Google Drive](https://drive.google.com/drive/folders/1c98Vzm2Pys6o9yc2JVSXn22shmvREeG0) |
| Real website evidence | [Screenshots and matrix](docs/evidence/Bayan_Website_Proof.zip) |
| Test explanations | [12-category matrix](docs/evidence/TEST_MATRIX.md) |

The recorded local evaluation on 6 October 2026 passed **12/12 example categories and 41/41 checks**, using 24 unique inputs and previous-answer reuse disabled. This is a recorded local result, not an independent scholarly certification or a guarantee for every question.

## English setup

### 1. Install

Use **Python 3.12** and Git.

```bash
git clone https://github.com/AbdulhamidObeid/BayanAI.git
cd BayanAI
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

On Windows, use `py -3.12 -m venv .venv`, activate with `.venv\Scripts\Activate.ps1`, and copy with `Copy-Item .env.example .env`.

### 2. Get your Gemini API key

1. Open [Google AI Studio API keys](https://aistudio.google.com/apikey).
2. Sign in, select or create a Google project, and create an API key.
3. Confirm your project can use [Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash). Check quota and billing in AI Studio.
4. Open your local `.env` and set:

```dotenv
GEMINI_API_KEY=your_private_key_here
```

The configured model is **`gemini-3.8-flash`**, in `configs/model_routing.json`. Do not publish the key, put it in browser JavaScript or commit `.env`. See Google’s [API-key instructions](https://ai.google.dev/gemini-api/docs/api-key).

### 3. Run

```bash
python -m uvicorn src.web.app:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/**. The API reference is at `/docs`; the provider connection check is `/api/health`. Select the output language and audience, enter a question and click Send. The first run downloads publisher evidence and builds a private source index; allow time for retrieval and review. Internet access to Google and the approved publishers is required.

Optional: `PYTHONPATH=. python scripts/build_source_index.py` refreshes original publisher passages. Source caches and signing keys are generated locally, not supplied in GitHub. A new installation retrieves evidence afresh.

### 4. Try the three showcases

| Showcase | Question/settings |
|---|---|
| Beginner | `أنا لا أعرف شيئاً عن الإسلام، ما معنى التوحيد؟ اشرح لي ببساطة.` · Arabic · General |
| Prior knowledge | `أعرف معنى التوحيد إجمالاً، فما الفرق بين توحيد الربوبية والألوهية والأسماء والصفات؟` · Arabic · General |
| Region | `ما معنى الدعوة إلى الإسلام، وكيف نفهمها مع مبدأ عدم الإكراه في الدين؟` · Arabic · compare General and Western |
| Multilingual | `لماذا يتجه المسلمون إلى الكعبة في الصلاة؟ هل يعبدون الكعبة؟` · English output |

### 5. Verify

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/test_answer_presentation.py tests/test_api_health.py tests/test_query_jobs.py tests/test_terminology_preserver.py -q
```

For the complete evaluation, use `python scripts/evaluate_acceptance.py --mode offline` or `--mode live`. Live evaluation uses real provider calls and quota. Offline tests verify software contracts and do not certify religious correctness. Recorded results belong to their original code/configuration snapshot.

## How it works

```text
Question + requested language + chosen region
 → personal-ruling screen and A/B/C/D classification
 → reader-knowledge and question-part analysis
 → retrieval from approved publisher sources
 → evidence relevance and coverage assessment
 → source-bound explanation and separate review
 → terminology checks and unchanged published passages
 → cited answer + browser history
```

## Repository map

| Folder/file | Purpose |
|---|---|
| `src/web/` | FastAPI routes, Arabic/English UI, browser history |
| `src/core/` | Analysis, publisher retrieval, evidence checks, recoverable jobs |
| `src/agents/` | Localized explanation and terminology preservation |
| `configs/` | Source policy, model routes, terminology and presentation rules |
| `scripts/` | Launch, original-source indexing and evaluation commands |
| `tests/` | Software checks and publisher-response fixtures |
| `docs/` | Setup, deployment, sources, deck, video and evidence |
| `render.yaml` | Free Python web-service deployment from `main` |

## Privacy and operation

Browser History stays in that browser until deleted. The server temporarily retains jobs for recovery and completed private results for one hour. Private signing keys, source caches, provider quota records and opt-in sharing receipts are created in `data/private/`, or `BAYAN_PRIVATE_STORE`. Keep these private. Free Render storage is temporary: restarts/redeployments clear server-side records and caches; browser history remains in its browser. Use persistent hosting for long-term server records.

Bayan is an AI source-based information tool; individual religious rulings are referred to specialists. A content fingerprint verifies byte integrity, not religious truth. Published source texts retain their owners’ rights.

## License and credits

Original project code is licensed under [MIT](LICENSE). Publisher content, fonts and dependency licenses are separate; see [Sources and licenses](docs/SOURCES_AND_LICENSES.md). Maintained by [Abdulhamid Obeid](https://github.com/AbdulhamidObeid).
