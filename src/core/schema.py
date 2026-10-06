"""
Bayan-AI Core Schema Definitions
==================================
Incorporating the Bathel Foundation Scientific Framework (Levels A, B, C, D)
and Multi-Agent Telemetry Specifications.

Sacred Source Law: docs/guides/10_immutability_constitution_and_source_lock.md
"""

from enum import Enum
from typing import Dict, List, Optional, Any, Literal, Union
from pydantic import BaseModel, Field, ConfigDict, field_validator, StrictInt


class ResponseLevel(str, Enum):
    """
    Official 4-tier Response Classification mandated by Bathel Foundation.
    LOCKED — do not modify definitions.
    - LEVEL_A: Original Established Facts (Quran, Sahih Hadith, Pillars) -> Direct verified answer.
    - LEVEL_B: Explanation, Definition & Reasoning -> Contextual cited explanation.
    - LEVEL_C: Disputed / High Sensitivity Topics -> Bound to accredited sources, showing disagreement.
    - LEVEL_D: Personal Fatwa / Individual Incident -> Deterministic Withhold & Escalate. NO content.
    """
    LEVEL_A = "LEVEL_A"
    LEVEL_B = "LEVEL_B"
    LEVEL_C = "LEVEL_C"
    LEVEL_D = "LEVEL_D"


class CulturalPersona(str, Enum):
    """Target Cultural Personas for authentic, respectful localization."""
    WESTERN_SECULAR = "WESTERN_SECULAR"
    EUROPEAN = "EUROPEAN"
    EAST_ASIAN = "EAST_ASIAN"
    SOUTHEAST_ASIAN = "SOUTHEAST_ASIAN"
    SOUTH_ASIAN = "SOUTH_ASIAN"
    AFRICAN = "AFRICAN"
    LATIN_AMERICAN = "LATIN_AMERICAN"
    ARAB_WORLD = "ARAB_WORLD"
    GENERAL_GLOBAL = "GENERAL_GLOBAL"
    ACADEMIC_SEEKER = "ACADEMIC_SEEKER"
    DIGITAL_YOUTH = "DIGITAL_YOUTH"
    NEW_MUSLIM = "NEW_MUSLIM"
    SKEPTICAL_WESTERN = "SKEPTICAL_WESTERN"
    DAWAH_WORKER = "DAWAH_WORKER"


class SourceRepository(str, Enum):
    """Official Accredited Islamic Repositories — LOCKED per sources_registry.json."""
    QURAN_ENC = "QuranEnc (King Fahd Complex)"
    QURAN_ENC_PUBLISHED = "QuranEnc (Published Quran Translations)"
    QURANPEDIA = "Quranpedia"
    HADEETH_ENC = "HadeethEnc (Verified Hadith)"
    DORAR_HADITH = "Dorar.net (Hadith Verification)"
    DORAR_TAFSEER = "Dorar.net (Classical Exegesis)"
    DORAR_AQEEDA = "Dorar.net (Islamic Creed)"
    DORAR_FEQHIA = "Dorar.net (Jurisprudence — Informational Only)"
    DORAR_HISTORY = "Dorar.net (History & Seerah)"
    DAWA_CENTER = "Dawa.center (Islamic Digital Repository)"
    BAYYANAT = "Bayyanat — Q&A on Islam (dawa.center)"
    AL_JAMHARAH = "Al-Jamharah (Islamic Content Dictionary)"
    SHAMELA = "Al-Maktaba Al-Shamela"
    OFFLINE_CACHE = "Offline Verified Cache"
    ISLAMIC_LIBRARY = "Islamic Content Library"


class SourcePassage(BaseModel):
    kind: str
    text: str
    reference_id: str
    source_url: str


