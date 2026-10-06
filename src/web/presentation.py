"""Exact display excerpts and publisher labels; never alter verified evidence."""
import json
import re
import sqlite3
from functools import lru_cache
from src.core.analysis_agent import normalized
from src.core.source_policy import ROOT


@lru_cache(maxsize=1)
def presentation_config():
    return json.loads((ROOT/'configs/presentation.json').read_text(encoding='utf-8'))


def exact_excerpts(text, query, scripture=False, opening=False):
    """Select contiguous original sentences most relevant to the query.

    Boilerplate Islamic closing formulas (khutbah endings, istighfar closings,
    publication footers) are filtered out before scoring so they can never win
    over substantive content — even if they happen to share words with the query.

    Scoring: keyword overlap × penalty; only sentences with ≥1 real query-term
    overlap qualify. Length is a tie-breaker only, never the primary signal.
    """
    if scripture:
        return [text]
    config = presentation_config()
    boilerplate = config.get('boilerplate_closing_patterns', [])
    score_threshold = config.get('excerpt_score_threshold', 0.80)

    # This known Word-export record is formatting metadata, not translation.
    chunks = re.split(config['formatting_residue_pattern'], text)
    clean = [chunk.strip() for chunk in chunks if chunk.strip()]
    if sum(len(chunk) for chunk in clean) <= min(500, config['excerpt_characters']):
        return clean

    units = []
    for chunk in clean:
        for unit in re.split(r'(?<=[.!?؟۔])\s+|\n\s*\n', chunk):
            unit = unit.strip()
            tokens = re.findall(r'[^\W_]+', normalized(unit))
            # Font/diagram debris made of isolated letters is not useful prose.
            debris = len(tokens) > 15 and sum(len(t) == 1 for t in tokens) / len(tokens) > .5
            if len(unit) < 30 or re.search(r'\*{3,}', unit) or debris:
                continue
            # Strip boilerplate closing phrases — these are standard lecture/article
            # endings that are never relevant evidence for any query.
            if any(re.search(pattern, unit) for pattern in boilerplate):
                continue
            units.append(unit)

    if opening:
        # A narrative preview begins at its opening, never at a keyword-rich
        # middle whose pronouns/actions depend on omitted story context.
        chosen = []; size = 0
        for unit in units[:config['excerpt_sentences']]:
            if size + len(unit) > config['excerpt_characters']:
                break
            chosen.append(unit); size += len(unit)
        return chosen

    terms = set(re.findall(r'[^\W_]+', normalized(query)))

    def score(unit):
        unit_tokens = set(re.findall(r'[^\W_]+', normalized(unit)))
        overlap = len(terms & unit_tokens)
        # Longer sentences carry more information; use as a tie-breaker only
        length_bonus = min(len(unit), 600) / 600
        # Penalise very short lines that are likely headings or nav fragments
        penalty = 0.5 if len(unit) < 60 else 1.0
        return (overlap * penalty, length_bonus * penalty)

    ranked = sorted(enumerate(units), key=lambda pair: score(pair[1]), reverse=True)
    best_score = score(ranked[0][1])[0] if ranked else 0
    chosen = []; size = 0
    for index, unit in ranked:
        s = score(unit)
        # Must have at least 1 real keyword match — length bonus alone is not enough
        if s[0] < 1:
            continue
        # Must be within score_threshold of the best sentence (tighter than before)
        if best_score and s[0] < best_score * score_threshold:
            continue
        if len(chosen) < config['excerpt_sentences'] and size + len(unit) <= config['excerpt_characters']:
            chosen.append(index); size += len(unit)

    # Fallback: if nothing scored ≥1, take the shortest non-boilerplate unit
    # (better than showing nothing; but prefer substantive content above)
    if not chosen and units:
        chosen = [min(range(len(units)), key=lambda i: len(units[i]))]
    return [units[i] for i in sorted(chosen)] or clean[:1]




@lru_cache(maxsize=114)
def chapter_names(surah):
    path=ROOT/'data/cache/sharia_sources_cache.db'
    if path.exists():
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
            row=db.execute('SELECT surah_name_ar,surah_name_en FROM quran_cache WHERE surah_number=? LIMIT 1',(surah,)).fetchone()
            if row:return row
    return str(surah),str(surah)


