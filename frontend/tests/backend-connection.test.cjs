const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/init\(\);\s*$/, '');

function harness(respond) {
  const requests = [], tokens = [], button = {};
  const location = { pathname: '/', hash: '', search: '' };
  const user = { uid: 'user-1', email: 'test@example.invalid' };
  const context = {
    window: { APP_CONFIG: { apiBaseUrl: 'http://127.0.0.1:8000', firebase: {} }, addEventListener() {}, scrollTo() {} },
    location, history: { pushState(_a, _b, url) { location.pathname = url; }, replaceState(_a,_b,url) { location.hash=''; location.search=''; location.pathname=url; } },
    document: { querySelectorAll: () => [], querySelector: (selector) => selector === '#delivery-hour' ? { value: '10' } : selector === '.modal-backdrop' ? null : button },
    crypto: { randomUUID: () => 'attempt-id' }, URLSearchParams,
    createFirebaseAuth: () => ({ getToken: async (force) => { tokens.push(force); return 'id-token'; },
      restore: async () => user, signIn: async () => user }),
    Intl, Date, encodeURIComponent,
    setTimeout,
    fetch: async (url, options) => {
      requests.push({ url, options });
      const { status = 200, body = {} } = await respond(url, options);
      return { status, ok: status >= 200 && status < 300, json: async () => body };
    },
  };
  vm.createContext(context);
  vm.runInContext(source + `
    render = () => {};
    toast = (value) => { globalThis.toastMessage = value; };
    session = { user: { uid: 'user-1' } };
    catalog = { categories: ['economy'], consent_version: 'v1', capabilities: {} };
    setup = { consent: true, categories: ['economy'], keywords: ['AI'], delivery_hour_kst: 9, duration_days: 14 };
    currentSubscription = normalizeSubscription(${JSON.stringify(subscription)});
    globalThis.actions = { api, createSubscription, refreshSubscription, cancelSubscription, saveSettings, requestDeletion, reloadDeletion, consumeFeedbackToken, submitFeedback, chooseRating, normalizeSubscription, getSession, onAction, openCancelModal, wire, addKeyword, endedPage, resolveFeedback, renderRoute, managePage, accountPage, feedbackPage, loadCatalog, setupPage, subscriptionPreviewNotice };
    globalThis.state = () => ({ subscription: currentSubscription, error: pageError, setup, draftSettings, deletionRequest, feedbackToken, feedbackValid, feedbackError, feedbackDraft, chosenRating, feedbackSubmitted });
    globalThis.switchAccount = (uid) => { session = { user: { uid } }; };
  `, context);
  return { context, requests, tokens };
}

const subscription = { status: 'active', uid: 'user-1', subscription_id: 'period-id', categories: ['economy'],
  keywords: ['AI'], delivery_hour_kst: 10, duration_days: 14, settings_version: 1, start_date: '2026-10-08', end_date_exclusive: '2026-10-22' };

test('preview completion distinguishes preparation from delayed dispatch without claiming delivery', () => {
  const h = harness(async () => ({body:{subscription}}));
  const sub = h.context.actions.normalizeSubscription({subscription:{...subscription,preview_requested_at:'2026-10-07T10:00:00Z',preview_dispatch_status:'pending'},preview_dispatch_status:'accepted'});
  assert.match(h.context.actions.subscriptionPreviewNotice(sub), /몇 분/);
  assert.doesNotMatch(h.context.actions.subscriptionPreviewNotice(sub), /발송 완료/);
  assert.match(h.context.actions.subscriptionPreviewNotice({...sub,preview_dispatch_status:'failed'}), /구독은 정상적으로 저장/);
  assert.equal(h.context.actions.subscriptionPreviewNotice(subscription), '');
});

test('subscription form syncs user, saves engine fields and reads actual backend routes', async () => {
  const h = harness(async () => ({ body: { subscription } }));
  await h.context.actions.createSubscription();
  assert.deepEqual(h.requests.map(r => new URL(r.url).pathname), ['/users/sync', '/subscriptions/save', '/subscriptions/me']);
  const save = h.requests[1];
  assert.equal(save.options.method, 'POST');
  assert.equal(save.options.headers.Authorization, 'Bearer id-token');
  const body = JSON.parse(save.options.body);
  assert.equal(body.plan, 'basic');
  assert.deepEqual(body.engine_settings, { consent_version: 'v1', categories: ['economy'], keywords: ['AI'], delivery_hour_kst: 10, duration_days: 14 });
  const sub = h.context.state().subscription;
  assert.equal(sub.first_delivery_at, '2026-10-08T10:00:00+09:00');
  assert.equal(sub.last_delivery_date, '2026-10-21');
  assert.equal(sub.current_settings.categories[0], 'economy');
});

