"""공통 10-2 카드 조립·검사. 현재는 원문 발췌만 허용하는 보수적 제안 구현."""

from dataclasses import dataclass
from datetime import date
import copy
import re
import unicodedata

from engine.article_store import StoredArticle, prepare_article
from engine.rag import RagResult, past_cutoff
from engine.selection import CARD_TITLE_MAX_CHARS

# 영문·숫자에 붙은 식별자(COVID-19, G7, 5.5의 일부)는 수량으로 보지 않는다.
NUMBER = re.compile(r"(?:(?<![A-Za-z0-9.,])[+-])?(?<![A-Za-z0-9.,])(?<![A-Za-z]-)\d+(?:,\d{3})*(?:\.\d+)?")
# 날짜는 시점 표현이다. 발췌 원문 일치로 확인하며 수치 메타데이터 대상에서 뺀다.
DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{4}\.\s?\d{1,2}\.\s?\d{1,2}\.?"
                  r"|(?:\d{4}년\s*)?\d{1,2}월\s*\d{1,2}일|\d{4}년\s*\d{1,2}월")
TERM_NAME_MAX_CHARS = 20  # 프런트 card-template.js와 같은 값. 김현서와 최종 합의 필요.
UNIT = re.compile(r"(?:%p|%|억원|만원|만명|억명|명|원|개|건|회|배|년|월|일|시간|분|초)")
REVIEW_VALIDATOR_VERSION = "extractive-review-v3-dates-term-names"
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


def quantity_matches(text):
    """날짜 구간을 가린 뒤 수량 후보와 바로 뒤의 단위를 돌려준다."""
    masked = DATE.sub(lambda match: "#" * len(match.group()), text)
    return [(match.group(), (UNIT.match(masked, match.end()) or [""])[0])
            for match in NUMBER.finditer(masked)]


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
        matches = quantity_matches(text)
        found = [surface for surface, _ in matches]
        expected_units = [unit for _, unit in matches]
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
        title = string(article.title, CARD_TITLE_MAX_CHARS)
        current_sources = {article.article_id: current}
        if type(draft["card1"]) is dict and type(draft["card1"].get("terms")) is list:
            kept = []
            for term in draft["card1"]["terms"]:
                # 선택 항목인 용어 이름이 템플릿 한도를 넘으면 카드 전체 대신 용어만 생략한다.
                if (type(term) is dict and type(term.get("term")) is str
                        and len(normalized(term["term"])) > TERM_NAME_MAX_CHARS):
                    issues.append("TERM_OMITTED:NAME_TOO_LONG")
                else:
                    kept.append(term)
            draft["card1"]["terms"] = kept
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


def repair_numeric_format(draft, current: StoredArticle):
    """사실 문구는 수정하지 않는다. 수치 필드 형식 보정과 수치 불일치 문장 제외만 허용.

    원문 근거를 먼저 확인한 문장에서만 surface의 단위를 분리하고, 검증된 문장 근거의
    일부인 수치 근거를 전체 문장 근거로 넓힌다. 그 외 오류는 그대로 예외로 전달한다.
    """
    repaired = copy.deepcopy(draft)
    changes = []
    retained = []
    for sentence in repaired["card1"]["sentences"]:
        quote = sentence["evidence_quote"]
        if (sentence["source_article_id"] == current.article.article_id
                and normalized(quote) in normalized(current.article.body)
                and normalized(sentence["text"]) in normalized(quote)):
            for number in sentence["numbers"]:
                surface, unit = number["surface"], number["unit"]
                match = NUMBER.match(surface)
                if (match and unit and surface == match.group() + unit
                        and surface in sentence["text"]):
                    number["surface"] = match.group()
                    changes.append("NUMBER_SURFACE_UNIT_SEPARATED")
                if (number["evidence_quote"] != quote and number["evidence_quote"]
                        and number["evidence_quote"] in quote):
                    number["evidence_quote"] = quote
                    changes.append("NUMBER_QUOTE_EXPANDED_TO_VERIFIED_SENTENCE")
        try:
            validate_card({"sentences": [sentence], "terms": []},
                          {current.article.article_id: current}, current.article.article_id, background=False)
        except CardInvalid as exc:
            if not exc.code.startswith("NUMBER_"):
                raise
            changes.append("SENTENCE_OMITTED:" + exc.code)
        else:
            retained.append(sentence)
    repaired["card1"]["sentences"] = retained
    return repaired, list(dict.fromkeys(changes))
