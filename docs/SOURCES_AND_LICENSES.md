# Sources, tools and licenses · المصادر والتراخيص

## Source policy

`configs/trust_policy.json` is the active retrieval policy; `configs/sources_registry.json` records the source registry. `configs/sharia_lexicon.json` controls religious terminology. This release preserves these files and the application’s existing publication checks.

| Source | Role |
|---|---|
| [QuranEnc](https://quranenc.com/) | Published Quran text, translations and publisher commentary |
| [HadeethEnc](https://hadeethenc.com/) | Published hadith narration, grading, attribution and explanation |
| [Islamic Content](https://islamic-content.com/) | Original terminology and article passages |
| [Dorar](https://dorar.net/) | Accredited subject references and qualification |
| [Shamela](https://shamela.ws/) | Original readable book pages with page/edition context |
| [Dawa Center](https://dawa.center/) | Publisher catalog and approved outreach publications |
| [Official Islamic Content MCP](https://mcp.islamiccontent.org/mcp) | Publisher discovery and retrieval access |

Additional allowed publisher hosts and routes are listed in the active policy. A source listing does not imply every collection is available or every PDF has a validated extractor. Exact publisher passages are distinguished from AI explanations; unavailable or insufficient evidence leads to qualification, abstention or referral. Quran and hadith are never translated by the model as replacement published source text.

## Rights and credits

- **Original code:** MIT, copyright Abdulhamid Obeid. This grant excludes third-party published passages and external assets.
- **Publisher materials:** retain publisher/author rights. Retrieval, quotation and redistribution must follow the applicable source terms. Original downloaded books, private caches and full corpus snapshots are excluded from this repository. Small test fixtures retain their original source URLs and provenance.
- **UI font:** IBM Plex Sans Arabic via Google Fonts, distributed upstream under SIL Open Font License. The application loads it remotely; font binaries are not bundled here. [IBM Plex](https://github.com/IBM/plex).
- **Submission artwork:** organizer/partner logos in the official deck template remain their owners’ marks; their inclusion is for this competition submission, not a trademark license.
- **Tools:** Python (PSF), FastAPI/Uvicorn (BSD), Pydantic and python-dotenv (MIT), Requests/google-genai/Jinja2 (Apache/BSD upstream licenses), Beautiful Soup (MIT), python-multipart (Apache), HTTPX (BSD), pytest (MIT).
- **PyMuPDF:** offered under GNU AGPL v3 or commercial license. Respect its license for network deployment and redistribution. The complete source of this application is provided publicly; the MIT notice does not replace PyMuPDF’s obligations. [PyMuPDF licensing](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright).

Keep upstream copyright notices and dependency licenses. The dependency list identifies the installed tools; installers supply their license metadata.

## العربية

يعتمد بَيان على سياسة المصادر المعتمدة في ملفات الإعدادات، ويعرض نص المصدر مستقلاً عن شرح الذكاء الاصطناعي. تبقى حقوق الكتب والنصوص والشعارات والخطوط لأصحابها. ترخيص MIT يخص الكود الأصلي فقط، ولا يمنح إعادة ترخيص محتوى الناشرين. لم تُرفع المفاتيح أو البيانات الخاصة أو الكتب المحمّلة أو قواعد البيانات إلى هذا المستودع.