test('empty subscription is normal; a later failure cannot display stale data', async () => {
  let response = { body: { subscription } };
  const h = harness(async () => response);
  await h.context.actions.refreshSubscription();
  response = { status: 404, body: { detail: '구독 정보가 없습니다.' } };
  await h.context.actions.refreshSubscription();
  assert.equal(h.context.state().subscription, null);
  assert.equal(h.context.state().error, '');
  response = { status: 503, body: { detail: '연결 실패' } };
  await h.context.actions.refreshSubscription();
  assert.equal(h.context.state().subscription, null);
  assert.equal(h.context.state().error, '연결 실패');
});

test('cancel uses captured subscription identity and explicit confirmation', async () => {
  const h = harness(async () => ({ body: { subscription: { ...subscription, status: 'cancelled' } } }));
  await h.context.actions.cancelSubscription();
  assert.equal(h.requests[0].url, 'http://127.0.0.1:8000/subscriptions/period-id/cancel');
  assert.equal(h.requests[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(h.requests[0].options.body), { confirm: true });
  assert.equal(h.context.state().subscription.status, 'cancelled');
});

test('401 refreshes ID token once and surfaces FastAPI detail on failure', async () => {
  const h = harness(async () => ({ status: 401, body: { detail: '토큰 검증 실패' } }));
  await assert.rejects(h.context.actions.api('/subscriptions/me'), /토큰 검증 실패/);
  assert.equal(h.requests.length, 2);
  assert.deepEqual(h.tokens, [undefined, true]);
});

test('concurrent clicks create one save request', async () => {
  let resolveSync;
  const blocked = new Promise(resolve => { resolveSync = resolve; });
  const h = harness(async (url) => { if (url.endsWith('/users/sync')) await blocked; return { body: { subscription } }; });
  const first = h.context.actions.createSubscription();
  await h.context.actions.createSubscription();
  resolveSync();
  await first;
  assert.equal(h.requests.filter(r => r.url.endsWith('/subscriptions/save')).length, 1);
});

test('restored login synchronizes backend user', async () => {
  const h = harness(async () => ({ body: {} }));
  h.context.window.APP_CONFIG.firebase.apiKey = 'public';
  await h.context.actions.getSession();
  assert.equal(h.requests[0].url, 'http://127.0.0.1:8000/users/sync');
});

test('an account switch cannot replay a write with the other user token', async () => {
  let h;
  h = harness(async () => {
    h.context.switchAccount('other-user');
    return { status: 401, body: {} };
  });
  await assert.rejects(h.context.actions.api('/subscriptions/save', { method: 'POST', body: {} }), /계정이 변경/);
  assert.equal(h.requests.length, 1);
});


test('dynamically opened cancel modal binds confirm and close exactly once', async () => {
  const h = harness(async () => ({ body: { subscription: { ...subscription, status: 'cancelled' } } }));
  const buttons = ['close-modal', 'close-modal', 'confirm-cancel'].map(action => ({
    dataset: { action }, listeners: [], focus() {},
    addEventListener(_event, fn) { this.listeners.push(fn); }
  }));
  let modal = null;
  const root = {};
  h.context.document.body = { insertAdjacentHTML() {
    modal = { querySelectorAll: () => buttons, querySelector: () => buttons[0], remove() { modal = null; } };
  } };
  h.context.document.querySelector = selector => selector === '.modal-backdrop' ? modal
    : selector.includes('confirm-cancel') ? buttons[2] : root;
  await h.context.actions.onAction('open-cancel');
  await h.context.actions.onAction('open-cancel');
  assert.deepEqual(buttons.map(b => b.listeners.length), [1, 1, 1]);
  await buttons[0].listeners[0]();
  assert.equal(modal, null);
  // A new modal gets its own handlers; a real DOM destroys the former buttons.
  buttons.forEach(b => { b.listeners = []; });
  await h.context.actions.onAction('open-cancel');
  await buttons[2].listeners[0]();
  assert.equal(h.context.state().subscription.status, 'cancelled');
  assert.equal(h.requests.filter(r => r.url.endsWith('/cancel')).length, 1);
});

test('concurrent cancel clicks send one confirmed request and release lock after failure', async () => {
  let release;
  const blocked = new Promise(resolve => { release = resolve; });
  let fails = true;
  const h = harness(async url => {
    if (url.endsWith('/cancel')) { await blocked; return fails
      ? { status: 503, body: { detail: '일시적인 오류' } }
      : { body: { subscription: { ...subscription, status: 'cancelled' } } }; }
    return { body: { subscription: { ...subscription, status: 'cancelled' } } };
  });
  const first = h.context.actions.cancelSubscription();
  await h.context.actions.cancelSubscription();
  release();await first;
  assert.equal(h.requests.filter(r => r.url.endsWith('/cancel')).length, 1);
  assert.match(h.context.state().error, /일시적인 오류/);
  fails = false;await h.context.actions.cancelSubscription();
  assert.equal(h.context.state().subscription.status, 'cancelled');
});

test('category, duration, keyword remove, consent and hour controls bind to settings', async () => {
  const h = harness(async () => ({ body: {} }));
  const control = dataset => ({ dataset, listeners: {}, addEventListener(type, fn) { this.listeners[type] = fn; } });
  const category = control({ category: 'economy' });
  const duration = control({ duration: '28' });
  const remove = control({ removeKeyword: '0' });
  const consent = control({}); const hour = control({}); const next = {};
  h.context.document.querySelectorAll = selector => ({ '[data-category]': [category], '[data-duration]': [duration], '[data-remove-keyword]': [remove] }[selector] || []);
  h.context.document.querySelector = selector => ({ '#consent': consent, '#delivery-hour': hour, '[data-action="consent-next"]': next }[selector] || null);
  h.context.actions.wire();
  category.listeners.click();assert.equal(h.context.state().setup.categories.length, 0);
  category.listeners.click();assert.equal(h.context.state().setup.categories[0], 'economy');
  duration.listeners.click();assert.equal(h.context.state().setup.duration_days, 28);
  remove.listeners.click();assert.equal(h.context.state().setup.keywords.length, 0);
  consent.listeners.change({ target: { checked: false } });assert.equal(next.disabled, true);
  consent.listeners.change({ target: { checked: true } });assert.equal(next.disabled, false);
  hour.listeners.change({ target: { value: '18' } });assert.equal(h.context.state().setup.delivery_hour_kst, 18);
  const words = [];
  h.context.actions.addKeyword(' AI ', words);h.context.actions.addKeyword('AI', words);
  assert.deepEqual(words, ['AI']);assert.match(h.context.toastMessage, /이미 추가/);
  h.context.actions.addKeyword('a'.repeat(21), words);assert.equal(words.length, 1);
});

test('direct ended route with no subscription never claims a subscription ended', () => {
  const h = harness(async () => ({ body: {} }));
  vm.runInContext('currentSubscription=null;',h.context);
  const html = h.context.actions.endedPage();
  assert(!html.includes('구독이 종료되었어요'));
  assert(html.includes('새 구독 시작하기'));
});


test('unimplemented mail feedback reports preparation instead of calling missing API', async () => {
  const h = harness(async () => ({ status: 404, body: {} }));
  const status = {};
  h.context.document.querySelector = () => status;
  vm.runInContext('catalog.capabilities.feedback=false;feedbackToken="fixture-only";', h.context);
  await h.context.actions.resolveFeedback();
  assert.equal(h.requests.length, 0);
  assert.match(h.context.state().feedbackError, /피드백 기능은 준비 중/);
});


test('settings submit carries identity and optimistic version, preserves input on conflict', async () => {
  const h=harness(async()=>({status:409,body:{detail:'SETTINGS_CONFLICT'}}));
  vm.runInContext(`draftSettings={categories:['economy'],keywords:['AI'],delivery_hour_kst:18,expected_settings_version:1};`,h.context);
  await h.context.actions.saveSettings();
  const request=h.requests[0];
  assert.equal(new URL(request.url).pathname,'/subscriptions/period-id/settings');
  assert.equal(request.options.method,'PATCH');
  assert.deepEqual(JSON.parse(request.options.body),{categories:['economy'],keywords:['AI'],delivery_hour_kst:18,expected_settings_version:1});
  assert.equal(h.context.state().draftSettings.delivery_hour_kst,18);
  assert.match(h.context.state().error,/최신 설정/);
});

test('saved settings show server pending date and normalized keywords',async()=>{
  const next={categories:['economy'],keywords:['AI'],delivery_hour_kst:18,settings_version:2,effective_date:'2026-10-09'};
  const h=harness(async()=>({body:{subscription:{...subscription,settings_version:2,next_settings:next}}}));
  vm.runInContext(`draftSettings={categories:['economy'],keywords:['ＡＩ'],delivery_hour_kst:18,expected_settings_version:1};catalog.capabilities.settings_change=true;`,h.context);
  await h.context.actions.saveSettings();
  assert.equal(h.context.state().subscription.next_settings.effective_date,'2026-10-09');
  assert.equal(h.context.state().draftSettings,null);
  const html=h.context.actions.managePage();
  assert.match(html,/부터 적용/);assert.match(html,/변경 저장하기/);
  assert.equal(h.context.state().draftSettings.expected_settings_version,2);
});

test('account deletion requires checkbox and explicit POST, repeated clicks cannot duplicate request',async()=>{
  let release;const blocked=new Promise(resolve=>{release=resolve;});
  const h=harness(async()=>{await blocked;return {body:{request_id:'request-one',status:'pending',sending_stopped:true}};});
  h.context.document.querySelector=()=>({checked:false});
  await h.context.actions.requestDeletion();assert.equal(h.requests.length,0);
  h.context.document.querySelector=()=>({checked:true});
  const first=h.context.actions.requestDeletion();
  await h.context.actions.requestDeletion();release();await first;
  assert.equal(h.requests.length,1);
  assert.equal(new URL(h.requests[0].url).pathname,'/account-deletion-requests');
  assert.deepEqual(JSON.parse(h.requests[0].options.body),{confirm:true});
  assert.equal(h.context.state().subscription,null);
  assert.equal(h.context.state().deletionRequest.request_id,'request-one');
  assert.match(h.context.actions.accountPage(),/새 브리핑 발송을 중단/);
});

test('fragment is erased immediately, resolving is anonymous and never auto-submits rating',async()=>{
  const h=harness(async()=>({body:{valid:true,current_rating:'down',reasons:['other'],comment:'이미 남긴 의견'}}));
  h.context.location.pathname='/feedback';h.context.location.hash='#t=fixture_feedback_token&rating=up';
  h.context.actions.consumeFeedbackToken();
  assert.equal(h.context.location.hash,'');
  await h.context.actions.resolveFeedback();
  assert.equal(h.requests.length,1);
  assert.equal(new URL(h.requests[0].url).pathname,'/feedback/resolve');
  assert.equal(h.requests[0].options.headers.Authorization,undefined);
  assert.equal(JSON.parse(h.requests[0].options.body).token,'fixture_feedback_token');
  assert(!h.requests[0].url.includes('fixture_feedback_token'));
  assert.equal(h.context.state().chosenRating,'down');
  assert.equal(h.context.state().feedbackDraft.comment,'이미 남긴 의견');
  assert.equal(h.tokens.length,0);
  assert(!h.context.actions.feedbackPage().includes('메일 수신 예시'));
});

test('transient resolve failure keeps token for retry; expired link disables evaluation',async()=>{
  let response={status:503,body:{detail:'일시적인 오류'}};
  const h=harness(async()=>response);
  vm.runInContext('feedbackToken="fixture_token";',h.context);
  h.context.location.pathname='/feedback';
  await h.context.actions.resolveFeedback();assert.equal(h.context.state().feedbackToken,'fixture_token');
  response={status:410,body:{detail:'TOKEN_EXPIRED'}};
  await h.context.actions.resolveFeedback();
  assert.equal(h.context.state().feedbackToken,null);
  assert.equal(h.context.state().feedbackValid,false);
  assert.match(h.context.state().feedbackError,/만료/);
});

test('confirmed feedback uses stable reason codes, locks duplicate submissions and keeps comment on error',async()=>{
  let release;const blocked=new Promise(resolve=>{release=resolve;});
  const h=harness(async()=>{await blocked;return {status:503,body:{detail:'다시 시도'}};});
  vm.runInContext('feedbackToken="fixture_token";feedbackValid=true;chosenRating="up";',h.context);
  h.context.document.querySelectorAll=()=>[{value:'useful'}];
  h.context.document.querySelector=()=>({value:'의견 보존'});
  const first=h.context.actions.submitFeedback();
  await h.context.actions.submitFeedback();release();await first;
  assert.equal(h.requests.length,1);
  assert.deepEqual(JSON.parse(h.requests[0].options.body),{token:'fixture_token',rating:'up',reasons:['useful'],comment:'의견 보존'});
  assert.equal(h.context.state().feedbackDraft.comment,'의견 보존');
  assert.equal(h.context.state().feedbackSubmitted,false);
});

test('feedback route entered through navigation consumes fragment without loading login SDK',async()=>{
  const h=harness(async()=>({body:{valid:true,current_rating:null}}));
  h.context.location.pathname='/feedback';h.context.location.hash='#t=fixture_token&rating=up';
  await h.context.actions.renderRoute();
  assert.equal(h.requests.length,1);
  assert.equal(h.context.state().chosenRating,'up');
  assert.equal(h.tokens.length,0);
});

test('changing consent version requires new checkbox confirmation and keeps form input',async()=>{
  const h=harness(async()=>({body:{categories:['economy'],consent_version:'v2'}}));
  await h.context.actions.loadCatalog();
  assert.equal(h.context.state().setup.consent,false);
  assert.equal(h.context.state().setup.keywords[0],'AI');
});


test('late feedback resolution cannot restore the previous mail token data',async()=>{
  let release;const blocked=new Promise(resolve=>{release=resolve;});
  const h=harness(async()=>{await blocked;return {body:{valid:true,current_rating:'down',comment:'old private comment'}};});
  h.context.location.pathname='/feedback';h.context.location.hash='#t=first_token';
  h.context.actions.consumeFeedbackToken();const first=h.context.actions.resolveFeedback();
  h.context.location.hash='#t=second_token';h.context.actions.consumeFeedbackToken();
  release();await first;
  assert.equal(h.context.state().feedbackToken,'second_token');
  assert.equal(h.context.state().feedbackDraft.comment,'');
  assert.equal(h.context.state().feedbackValid,false);
});


test('successful subscription survives temporary refresh failure and repeated retry', async () => {
  const h = harness(async url => url.endsWith('/subscriptions/me')
    ? { status: 503, body: { detail: 'temporary' } } : { body: { subscription } });
  await h.context.actions.createSubscription();
  assert.equal(h.context.location.pathname, '/complete');
  assert.equal(h.context.state().subscription.subscription_id, 'period-id');
  assert.match(h.context.state().error, /구독 신청은 저장/);
  await h.context.actions.onAction('reload-subscription');
  assert.equal(h.context.state().subscription.subscription_id, 'period-id');
  assert.match(h.context.state().error, /구독 신청은 저장/);
});

test('completion refresh cannot keep data after definitive errors or an account switch', async () => {
  for (const status of [401, 404]) {
    const h = harness(async () => ({ status, body: { detail: 'unavailable' } }));
    await h.context.actions.refreshSubscription({ preserveOnError: true });
    assert.equal(h.context.state().subscription, null);
  }
  let h;
  h = harness(async () => { h.context.switchAccount('other-user'); return { status: 503 }; });
  await h.context.actions.refreshSubscription({ preserveOnError: true });
  assert.equal(h.context.state().subscription, null);
});


test('all backend interest choices render and selected interests reach subscription save', async () => {
  const categories = ['culture','economy','it_science','politics','society','world'];
  const h = harness(async url => url.endsWith('/catalog')
    ? { body:{categories,consent_version:'v1'} } : { body:{subscription} });
  await h.context.actions.loadCatalog();
  const html = h.context.actions.setupPage();
  for (const id of categories) assert.ok(html.includes('data-category="'+id+'"'));
  vm.runInContext('setup.categories = ["economy","world"]',h.context);
  await h.context.actions.createSubscription();
  const saved = h.requests.find(r => r.url.endsWith('/subscriptions/save'));
  assert.deepEqual(JSON.parse(saved.options.body).engine_settings.categories,['economy','world']);
});
