# الأمان

## العربية

لا ترفع `.env` أو مفاتيح API أو مفاتيح التوقيع أو سجلات الأسئلة أو قواعد البيانات المولدة. اضبط الأسرار من إعدادات Environment في الاستضافة. تكون وظائف مراجعة المصطلحات محمية ومعطلة عندما تكون بيانات دخولها فارغة.

بلّغ عن المشكلات الأمنية بصورة خاصة عبر وسائل التواصل المتاحة لدى مسؤول المشروع في GitHub. لا تضع أسئلة المستفيدين أو القيم السرية في البلاغات العامة. إذا انكشف مفتاح، ألغِه لدى المزود واستبدله؛ حذف الملف وحده لا يحذفه من تاريخ Git.

---

## English

Never commit `.env`, API keys, private signing keys, question logs or generated databases. Configure server secrets using the hosting provider’s environment settings. Optional terminology-review functions are disabled when their credentials are blank.

Report security issues privately through the maintainer’s GitHub contact options. Do not include beneficiary questions or secret values in public issues. If a key is exposed, revoke it with its provider and replace it; deleting a file alone does not remove it from Git history.
