# GitHub publishing · نشر المشروع

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

## العربية

استخدم مجلد الإصدار النظيف، وليس مجلد العمل القديم. نفّذ الأوامر أعلاه للنشر على `main`، ثم أضف الوصف والوسوم من إعدادات About. أبقِ المستودع عاماً. لا ترفع مفاتيح API أو قواعد البيانات الخاصة. بعد التأكد من الاستضافة، أضف رابط الموقع إلى المستودع وبوابة المسابقة.