class SourceCitation(BaseModel):
    """Published passages and retrieval evidence; trust must be checked by the pipeline."""
    repository: SourceRepository
    reference_id: str = Field(description="Surah:Ayah, Hadith Number, or Dictionary Key")
    arabic_text: str = Field(description="Uthmani Quranic text or authentic Hadith wording")
    accredited_translation: str = Field(description="Official translation without machine alteration")
    scholarly_grading: Optional[str] = Field(default=None, description="Sahih, Hasan, or Mutawatir")
    source_url: str = Field(description="Canonical direct URL for judge verification")
    is_offline_cached: bool = Field(default=True, description="Available locally without internet")
    source_language: str = "en"
    translations: Dict[str, str] = Field(default_factory=dict)
    translation_urls: Dict[str, str] = Field(default_factory=dict)
    retrieval_endpoint: Optional[str] = None
    retrieved_at: Optional[str] = None
    source_digest: Optional[str] = None
    provenance_verified: bool = False
    link_checked_at: Optional[str] = None
    content_kind: str = "unverified"
    passages: Dict[str, List[SourcePassage]] = Field(default_factory=dict)
    published_attribution: Dict[str, str] = Field(default_factory=dict)

    def evidence_language(self, language):
        if language in self.translations:
            return language
        # Explanatory prose can be localized from an original publication.
        # Scripture still requires its publisher's translation in that language.
        if self.content_kind in ('article', 'book_excerpt', 'dictionary'):
            return self.source_language
        raise KeyError(language)

    def text_for(self, language):
        return self.translations[self.evidence_language(language)]

    def url_for(self, language):
        return self.translation_urls[self.evidence_language(language)]

    def passages_for(self, language):
        return self.passages.get(self.evidence_language(language), [])

    @field_validator("source_url")
    @classmethod
    def approved_url(cls, value: str) -> str:
        from src.core.source_policy import validate_source_url
        return validate_source_url(value)


class TerminologyCorrection(BaseModel):
    """Record of a deterministic linter correction by Agent 04."""
    term_key: str
    detected_distortion: str
    canonical_restoration: str
    scholarly_gloss: str


class FatwaReferralCard(BaseModel):
    """
    Official institutional referral issued when Level D is triggered.
    This card IS the response for Level D — no Islamic content is generated.
    """
    triggered: bool = True
    reason: Optional[str] = None
    referral_institution: str = "دار الإفتاء الرسمية / الهيئة العامة للشؤون الإسلامية"
    official_portal_url: str = "https://www.aliftaa.jo | https://www.dar-alifta.org"
    guidance_note: str = (
        "This system does not issue personal legal rulings or judgments on individual circumstances. "
        "Please consult a qualified Islamic scholar or accredited Ifta institution."
    )
    guidance_note_ar: str = (
        "لا يستقل النظام بالفتوى الشخصية أو المسائل القضائية والنزاعات الفردية. "
        "يُرجى التواصل مع عالم شرعي مؤهل أو دار إفتاء معتمدة."
    )


class AgentTelemetryStep(BaseModel):
    """Telemetry event emitted during live agent execution."""
    agent_id: str
    agent_name_ar: str
    status: str = Field(description="running, completed, bypassed, blocked")
    duration_ms: float
    summary: str


class PipelineInput(BaseModel):
    """Standard request payload submitted to Master Orchestrator."""
    query: str = Field(min_length=1, max_length=5000, description="Topic, question, or Ayah/Hadith reference")
    target_language: str = Field(default="en", description="ISO 639-1 code (en, fr, es, ur, id, etc.)")
    cultural_context: str = Field(default="general", description="Cultural context identifier string")
    cultural_persona: Optional[CulturalPersona] = Field(default=CulturalPersona.GENERAL_GLOBAL)
    requested_response_level: Optional[ResponseLevel] = None
    share_response: bool = False

    @field_validator("query")
    @classmethod
    def nonempty_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A question is required")
        return value.strip()

    @field_validator("target_language")
    @classmethod
    def supported_language(cls, value: str) -> str:
        from src.core.source_policy import load_policy
        value = value.strip().lower()
        if value not in load_policy()["messages"]:
            raise ValueError("This language is not yet supported; no substitute language will be used")
        return value


class KnowledgeLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    UNKNOWN = "unknown"


