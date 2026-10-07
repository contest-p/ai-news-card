import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const source = await readFile(new URL("../card-template.js", import.meta.url), "utf8");
const { renderCardTemplate } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

function fixture() {
  return {
    schema_version: "1.0", article_id: "current", title: "기사 제목", published_at: "2026-10-07T00:00:00Z", ai_generated: true,
    sources: [
      { article_id: "current", publisher: "오늘뉴스", url: "https://example.com/current", published_at: "2026-10-07T00:00:00Z" },
      { article_id: "past", publisher: "과거뉴스", url: "https://example.com/past", published_at: "2026-10-01T00:00:00Z" },
    ],
    card1: { sentences: [{ text: "현재 설명", source_article_id: "current", temporal_role: "current", as_of: null }], terms: [] },
    card2: { sentences: [{ text: "과거 설명", source_article_id: "past", temporal_role: "past", as_of: "2026-09-30" }], terms: [] },
  };
}

test("renders one selected card and escapes article text", () => {
  const data = fixture();
  data.title = '<script>alert("x")</script>';
  const html = renderCardTemplate(data, { cardNumber: 1 });
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /오늘의 핵심/);
  assert.doesNotMatch(html, /data-card-number="2"/);
  assert.match(html, /오늘뉴스 · 2026\.10\.07 보도/);
});

test("background card marks its verified fact date", () => {
  const html = renderCardTemplate(fixture(), { cardNumber: 2 });
  assert.match(html, /배경 정보/);
  assert.match(html, /2026\.09\.30 기준/);
  assert.match(html, /과거뉴스 · 2026\.10\.01 보도/);
  assert.doesNotMatch(html, /data-card-number="1"/);
});

test("supports contract maximums and rejects overlong content", () => {
  const data = fixture();
  data.title = "제".repeat(60);
  data.card1.sentences[0].text = "설".repeat(400);
  data.card1.terms = [1, 2].map((n) => ({ term: `용어${n}`, definition: "풀이".repeat(25), source_article_id: "current" }));
  const html = renderCardTemplate(data, { cardNumber: 1 });
  assert.ok(html.includes("설".repeat(400)));
  assert.ok(html.includes("풀이".repeat(25)));
  data.card1.sentences[0].text += "설";
  assert.throws(() => renderCardTemplate(data, { cardNumber: 1 }), RangeError);
});

test("rejects a missing background card and any unsafe render index", () => {
  const data = fixture();
  data.card2 = null;
  assert.throws(() => renderCardTemplate(data, { cardNumber: 2 }), TypeError);
  assert.throws(() => renderCardTemplate(fixture(), { cardNumber: 3 }), RangeError);
});
