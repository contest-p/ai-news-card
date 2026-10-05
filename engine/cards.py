"""공통 10-2 카드 조립·검사. 현재는 원문 발췌만 허용하는 보수적 제안 구현."""

from dataclasses import dataclass
from datetime import date
import copy
import re
import unicodedata

from engine.article_store import StoredArticle, prepare_article
from engine.rag import RagResult, past_cutoff

NUMBER = re.compile(r"[+-]?\d+(?:,\d{3})*(?:\.\d+)?")
UNIT = re.compile(r"(?:%p|%|억원|만원|만명|억명|명|원|개|건|회|배|년|월|일|시간|분|초)")
REVIEW_VALIDATOR_VERSION = "extractive-review-v2-term-fragments"
# 문장 전체의 의미 판정이 아닌, 명백한 접속형 종결을 거르는 품질 제안.
INCOMPLETE_TERM_END = re.compile(r"(?:하므로|이므로|으므로|하지만|했지만|하며|으며|하는데|했는데|하고|해서)$")


class CardInvalid(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CardResult:
    status: str  # ready_for_review / failed
    card_data: dict | None
    issues: tuple[str, ...]
    human_review_required: bool = True


def normalized(text):
    return unicodedata.normalize("NFKC", text)


def fields(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise CardInvalid("FIELDS_INVALID")


def string(value, maximum=None):
    if type(value) is not str or not value.strip():
        raise CardInvalid("TEXT_INVALID")
    value = normalized(value)
    if any(unicodedata.category(char) == "Cc" and char not in "\n\t" for char in value):
        raise CardInvalid("CONTROL_CHARACTER")
    if maximum is not None and len(value) > maximum:
        raise CardInvalid("LENGTH_EXCEEDED")
    return value


def evidence(item, sources):
    identity = string(item["source_article_id"])
    if identity not in sources:
        raise CardInvalid("SOURCE_NOT_ALLOWED")
    quote = string(item["evidence_quote"])
    if quote not in normalized(sources[identity].article.body):
        raise CardInvalid("QUOTE_NOT_IN_BODY")
    return identity, quote


def fact_date(value, quote):
    if value is None:
        return
    if type(value) is not str:
        raise CardInvalid("AS_OF_INVALID")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise CardInvalid("AS_OF_INVALID") from None
    if parsed.isoformat() != value or value not in quote:
        # 게시 날짜로 사건 기준일을 추정하지 않는다. 미확인이면 null.
        raise CardInvalid("AS_OF_NOT_EXPLICIT_IN_QUOTE")


def validate_card(card, sources, current_id, *, background):
    fields(card, ("sentences", "terms"))
    sentences, terms = card["sentences"], card["terms"]
    if type(sentences) is not list or not sentences or type(terms) is not list:
        raise CardInvalid("CARD_TYPE_INVALID")
    if len(terms) > 2 or (background and terms):
        raise CardInvalid("TERMS_INVALID")
    total = 0
    uses_past = False
    for sentence in sentences:
        fields(sentence, ("text", "source_article_id", "evidence_quote", "as_of", "temporal_role", "numbers"))
        text = string(sentence["text"])
        total += len(text)
        identity, quote = evidence(sentence, sources)
        # 자유 요약의 의미 일치를 증명할 수 없으므로 이번 단계는 발췌만 허용한다.
        if text not in quote:
            raise CardInvalid("PARAPHRASE_REQUIRES_SEMANTIC_REVIEW")
        role = "current" if identity == current_id else "past"
        if sentence["temporal_role"] != role or (not background and role != "current"):
            raise CardInvalid("TEMPORAL_ROLE_INVALID")
        uses_past |= role == "past"
        fact_date(sentence["as_of"], quote)
        numbers = sentence["numbers"]
        if type(numbers) is not list:
            raise CardInvalid("NUMBERS_INVALID")
        matches = list(NUMBER.finditer(text))
        found = [match.group() for match in matches]
        expected_units = []
        for match in matches:
            unit_match = UNIT.match(text, match.end())
            expected_units.append(unit_match.group() if unit_match else "")
        supplied = []
        for number in numbers:
            fields(number, ("surface", "unit", "subject", "as_of", "source_article_id", "evidence_quote"))
            surface = string(number["surface"])
            unit = number["unit"]
            subject = string(number["subject"])
            if type(unit) is not str:
                raise CardInvalid("NUMBER_UNIT_INVALID")
            number_id, number_quote = evidence(number, sources)
            if (number_id != identity or number_quote != quote or NUMBER.fullmatch(surface) is None
                    or surface not in quote or subject not in quote
                    or (unit and surface + normalized(unit) not in text)
                    or number["as_of"] != sentence["as_of"]):
                raise CardInvalid("NUMBER_EVIDENCE_MISMATCH")
            fact_date(number["as_of"], quote)
            supplied.append(surface)
        if sorted(supplied) != sorted(found):
            raise CardInvalid("NUMBER_METADATA_MISSING_OR_EXTRA")
        if [(normalized(number["surface"]), normalized(number["unit"])) for number in numbers] != list(zip(found, expected_units)):
            raise CardInvalid("NUMBER_ORDER_OR_UNIT_MISMATCH")
    if total > 400:
        raise CardInvalid("DESCRIPTION_TOO_LONG")
    if background and not uses_past:
        raise CardInvalid("BACKGROUND_WITHOUT_PAST_EVIDENCE")
    for term in terms:
        fields(term, ("term", "definition", "source_article_id", "evidence_quote"))
        name, definition = string(term["term"]), string(term["definition"], 100)
        _, quote = evidence(term, sources)
        if name not in quote or definition not in quote or NUMBER.search(definition):
            raise CardInvalid("TERM_REQUIRES_REVIEW")


def assemble_cards(draft, current: StoredArticle, rag: RagResult, *, publishers: dict[str, str],
                   work_date_kst: date) -> CardResult:
    """LLM URL·게시일은 받지 않고 검증된 기사 레코드에서 10-2 데이터를 채운다."""
    issues = []
    try:
        fields(draft, ("card1", "card2"))
        draft = copy.deepcopy(draft)
        article, digest = prepare_article(current.article)
        if digest != current.content_hash:
            raise CardInvalid("CURRENT_CONTENT_CHANGED")
        title = string(article.title, 60)
        current_sources = {article.article_id: current}
        validate_card(draft["card1"], current_sources, article.article_id, background=False)
        retained = []
        for term in draft["card1"]["terms"]:
            ending = normalized(term["definition"]).strip().rstrip(".,!?…\"'”’")
            if INCOMPLETE_TERM_END.search(ending):
                issues.append("TERM_OMITTED:INCOMPLETE_DEFINITION")
            else:
                retained.append(term)
        draft["card1"]["terms"] = retained
    except (CardInvalid, ValueError, TypeError, KeyError, AttributeError) as exc:
        return CardResult("failed", None, ("CARD1:" + getattr(exc, "code", "INPUT_INVALID"),))

    card2 = draft["card2"]
    available = dict(current_sources)
    try:
        if card2 is not None:
            if rag.status != "ready" or rag.cutoff_utc != past_cutoff(work_date_kst):
                raise CardInvalid("RAG_NOT_READY_OR_DATE_MISMATCH")
            if not 1 <= len(rag.usable_evidence) <= 3:
                raise CardInvalid("PAST_EVIDENCE_COUNT_INVALID")
            for hit in rag.usable_evidence:
                past, digest = prepare_article(hit.record.article)
                if (digest != hit.record.content_hash or past.published_at >= rag.cutoff_utc
                        or past.article_id in available or past.url == article.url):
                    raise CardInvalid("PAST_EVIDENCE_INVALID")
                available[past.article_id] = hit.record
            validate_card(card2, available, article.article_id, background=True)
    except (CardInvalid, ValueError, TypeError, KeyError, AttributeError) as exc:
        card2 = None
        issues.append("CARD2_OMITTED:" + getattr(exc, "code", "INPUT_INVALID"))
    selected = [draft["card1"]] + ([card2] if card2 is not None else [])
    identities = {item["source_article_id"] for card in selected
                  for item in card["sentences"] + card["terms"]}
    identities.add(article.article_id)
    try:
        sources = [{"article_id": identity, "publisher": string(publishers[identity]),
                    "url": available[identity].article.url,
                    "published_at": available[identity].article.published_at.isoformat()}
                   for identity in sorted(identities)]
    except (CardInvalid, KeyError):
        return CardResult("failed", None, ("SOURCE_PUBLISHER_MISSING",))
    data = {"schema_version": "1.0", "article_id": article.article_id, "title": title,
            "published_at": article.published_at.isoformat(),
            "card1": copy.deepcopy(draft["card1"]), "card2": copy.deepcopy(card2),
            "sources": sources, "ai_generated": True}
    return CardResult("ready_for_review", data, tuple(issues))
