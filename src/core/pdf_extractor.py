"""Single-process MuPDF extraction. No OCR, text repair or source admission.

Publisher bytes arrive on stdin. Only exact numbered original page text leaves
on stdout. The parent applies provenance, readability and evidence gates.
"""
import json
import sys


def main():
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # Older installed releases expose only this module.
    with fitz.open(stream=sys.stdin.buffer.read(),filetype='pdf') as document:
        if document.page_count>int(sys.argv[1]):
            return 2
        pages=[(i+1,page.get_text(sort=False).strip()) for i,page in enumerate(document)]
    sys.stdout.write(json.dumps(pages,ensure_ascii=False))
    return 0


if __name__=='__main__':
    sys.exit(main())
