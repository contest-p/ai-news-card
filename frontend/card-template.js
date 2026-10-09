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
// Fixed local artwork; never fetch or generate an illustration during delivery.
const ART = {
  story: '<rect x="20" y="18" width="78" height="92" rx="16" fill="#fff4e7"/><rect x="30" y="28" width="58" height="72" rx="8" fill="white" stroke="#ef850e" stroke-width="3"/><rect x="40" y="40" width="38" height="17" rx="4" fill="#ef850e"/><path d="M40 69h38M40 79h38M40 89h24" stroke="#738091" stroke-width="4" stroke-linecap="round"/><circle cx="100" cy="96" r="20" fill="#ef850e"/><path d="m90 96 7 7 14-16" fill="none" stroke="white" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>',
  comparison: '<rect x="12" y="18" width="108" height="96" rx="22" fill="#fff4e7"/><path d="M30 94h73" stroke="#b4bfce" stroke-width="3"/><rect x="33" y="68" width="16" height="24" rx="4" fill="#4388e8"/><rect x="59" y="51" width="16" height="41" rx="4" fill="#ef850e"/><rect x="85" y="33" width="16" height="59" rx="4" fill="#ef850e"/>',
  timeline: '<circle cx="66" cy="66" r="48" fill="#fff4e7"/><circle cx="66" cy="66" r="33" fill="white" stroke="#ef850e" stroke-width="4"/><path d="M66 43v25l18 12" fill="none" stroke="#ef850e" stroke-width="5" stroke-linecap="round"/><circle cx="28" cy="106" r="9" fill="#4388e8"/><circle cx="66" cy="106" r="9" fill="#ef850e"/><circle cx="104" cy="106" r="9" fill="#ef850e"/>',
};
const illustration = (kind) => `<svg class="news-fixed-art" viewBox="0 0 132 132" aria-hidden="true" xmlns="http://www.w3.org/2000/svg">${ART[kind]}</svg>`;
const explicitDate = (value) => {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(value + 'T00:00:00Z');
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
};
function comparisonRows(card) {
  const rows = [];
  for (const sentence of card.sentences) {
    if (sentence.numbers != null && !Array.isArray(sentence.numbers)) return null;
    for (const number of sentence.numbers || []) {
      // Presentation checks are additional to the engine's source validation.
      if (!number || typeof number.subject !== 'string' || !number.subject.trim() || number.subject.length > 40 ||
          typeof number.unit !== 'string' || !number.unit || number.unit.length > 6 ||
          typeof number.surface !== 'string' || number.surface.length > 12 ||
          !/^[+-]?\d+(?:,\d{3})*(?:\.\d+)?$/.test(number.surface) ||
          !explicitDate(number.as_of) || number.as_of !== sentence.as_of ||
          number.source_article_id !== sentence.source_article_id ||
          number.evidence_quote !== sentence.evidence_quote || typeof sentence.evidence_quote !== 'string' ||
          !sentence.evidence_quote.includes(number.subject) ||
          !sentence.evidence_quote.includes(number.as_of) ||
          !sentence.text.includes(number.surface + number.unit)) return null;
      rows.push(number);
    }
  }
  if (rows.length !== 2 || rows[0].subject !== rows[1].subject ||
      rows[0].unit !== rows[1].unit || rows[0].as_of === rows[1].as_of) return null;
  return rows.sort((a, b) => a.as_of.localeCompare(b.as_of));
}
export function selectCardLayout(card) {
  if (comparisonRows(card)) return 'comparison';
  const sentences = card.sentences;
  if (sentences.length >= 2 && sentences.length <= 4 &&
      sentences.every(s => explicitDate(s.as_of) && s.evidence_quote?.includes(s.as_of)) &&
      new Set(sentences.map(s => s.as_of)).size === sentences.length) return 'timeline';
  return 'story';
}
function oneCard(data, key, number, sources) {
  const card = data[key];
  if (!card) return '';
  const kind = selectCardLayout(card);
  const past = card.sentences.some(s => s.temporal_role === 'past');
  const label = number === 1 ? LABELS.current : LABELS.past;
  let visual = '';
  if (kind === 'comparison') {
    const rows = comparisonRows(card);
    visual = `<section class="news-comparison" aria-label="기준일별 수치">${rows.map((n, i) => `<div class="news-metric ${i ? 'current' : 'previous'}"><p class="news-metric-date">${safeText(formatDate(n.as_of))} 기준</p><p class="news-metric-subject">${safeText(n.subject)}</p><p class="news-metric-value">${safeText(n.surface)}<span>${safeText(n.unit)}</span></p></div>`).join('')}</section><p class="news-comparison-note">각 기준일의 수치예요. 집계 범위와 조건은 원문을 확인해 주세요.</p>`;
  }
  const sentences = kind === 'timeline'
    ? `<section class="news-timeline" aria-label="확인된 날짜별 내용">${[...card.sentences].sort((a,b) => a.as_of.localeCompare(b.as_of)).map(s => `<div class="news-timeline-step"><p class="news-timeline-date">${safeText(formatDate(s.as_of))} 기준</p>${sentenceMarkup(s, sources, number)}</div>`).join('')}</section>`
    : `<div class="news-template-body">${card.sentences.map(s => sentenceMarkup(s, sources, number)).join('')}</div>`;
  const terms = (card.terms || []).map(term => {
    if (!sources.has(term.source_article_id)) throw new TypeError(`등록되지 않은 용어 근거 기사: ${term.source_article_id}`);
    return `<div class="news-term"><strong>${safeText(term.term)}</strong><span>${safeText(term.definition)}</span></div>`;
  }).join('');
  const cardSources = [...new Set(card.sentences.map(s => s.source_article_id).concat((card.terms || []).map(t => t.source_article_id)))];
  const sourceMarkup = cardSources.map(id => {
    const source = sources.get(id);
    if (!validSource(source)) throw new TypeError(`출처 URL이 올바르지 않습니다: ${id}`);
    return `<a class="news-source-link" href="${safeText(source.url)}" target="_blank" rel="noopener noreferrer">${safeText(source.publisher || '원문')}${formatDate(source.published_at) ? ` · ${safeText(formatDate(source.published_at))} 보도` : ''}</a>`;
  }).join('');
  return `<article class="news-template-card" data-card-number="${number}" data-card-layout="${kind}"><div class="news-template-top"><span class="news-template-label ${past ? 'past' : 'current'}">${label}</span><span class="news-template-ai">AI 생성</span></div><div class="news-card-hero"><h2>${safeText(number === 1 ? data.title : '과거 기사에서 확인한 배경')}</h2>${illustration(kind)}</div>${visual}${kind !== 'timeline' ? '<h3 class="news-explanation-title">핵심 내용</h3>' : ''}${sentences}${terms ? `<section class="news-terms"><h3>기사 속 용어</h3>${terms}</section>` : ''}<div class="news-template-sources">${sourceMarkup}</div></article>`;
}

