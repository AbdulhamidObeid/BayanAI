# نشر المشروع على GitHub

## العربية

المشروع منشور على [BayanAI](https://github.com/AbdulhamidObeid/BayanAI)، والفرع الرئيسي هو `main`. يحتوي الجذر على `README.md` بالعربية أولاً ثم الإنجليزية. [الموقع المباشر](https://bayanai.onrender.com/).

### رفع نسخة جديدة

استخدم مجلد الإصدار النظيف `release/BayanAI/` في مشروع العمل. لا يتضمن ملفات `.env` أو قواعد البيانات الخاصة أو الكتب المحمّلة أو المسودات القديمة.

في مستودع فارغ فقط:

```bash
cd BayanAI
git init -b main
git add .
git commit -m "Publish Bayan AI application and submission materials"
git remote add origin https://github.com/AbdulhamidObeid/BayanAI.git
git push -u origin main
```

إذا كان المجلد مرتبطاً بالمستودع بالفعل، تجاوز إنشاء المستودع وإضافة `origin`، ثم سجّل تغييراتك وارفعها. سجّل الدخول عبر `gh auth login` أو مدير بيانات اعتماد Git. لا تضع رمز الدخول في رابط المستودع، ولا تستخدم الرفع القسري فوق تاريخ موجود.

### بيانات المستودع

الوصف: منصة معرفة إسلامية موثقة، بشرح يناسب مستوى السائل، وتوطين ثقافي وإجابات متعددة اللغات.

الوسوم: `islamic-ai`, `gemini`, `fastapi`, `arabic`, `multilingual`, `rag`, `localization`.

أبقِ المستودع عاماً **Public**، وأضف رابط الموقع في **About → Website**. راجع ظهور README والتراخيص وملف `.env.example` ومتطلبات التشغيل. افتح روابط العرض والفيديو والأدلة. تأكد من عدم وجود أي مفتاح API أو بيانات مستفيدين أو سجل خاص.

---

## English

Use this clean release folder, not the original working directory. It excludes `.env`, caches, private questions, downloaded books and old drafts.

## Terminal

```bash
cd BayanAI
git init -b main
git add .
git commit -m "Publish Bayan AI application and submission materials"
git remote add origin https://github.com/AbdulhamidObeid/BayanAI.git
git push -u origin main
```

If the folder already has a Git repository, skip `git init` and use the existing `origin`. Authenticate through GitHub CLI (`gh auth login`) or your Git credential manager. Do not paste a token into a remote URL or commit it. Do not force-push over existing remote commits.

## Repository About

**Description:** Source-grounded Islamic AI with multilingual explanations, regional localization and reader-aware answers.

**Topics:** `islamic-ai`, `gemini`, `fastapi`, `arabic`, `multilingual`, `rag`, `localization`.

Keep the repository **Public**. Add the live website only after deployment verification. The main branch contains current source code and final submission files.

## Final check

- README displays correctly in Arabic and English.
- `LICENSE`, `.env.example`, runtime requirements and `render.yaml` are visible.
- No `.env`, API key, signing key, database or private log is present.
- Deck, demo and test evidence links open.
- A fresh clone installs and serves the actual dashboard.
