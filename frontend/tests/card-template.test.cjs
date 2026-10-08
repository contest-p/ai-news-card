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