class AnswerRequirement(BaseModel):
    """What must be delivered, without generating its religious content."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["explanation", "exact_quote", "steps", "comparison", "example"]
    description: str = Field(min_length=1, max_length=500)


class EvidenceWitness(BaseModel):
    """An exact source span supporting one requested deliverable."""
    model_config = ConfigDict(extra="forbid")
    requirement_id: int = Field(ge=0, strict=True)
    candidate_id: int = Field(ge=0, strict=True)
    source_text: str = Field(default="")
    source_span_id: Optional[int] = Field(default=None, ge=0, strict=True,
        description="ID in this candidate's server-supplied witness_spans for the coverage language; leave source_text empty when using this ID.")


class RequirementAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: int = Field(ge=0, strict=True)
    segment_ids: List[StrictInt]


class QuestionPart(BaseModel):
    """One requested aspect, with discovery concepts rather than answer text."""
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=1000)
    keywords: Dict[str, List[str]]
    requirements: List[AnswerRequirement] = Field(default_factory=list, max_length=8)


class QueryAnalysis(BaseModel):
    """Interpretation only: AI must never provide answer text in this contract."""
    model_config = ConfigDict(extra="forbid")
    level: ResponseLevel
    confidence: float = Field(ge=0, le=1)
    knowledge_level: KnowledgeLevel
    knowledge_reason: str
    question_language: str
    keywords: Dict[str, List[str]]
    intent: str
    needs_clarification: bool
    question_parts: List[QuestionPart] = Field(default_factory=list, max_length=8)


class PartSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part_id: int = Field(ge=0, strict=True)
    keywords: Dict[str, List[str]]


class SearchExpansion(BaseModel):
    """Search concepts only; no answers, invented references or level changes."""
    model_config = ConfigDict(extra="forbid")
    keywords: Dict[str, List[str]]
    part_searches: List[PartSearch] = Field(default_factory=list)


class DiscoveryRanking(BaseModel):
    """Observed catalog IDs to fetch; catalog metadata is never answer evidence."""
    model_config = ConfigDict(extra="forbid")
    candidate_ids: List[StrictInt] = Field(max_length=32)


class RelevanceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: int = Field(ge=0, strict=True)
    direct_answer: bool = Field(strict=True)
    confidence: float = Field(ge=0, le=1)
    reason: str


class EvidenceCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part_id: int = Field(ge=0, strict=True)
    language: str
    candidate_ids: List[StrictInt]
    witnesses: List[EvidenceWitness] = Field(default_factory=list)
    supported: bool = Field(strict=True)
    confidence: float = Field(ge=0, le=1)
    reason: str


class EvidenceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: List[RelevanceDecision]
    fully_answers_question: bool = Field(strict=True)
    explains_disagreement: bool = Field(strict=True)
    coverage: List[EvidenceCoverage] = Field(default_factory=list)


class PartialEvidencePlan(BaseModel):
    """A useful supported subset; omissions are disclosed, never claimed answered."""
    model_config = ConfigDict(extra="forbid")
    supported_parts: List[QuestionPart] = Field(max_length=8)
    unanswered_aspects: Dict[str, List[str]]
    selection: EvidenceSelection


class ExplanationSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(explanation|quote)$")
    text: str = Field(default="", max_length=6000)
    citation_ids: List[StrictInt] = Field(min_length=1)


class ExplanationProse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["explanation"]
    text: str = Field(min_length=1, max_length=6000)
    citation_ids: List[StrictInt] = Field(min_length=1)


class FullQuoteReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["quote"]
    text: Literal[""] = ""
    citation_ids: List[StrictInt] = Field(min_length=1, max_length=1)


class ExplanationVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str
    segments: List[Union[ExplanationProse, FullQuoteReference]] = Field(min_length=1, max_length=12)


class ExplanationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    versions: List[ExplanationVersion]


class SourceBoundDraft(BaseModel):
    """Unpublished proposal; evidence validation and independent review remain mandatory."""
    model_config = ConfigDict(extra="forbid")
    selection: EvidenceSelection
    draft: ExplanationDraft


class ExplanationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str
    segment_id: int = Field(ge=0, strict=True)
    supported: bool = Field(strict=True)
    confidence: float = Field(ge=0, le=1)
    reason: str


class AnswerCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part_id: int = Field(ge=0, strict=True)
    language: str
    segment_ids: List[StrictInt]
    requirements: List[RequirementAnswer] = Field(default_factory=list)
    answered: bool = Field(strict=True)
    reason: str


class ExplanationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: List[ExplanationCheck]
    fully_answers_question: bool = Field(strict=True)
    versions_agree: bool = Field(strict=True)
    correct_languages: bool = Field(strict=True)
    preserves_qualifications: bool = Field(strict=True)
    respectful_without_stereotypes: bool = Field(strict=True)
    no_unmarked_quotes: bool = Field(strict=True)
    coverage: List[AnswerCoverage] = Field(default_factory=list)


class PartialExplanationReview(ExplanationReview):
    """Independent approval of relevance, limited scope and honest disclosure."""
    partial_scope_valid: bool = Field(strict=True)
    limitations_accurate: bool = Field(strict=True)


class PassageTranslationResult(BaseModel):
    """LLM response contract for batch Arabic→target_language passage translation.
    One translated string per input passage, in the same order.
    Used only for article/book_excerpt/dictionary sources — never for Quran or Hadith.
    """
    model_config = ConfigDict(extra="forbid")
    translations: List[str] = Field(min_length=1, description="Translated passage texts, one per input passage, in order.")


class AnswerVersion(BaseModel):
    language: str
    text: str
    source_urls: List[str] = Field(default_factory=list)
    passages: List[SourcePassage] = Field(default_factory=list)
    source_text: str = ""
    scope_notice: str = ""
    explanation_segments: List[ExplanationSegment] = Field(default_factory=list)
    # LLM-translated passages for Arabic-only sources (article/book_excerpt/dictionary).
    # Keyed by str(citation_index). Never replaces original Arabic; only used for non-AR display.
    # Quran and Hadith MUST always use their official publisher translations — never this field.
    translated_passages: Dict[str, List[SourcePassage]] = Field(default_factory=dict)


class CacheLookupMetrics(BaseModel):
    """Anonymous counters for one cache layer during one request."""
    hits: int = 0
    misses: int = 0
    errors: int = 0
    duration_ms: float = 0.0


class PipelineOutput(BaseModel):
    """Comprehensive, verified output delivered by Bayan-AI."""
    query: str
    target_language: str
    cultural_persona: CulturalPersona = CulturalPersona.GENERAL_GLOBAL
    response_level: Optional[ResponseLevel] = None

    # Content
    localized_text: Optional[str] = Field(default=None, description="Final culturally adapted and linted output text")
    localized_content: Optional[str] = Field(default=None, description="Alias for localized_text (legacy)")

    # Citations — list of all citations used
    citations: List[SourceCitation] = Field(default_factory=list)
    citation: Optional[SourceCitation] = Field(default=None, description="Primary citation (first in citations)")

    # Safety
    fatwa_referral: Optional[FatwaReferralCard] = None
    linter_corrections: List[TerminologyCorrection] = Field(default_factory=list)
    is_hallucination_free: bool = False
    answer_status: str = "ABSTAINED"
    stop_reason: Optional[str] = None
    knowledge_level: KnowledgeLevel = KnowledgeLevel.UNKNOWN
    analysis: Optional[QueryAnalysis] = None
    versions: List[AnswerVersion] = Field(default_factory=list)
    linter_status: str = "NOT_RUN"
    source_verified: bool = False

    # Provenance
    provenance_hash: Optional[str] = Field(default=None, description="SHA-256 of the output content")
    cryptographic_hash: Optional[str] = Field(default=None, description="Alias for provenance_hash (legacy)")
    verification_qr_data: Optional[str] = Field(default=None, description="Payload encoded in QR code")

    # Pipeline telemetry
    telemetry: List[AgentTelemetryStep] = Field(default_factory=list)
    cache_metrics: Dict[str, CacheLookupMetrics] = Field(default_factory=dict)
    answer_cache_reused: bool = False
    total_duration_ms: float = 0.0

    def model_post_init(self, __context: Any) -> None:
        """Sync aliases after initialization."""
        if self.localized_text and not self.localized_content:
            self.localized_content = self.localized_text
        elif self.localized_content and not self.localized_text:
            self.localized_text = self.localized_content
        if self.citations and not self.citation:
            self.citation = self.citations[0]
        elif self.citation and not self.citations:
            self.citations = [self.citation]
        if self.provenance_hash and not self.cryptographic_hash:
            self.cryptographic_hash = self.provenance_hash
        elif self.cryptographic_hash and not self.provenance_hash:
            self.provenance_hash = self.cryptographic_hash
