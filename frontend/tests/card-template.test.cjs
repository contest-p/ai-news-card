const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'card-template.js'), 'utf8');
const { pathToFileURL } = require('node:url');
const ready = import(pathToFileURL(path.join(__dirname, '..', 'card-template.js')).href);

test('single card and maximum two cards retain full title, terms and past dates', async () => {
  const {renderCardTemplate,cardTemplateFixture,cardTemplateMaxFixture:max} = await ready;
  assert.equal((renderCardTemplate(cardTemplateFixture).match(/data-card-number=/g)||[]).length,1);
  const html=renderCardTemplate(max);
  assert.equal((html.match(/data-card-number=/g)||[]).length,2);
  assert.equal([...max.title].length,60);
  for (const key of ['card1','card2']) {
    assert.equal([...max[key].sentences[0].text].length,400);
    assert(html.includes(max[key].sentences[0].text));
    for (const term of max[key].terms) {assert.equal([...term.definition].length,100);assert(html.includes(term.definition));}
  }
  assert(html.includes('2025.10.01 기준'));
  assert(html.includes('AI 생성'));
});

test('card text is escaped and unsafe source links are rejected',async()=>{
  const {renderCardTemplate,cardTemplateFixture} = await ready;
  const fixture=structuredClone(cardTemplateFixture);
  fixture.title='<script>alert(1)</script>';
  assert(!renderCardTemplate(fixture).includes('<script>'));
  for (const url of ['javascript:alert(1)','http://example.com/news','https://user:password@example.com/news']) {
    fixture.sources[0].url=url;
    assert.throws(()=>renderCardTemplate(fixture),/URL/);
  }
});

function datedSentence(date, value = '100', unit = '개', subject = '충전소') {
  const text = `${date} 기준 ${subject}는 ${value}${unit}입니다.`;
  return {text, source_article_id:'article_fixture_001', evidence_quote:text, as_of:date, temporal_role:'current',
    numbers:[{surface:value,unit,subject,as_of:date,source_article_id:'article_fixture_001',evidence_quote:text}]};
}
test('comparison requires exactly two supported same-subject/unit values at distinct explicit dates',async()=>{
  const {selectCardLayout,renderCardTemplate,cardTemplateFixture} = await ready;
  const fixture=structuredClone(cardTemplateFixture);
  fixture.card1.sentences=[datedSentence('2026-10-01','120'),datedSentence('2025-10-01')];
  assert.equal(selectCardLayout(fixture.card1),'comparison');
  const html=renderCardTemplate(fixture);
  assert(html.indexOf('2025.10.01 기준') < html.indexOf('2026.10.01 기준'));
  assert(!html.includes('20%')); // Never derive a change or assume comparable collection periods.
  for(const change of [s=>s.numbers[0].unit='명',s=>s.numbers[0].subject='다른 대상',s=>s.numbers[0].as_of=null,s=>s.numbers[0].surface='999',s=>s.numbers={value:1}]) {
    const bad=structuredClone(fixture); change(bad.card1.sentences[0]);
    assert.notEqual(selectCardLayout(bad.card1),'comparison');
    assert.doesNotThrow(()=>renderCardTemplate(bad));
  }
});
test('timeline uses only explicit evidence dates, does not infer events from publication dates',async()=>{
  const {selectCardLayout,renderCardTemplate,cardTemplateFixture} = await ready;
  const fixture=structuredClone(cardTemplateFixture);
  fixture.card1.sentences=['2026-10-03','2026-10-01'].map(date=>({text:`${date} 시험 운영을 시작했습니다.`,source_article_id:fixture.article_id,evidence_quote:`${date} 시험 운영을 시작했습니다.`,as_of:date,temporal_role:'current',numbers:[]}));
  assert.equal(selectCardLayout(fixture.card1),'timeline');
  const html=renderCardTemplate(fixture);
  assert(html.indexOf('2026.10.01 기준') < html.indexOf('2026.10.03 기준'));
  fixture.card1.sentences[0].as_of=null;
  assert.equal(selectCardLayout(fixture.card1),'story');
  fixture.card1.sentences[0].as_of='2026-02-30';
  assert.equal(selectCardLayout(fixture.card1),'story');
  fixture.card1.sentences[0].as_of='2026-10-01';
  assert.equal(selectCardLayout(fixture.card1),'story');
});
test('story keeps all text and fixed artwork without external image dependencies',async()=>{
  const {renderCardTemplate,cardTemplateFixture} = await ready;
  const html=renderCardTemplate(cardTemplateFixture);
  assert(html.includes('data-card-layout="story"'));
  assert(html.includes(cardTemplateFixture.card1.sentences[0].text));
  assert(html.includes('<svg'));
  assert(!html.includes('<img'));
  assert(!html.includes('<script'));
});
