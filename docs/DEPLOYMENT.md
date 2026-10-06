# الاستضافة المجانية من GitHub

## العربية

**الموقع المباشر: https://bayanai.onrender.com/**

تم التحقق يوم 6 أكتوبر 2026 من الصفحة الرئيسية والاتصال بـGemini. نجحت إجابة عربية للمبتدئ بإحالتين إلى موسوعة الأحاديث، واستغرقت 133.9 ثانية. نجح استرجاع آية برقمها، وفتح رابطا الحديث برمز HTTP 200. هذا تحقق من الاستضافة، وليس إعادة تشغيل لجميع اختبارات التقييم.

بَيان يحتاج خادم Python. لا تكفي GitHub Pages لتشغيل FastAPI. يحدد `render.yaml` خدمة ويب على خطة **Free** مرتبطة بفرع `main`.

### خطوات النشر

1. ارفع الإصدار النظيف إلى [المستودع العام](https://github.com/AbdulhamidObeid/BayanAI).
2. افتح [Render](https://dashboard.render.com/)، ثم **New → Blueprint** واربط المستودع، أو اختر Web Service باستخدام رابطه العام.
3. اختر `main` وPython 3 وخطة **Free**.
4. أمر البناء:

```bash
pip install -r requirements.txt
```

5. أمر التشغيل:

```bash
python -m uvicorn src.web.app:app --host 0.0.0.0 --port $PORT --workers 1
```

6. أضف في **Environment**:

| الاسم | القيمة |
|---|---|
| `PYTHON_VERSION` | `3.12.12` |
| `BAYAN_PRIVATE_STORE` | `/tmp/bayan-private` |
| `GEMINI_API_KEY` | مفتاحك الخاص من Google AI Studio، كقيمة سرية |

لا تضع المفتاح في GitHub أو YAML أو الصور. اترك بيانات إدارة المصطلحات فارغة إن لم تحتاجها.

7. اضغط النشر، وانتظر **Live**، ثم انسخ الرابط الفعلي `https://…onrender.com`.
8. افتح الرابط في متصفح جديد، وتحقق من اتصال API وإجابة عربية والتوطين والإخراج الإنجليزي وروابط المصادر.
9. أضف الرابط المتحقق منه إلى المستودع وبوابة المسابقة.

### ما يجب معرفته

- استضافة الخادم مجانية ضمن حدود الخطة؛ استهلاك Gemini يخضع لحصة وفوترة مشروع Google.
- تتوقف الخدمة المجانية بعد 15 دقيقة دون زيارات، ويحتاج أول فتح إلى وقت لتشغيلها مجدداً.
- ملفات الخادم مؤقتة؛ إعادة التشغيل أو النشر أو التوقف تفقد الطوابير والذاكرة المؤقتة ومفاتيح توقيع المشاركة. يبقى سجل المتصفح في متصفحه.
- لا تُرفع قواعد المصادر الخاصة إلى GitHub؛ تجلب النسخة الجديدة الأدلة من الناشرين، وقد تختلف سرعتها عن العرض ذي المصادر المخزنة.
- استخدم عملية واحدة؛ يشغل التطبيق العمال في الخلفية. ذاكرة الخدمة المجانية 512 ميغابايت، وتحتاج متابعة أثناء جلب المحتوى.
- مسار فحص الاستضافة `/` لا يستهلك طلبات Gemini. المسار `/api/health` يفحص اتصال المزود.
- عند تعديل التوثيق فقط، يمكن وضع `[skip render]` في رسالة Git لتجنب إعادة نشر الخادم تلقائياً.

[توثيق Render الرسمي](https://render.com/docs/web-services) · [حدود الخطة المجانية](https://render.com/docs/free) · [إعداد Blueprint](https://render.com/docs/blueprint-spec).

---

## English

Bayan requires a Python backend. GitHub Pages serves static files and cannot run this FastAPI application. The provided `render.yaml` selects a **Free** Render web service, connected to `main`.

## Live website

**https://bayanai.onrender.com/**

Verified on 6 October 2026: homepage HTTP 200, Gemini connected, a real beginner Arabic question answered with two HadeethEnc citations (133.9 seconds), and an exact Quran reference returned a verified answer. Both hadith publisher links returned HTTP 200. These are deployment smoke checks, not a full live benchmark.

## Deploy

1. Push this clean project to the public [BayanAI repository](https://github.com/AbdulhamidObeid/BayanAI).
2. Sign in to [Render](https://dashboard.render.com/), select **New → Blueprint**, and connect the repository. Alternatively create a Web Service using the public repository URL.
3. Use `main`, Python 3, and **Free**. Build: `pip install -r requirements.txt`.
4. Start: `python -m uvicorn src.web.app:app --host 0.0.0.0 --port $PORT --workers 1`.
5. Set `PYTHON_VERSION=3.12.12`, `BAYAN_PRIVATE_STORE=/tmp/bayan-private`, and **secret** `GEMINI_API_KEY` in Environment. Do not store the key in the repository, YAML or screenshots. Leave optional admin credentials blank unless needed.
6. Deploy. Copy the actual `https://…onrender.com` URL after Render reports **Live**.
7. Open it in a fresh browser. Verify the homepage, API connection, an Arabic question, a regional comparison and an English-output question. Verify displayed citations open the correct publisher passages.
8. Add the confirmed link to GitHub’s About → Website and the competition submission. Until checks pass, do not call the deployment verified.

## Important operation details

- Server hosting is free; Google Gemini API usage may incur charges according to the API project’s billing and quota.
- Free services sleep after 15 minutes of inactivity. Opening the service wakes it; allow for a cold start.
- The filesystem is ephemeral. Redeployments/restarts remove server queues, caches and receipt signing keys. Previously issued sharing receipts cannot survive loss of that key. Browser-local History remains in its original browser.
- Fresh deployment has no private local source cache; evidence is fetched and indexed again. First-generation performance may differ from the recorded warm-source demo.
- Use one process; background question workers are started by the application. A free instance has 512 MB RAM; monitor memory while fetching publisher content.
- The homepage health path avoids consuming Gemini quota during Render’s liveness probes. `/api/health` checks provider connectivity.