export function renderCardTemplate(data) {
  if (!data || typeof data !== "object") throw new TypeError("카드 데이터가 필요합니다.");
  if (count(data.title) < 1 || count(data.title) > 60) throw new RangeError("제목은 NFKC 기준 1~60자여야 합니다.");
  if (data.ai_generated !== true) throw new TypeError("AI 생성 여부가 true인 카드만 표시할 수 있습니다.");
  validateCard(data, "card1", 2);
  validateCard(data, "card2", 0);
  const sources = sourceIndex(data.sources);
  const articleDate = formatDate(data.published_at);
  return `<section class="news-template" aria-label="뉴스 카드"><header class="news-template-header"><div class="news-template-brand"><span class="brand-mark" aria-hidden="true"></span><strong>뉴스 브리핑</strong></div>${articleDate ? `<span class="news-template-date">기사 게시 · ${safeText(articleDate)}</span>` : ""}</header><h1 class="news-template-title">오늘의 관심 뉴스</h1><p class="news-template-subtitle">하루 한 번, 한눈에 읽는 브리핑</p>${oneCard(data, "card1", 1, sources)}${oneCard(data, "card2", 2, sources)}<p class="news-template-disclaimer">AI가 기사 내용을 요약·설명했습니다. 정확한 맥락은 원문을 확인해 주세요.</p></section>`;
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
