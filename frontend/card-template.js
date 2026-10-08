/*
 * Reusable, deterministic HTML card template for the engine's Playwright renderer.
 * Import renderCardTemplate(data) and pass the PRD 10-2 card JSON object.
 * The returned markup contains no recipient or feedback-token fields.
 */
const LABELS = { current: "오늘의 핵심", past: "배경 정보" };
const safeText = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const count = (value) => [...String(value ?? "").normalize("NFKC")].length;
const formatDate = (value) => {
  if (!value) return "";
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[1]}.${match[2]}.${match[3]}` : "";
};
function sourceIndex(sources) {
  return new Map((sources || []).map((source) => [source.article_id, source]));
}
function validateCard(data, key, maxTerms) {
  const card = data?.[key];
  if (card == null && key === "card2") return;
  if (!card || !Array.isArray(card.sentences) || !card.sentences.length) throw new TypeError(`${key}에는 한 개 이상의 설명 문장이 필요합니다.`);
  const explanationLength = card.sentences.reduce((total, item) => total + count(item?.text), 0);
  if (explanationLength > 400) throw new RangeError(`${key} 설명은 NFKC 기준 400자를 넘을 수 없습니다.`);
  if (!Array.isArray(card.terms || [] ) || (card.terms || []).length > maxTerms) throw new RangeError("용어는 최대 2개까지 표시할 수 있습니다.");
  for (const item of card.sentences) {
    if (!item || typeof item.text !== "string" || !item.source_article_id) throw new TypeError("설명 문장과 근거 기사 정보가 필요합니다.");
    if (item.temporal_role !== "current" && item.temporal_role !== "past") throw new TypeError("temporal_role은 current 또는 past여야 합니다.");
  }
  for (const term of card.terms || []) {
    if (count(term.term) < 1 || count(term.term) > 20 || count(term.definition) > 100) throw new RangeError("용어는 1~20자, 풀이는 100자 이내여야 합니다.");
    if (!term.source_article_id) throw new TypeError("용어의 근거 기사 정보가 필요합니다.");
  }
}
function validSource(source) {
  if (!source?.url) return false;
  try { const url = new URL(source.url); return url.protocol === "https:" && !url.username && !url.password; } catch { return false; }
}
function sentenceMarkup(sentence, sources, cardNumber) {
  const source = sources.get(sentence.source_article_id);
  if (!source) throw new TypeError(`등록되지 않은 근거 기사: ${sentence.source_article_id}`);
  const asOf = formatDate(sentence.as_of);
  const date = sentence.temporal_role === "past"
    ? (asOf || formatDate(source.published_at))
    : "";
  const dateLabel = date
    ? `<span class="news-card-date">${safeText(date)} ${asOf ? "기준" : "보도에 따르면"}</span>`
    : "";
  return `<p class="news-card-sentence">${safeText(sentence.text)}${dateLabel}</p>`;
}
function oneCard(data, key, number, sources) {
  const card = data[key];
  if (!card) return "";
  const past = card.sentences.some((sentence) => sentence.temporal_role === "past");
  const label = number === 1 ? LABELS.current : LABELS.past;
  const sentences = card.sentences.map((sentence) => sentenceMarkup(sentence, sources, number)).join("");
  const terms = (card.terms || []).map((term) => {
    const source = sources.get(term.source_article_id);
    if (!source) throw new TypeError(`등록되지 않은 용어 근거 기사: ${term.source_article_id}`);
    return `<div class="news-term"><strong>${safeText(term.term)}</strong><span>${safeText(term.definition)}</span></div>`;
  }).join("");
  const cardSources = [...new Set(card.sentences.map((sentence) => sentence.source_article_id).concat((card.terms || []).map((term) => term.source_article_id)))];
  const sourceMarkup = cardSources.map((id) => {
    const source = sources.get(id);
    if (!validSource(source)) throw new TypeError(`출처 URL이 올바르지 않습니다: ${id}`);
    return `<a class="news-source-link" href="${safeText(source.url)}" target="_blank" rel="noopener noreferrer">${safeText(source.publisher || "원문")}${formatDate(source.published_at) ? ` · ${safeText(formatDate(source.published_at))} 보도` : ""}</a>`;
  }).join("");
  return `<article class="news-template-card" data-card-number="${number}"><div class="news-template-top"><span class="news-template-label ${past ? "past" : "current"}">${label}</span><span class="news-template-ai">AI 생성</span></div><div class="news-template-body">${sentences}</div>${terms ? `<section class="news-terms"><h3>기사 속 용어</h3>${terms}</section>` : ""}<div class="news-template-sources">${sourceMarkup}</div></article>`;
}

export function renderCardTemplate(data) {
  if (!data || typeof data !== "object") throw new TypeError("카드 데이터가 필요합니다.");
  if (count(data.title) < 1 || count(data.title) > 60) throw new RangeError("제목은 NFKC 기준 1~60자여야 합니다.");
  if (data.ai_generated !== true) throw new TypeError("AI 생성 여부가 true인 카드만 표시할 수 있습니다.");
  validateCard(data, "card1", 2);
  validateCard(data, "card2", 0);
  const sources = sourceIndex(data.sources);
  const articleDate = formatDate(data.published_at);
  return `<section class="news-template" aria-label="뉴스 카드"><header class="news-template-header"><div class="news-template-brand"><span class="brand-mark" aria-hidden="true"></span><strong>뉴스 브리핑</strong></div>${articleDate ? `<span class="news-template-date">기사 게시 · ${safeText(articleDate)}</span>` : ""}</header><h1 class="news-template-title">${safeText(data.title)}</h1>${oneCard(data, "card1", 1, sources)}${oneCard(data, "card2", 2, sources)}<p class="news-template-disclaimer">AI가 기사 내용을 요약·설명했습니다. 정확한 맥락은 원문을 확인해 주세요.</p></section>`;
}

export const cardTemplateFixture = Object.freeze({
  schema_version: "1.0",
  article_id: "article_fixture_001",
  title: "샘플 기사: 서비스 연결 테스트",
  published_at: "2026-10-05T21:00:00Z",
  card1: { sentences: [{ text: "테스트용 기사에서는 시험 운영이 시작됐다고 설명합니다.", source_article_id: "article_fixture_001", evidence_quote: "시험 운영이 시작됐다", as_of: "2026-10-06", temporal_role: "current", numbers: [] }], terms: [] },
  card2: null,
  sources: [{ article_id: "article_fixture_001", publisher: "테스트 출처", url: "https://example.com/news/fixture-001", published_at: "2026-10-05T21:00:00Z" }],
  ai_generated: true,
});

// Deliberately fills the PRD limits; synthetic copy, never presented as actual news.
const repeated = (text, limit) => [...text.repeat(Math.ceil(limit / [...text].length))].slice(0, limit).join("");
export const cardTemplateMaxFixture = {
  ...cardTemplateFixture,
  title: repeated("최대 길이 가상 뉴스 제목 ", 60),
  card1: { sentences: [{ ...cardTemplateFixture.card1.sentences[0], text: repeated("현재 기사 핵심을 설명하는 가상 문장입니다. ", 400) }],
    terms: [{term:"가상 용어 하나",definition:repeated("가상 용어 풀이입니다. ",100),source_article_id:"article_fixture_001"},
            {term:"가상 용어 둘",definition:repeated("가상 설명입니다. ",100),source_article_id:"article_fixture_001"}] },
  card2: { sentences: [{ text: repeated("과거 근거를 설명하는 가상 문장입니다. ",400),source_article_id:"article_fixture_past",temporal_role:"past",as_of:"2025-10-01" }],
    terms: [] },
  sources: [...cardTemplateFixture.sources,{article_id:"article_fixture_past",publisher:"가상 과거 출처",url:"https://example.com/news/past-fixture",published_at:"2025-10-01T00:00:00Z"}],
};
