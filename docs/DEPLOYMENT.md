# Free hosting from GitHub · الاستضافة المجانية

Bayan requires a Python backend. GitHub Pages serves static files and cannot run this FastAPI application. The provided `render.yaml` selects a **Free** Render web service, connected to `main`.

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

## العربية

1. ارفع المشروع إلى GitHub على فرع `main`.
2. سجّل الدخول إلى Render، واربط المستودع عبر Blueprint أو Web Service.
3. اختر الخطة **Free**، واستخدم أوامر البناء والتشغيل أعلاه.
4. أضف مفتاح Gemini في Environment كقيمة سرية؛ لا تضعه في GitHub.
5. انتظر ظهور **Live**، ثم افتح الرابط واختبر إجابة حقيقية والمصادر.
6. أضف الرابط الذي تم التحقق منه إلى GitHub وبوابة المسابقة.

الاستضافة المجانية قد تتوقف عند الخمول وتفقد ملفات الخادم عند إعادة التشغيل. تبقى الإجابات في سجل متصفح المستخدم. استخدام Gemini يخضع لحصة وفوترة مشروع Google.

Official references: [Render web services](https://render.com/docs/web-services), [Free services](https://render.com/docs/free), [Blueprint configuration](https://render.com/docs/blueprint-spec).