def card_title(citation, language):
    config=presentation_config();labels=config['languages'][language]
    if citation.content_kind=='quran':
        surah,ayah=citation.reference_id.split(':')
        names=chapter_names(int(surah))
        return names[0 if language=='ar' else 1]+' - '+labels['verse']+' '+ayah
    if citation.content_kind=='hadith':
        return labels['hadith_site']+' · '+citation.reference_id
    metadata={}
    try:metadata=json.loads(citation.published_attribution.get(citation.source_language,'{}'))
    except (ValueError,TypeError):pass
    title=metadata.get('title','')
    for suffix in config['title_suffixes']:
        if title.endswith(suffix):title=title[:-len(suffix)]
    site=labels['article_site'] if citation.content_kind=='article' else labels['library_site']
    return site+' - '+title.strip() if title.strip() else site


def _source_sort_key(card):
    """Quran first, Hadith second, everything else third."""
    order={'quran':0,'hadith':1}
    return order.get(card['kind'],2)


def source_cards(output):
    """One shared numbering map across versions; omit evidence never cited in prose/quotes.
    Cards are ordered Quran → Hadith → other sources (capped at 3 for the 'other' category).
    For non-Arabic versions, Arabic-only sources are shown using LLM-translated passages
    stored in version.translated_passages — never mixing the displayed language.
    """
    citations=output.citations
    used=sorted({i for version in output.versions for segment in version.explanation_segments for i in segment.citation_ids})
    if not any(v.explanation_segments for v in output.versions):used=list(range(len(citations)))
    numbers={i:n+1 for n,i in enumerate(used)}
    result={}
    for version in output.versions:
        raw_cards=[]
        translated=getattr(version,'translated_passages',{})
        for i in used:
            c=citations[i]
            # Prefer native passages in the version's language.
            # For Arabic-only sources in a non-Arabic version, use pre-translated passages.
            native_parts=c.passages_for(version.language)
            translated_parts=translated.get(str(i),[]) if not native_parts else []
            parts=native_parts or translated_parts
            # Determine the display language for this card's text.
            if native_parts:
                display_language=c.evidence_language(version.language)
            elif translated_parts:
                display_language=version.language  # translated → matches output language
            else:
                display_language=c.evidence_language(version.language)  # fallback (will be AR)
            query=output.query+' '+' '.join(s.text for s in version.explanation_segments if i in s.citation_ids)
            displayed=[]
            for part in parts or [None]:
                kind=part.kind if part else c.content_kind
                if part:
                    text=part.text
                else:
                    # No passage at all — fall back to raw citation text in evidence language
                    text=c.text_for(version.language)
                excerpts=exact_excerpts(text,query,kind in ('quran','hadith'),
                    opening=c.content_kind=='hadith' and kind=='commentary')
                best_excerpts=[e for e in excerpts if e in text]
                if not best_excerpts and excerpts:
                    best_excerpts=excerpts[:1]
                for excerpt in best_excerpts:
                    if excerpt not in text:raise ValueError('display_excerpt_changed')
                    displayed.append({'kind':kind,'text':excerpt,'is_excerpt':excerpt!=text,
                        'full_text':text,'collapsed':excerpt==text and len(text)>presentation_config()['excerpt_characters']})
                if best_excerpts and any(excerpt!=text for excerpt in best_excerpts):
                    displayed.append({'kind':kind,'text':text,'is_excerpt':False,
                        'full_text':text,'collapsed':True})
            raw_cards.append({'citation_id':i,'number':numbers[i],'title':card_title(c,version.language),
                'kind':c.content_kind,'language':display_language,
                'source_url':c.url_for(version.language),'parts':displayed,
                'is_translated': bool(translated_parts)})
        # Sort: Quran → Hadith → other sources, and cap "other" at 3
        quran=[card for card in raw_cards if card['kind']=='quran']
        hadith=[card for card in raw_cards if card['kind']=='hadith']
        others=[card for card in raw_cards if card['kind'] not in ('quran','hadith')][:3]
        result[version.language]=quran+hadith+others
    return result
