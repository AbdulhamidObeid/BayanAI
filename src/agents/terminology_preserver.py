"""Immutable terminology inspection. Findings block output; quotations are never edited.
Lexical inspection is bounded and does not establish theological correctness.

Two modes:
  audit_published_text()  — read-only; never modifies text; violations BLOCK output (used for
                            original source text and explanation prose).
  correct_translated_text() — for LLM-generated translations ONLY; applies deterministic
                              regex replacements to enforce canonical terms; logs each fix.
                              Permitted because translated text is not a sacred immutable source.
"""
import json
import re
from pathlib import Path


class TerminologyPreserverAgent:
    def __init__(self, lexicon_path=None):
        self.lexicon_path = Path(lexicon_path) if lexicon_path else Path(__file__).resolve().parents[2] / 'configs/sharia_lexicon.json'
        self.lexicon = json.loads(self.lexicon_path.read_text(encoding='utf-8'))

    def _norm(self, s):
        import unicodedata
        return ''.join(c for c in unicodedata.normalize('NFKD', s.casefold())
                       if not unicodedata.combining(c))

    def _term_rules(self, language):
        """Return only rules actually recorded for the requested language.

        English rules cannot certify wording in an unreviewed language.
        """
        results = []
        for key, term in self.lexicon['terms'].items():
            translations = term.get('translations', {})
            rules = translations.get(language)
            if rules:
                results.append((key, term, rules))
        return results

    def audit_published_text(self, text, language, found_terms=None):
        """Inspect immutable passages; NEVER repair, translate or delete publisher words.

        Negated mistranslations may be discussed without being asserted. Ambiguous
        contexts are held for review. This is a bounded lexical check, not a proof
        of theological truth.

        Returns (text_unchanged, audit_dict).
        """
        from src.core.source_policy import load_policy
        norm = self._norm
        violations = []
        checked = 0
        if language == 'ar':
            return text, {
                'target_language': language, 'terms_evaluated': 0,
                'violations_detected_in_raw_llm': 0, 'violations_details': [],
                'deterministic_fixes_applied': [], 'coverage_available': True,
                'status': 'APPROVED_SAFE',
            }
        term_rules = [(k, t, r) for k, t, r in self._term_rules(language)
                      if found_terms is None or k in found_terms]
        for key, term, rules in term_rules:
            checked += 1
            aliases = [norm(key), norm(term.get('transliteration', '')), norm(term.get('arabic', ''))]
            for sentence in re.split(r'(?<=[.!?。])\s+|\n', text):
                lowered = norm(sentence)
                patterns = list(rules.get('regex_fix_patterns', []))
                if any(alias and alias in lowered for alias in aliases):
                    patterns += load_policy().get('contextual_forbidden_patterns', {}).get(key, {}).get(language, [])
                for forbidden in rules.get('forbidden_substitutes', []):
                    if any(alias and alias in lowered for alias in aliases):
                        patterns.append(r'(?i)\b' + re.escape(forbidden) + r'\b')
                for pattern in patterns:
                    for match in re.finditer(pattern, sentence):
                        before = norm(sentence[max(0, match.start() - 60):match.start()])
                        after = norm(sentence[match.end():match.end() + 100])
                        rejected_before = re.search(
                            r"(?:\bnot|\bnever|\bpas)(?:\s+(?:mean|means|a|an|the|just|simply|merely|une?|du|de))*\s*['\"''""]*$",
                            before)
                        rejected_after = re.search(
                            r"^[\s'\"''""]*(?:is|est)\s+(?:(?:an?|une?)\s+)?(?:incorrect|wrong|inaccurate|misleading|erronee|incorrecte)\s+(?:translation|equivalent|traduction)",
                            after)
                        contrasted_before = re.search(
                            r"(?:\bunlike|\brather than|\bas opposed to|\bdifferent from|\bdiffers(?:\s+fundamentally)?\s+from)"
                            r"(?:\s+(?:conventional|common|popular|ordinary|random|good|mere|just|simple|secular|worldly|chance|or|notions?|ideas?|concepts?|of|the|an?)){0,6}\s*['\"]*$",
                            before)
                        # In an explicit paired contrast, the first clause names
                        # the alternative. A later affirmative substitute is
                        # still audited separately and cannot inherit this scope.
                        contrasted_while = (re.search(r'\bwhile\s*$',before) and
                            any(alias and re.search(r'^[^,;.!?]{1,100},\s*'+re.escape(alias)+r'\b',after)
                                for alias in aliases))
                        if rejected_before or rejected_after or contrasted_before or contrasted_while:
                            continue
                        violations.append({'term': key, 'flawed_phrase': match.group(0),
                                           'action': 'HOLD_IMMUTABLE_SOURCE_FOR_REVIEW'})
        violations = list({(v['term'], v['flawed_phrase']): v for v in violations}.values())
        covered = checked > 0
        return text, {
            'target_language': language, 'terms_evaluated': checked,
            'violations_detected_in_raw_llm': len(violations), 'violations_details': violations,
            'deterministic_fixes_applied': [], 'coverage_available': covered,
            'status': 'APPROVED_SAFE' if covered and not violations else 'FLAGGED_FOR_HUMAN_REVIEW',
        }

    def correct_translated_text(self, text, language):
        """Apply deterministic canonical replacements to LLM-generated translated text.

        PERMITTED ONLY FOR LLM-TRANSLATED OUTPUT — never for original sacred source text.
        Applies regex replacements to substitute forbidden phrases with the canonical form.
        Logs every substitution made.

        Returns (corrected_text, correction_log).
        """
        from src.core.source_policy import load_policy
        norm = self._norm
        corrected = text
        fixes = []
        if language == 'ar':
            return text, []
        for key, term, rules in self._term_rules(language):
            canonical = rules.get('canonical', '')
            aliases = [norm(key), norm(term.get('transliteration', '')), norm(term.get('arabic', ''))]
            # Only apply replacements in sentences that mention this term
            sentences = re.split(r'(?<=[.!?。])\s+|\n', corrected)
            rebuilt = []
            for sentence in sentences:
                lowered = norm(sentence)
                if not any(alias and alias in lowered for alias in aliases):
                    rebuilt.append(sentence)
                    continue
                patched = sentence
                patterns = list(rules.get('regex_fix_patterns', []))
                patterns += load_policy().get('contextual_forbidden_patterns', {}).get(key, {}).get(language, [])
                for forbidden in rules.get('forbidden_substitutes', []):
                    patterns.append(r'(?i)\b' + re.escape(forbidden) + r'\b')
                for pattern in patterns:
                    def replacer(m, _can=canonical, _key=key, _pat=pattern):
                        fixes.append({'term': _key, 'replaced': m.group(0), 'with': _can, 'pattern': _pat})
                        return _can
                    patched = re.sub(pattern, replacer, patched)
                rebuilt.append(patched)
            corrected = ' '.join(rebuilt) if '\n' not in text else '\n'.join(rebuilt)
        return corrected, fixes
