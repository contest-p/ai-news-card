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
    location, history: { pushState(_a, _b, url) { location.pathname = url; } },
    document: { querySelector: (selector) => selector === '#delivery-hour' ? { value: '10' } : selector === '.modal-backdrop' ? null : button },
    crypto: { randomUUID: () => 'attempt-id' }, URLSearchParams,
    createFirebaseAuth: () => ({ getToken: async (force) => { tokens.push(force); return 'id-token'; },
      restore: async () => user, signIn: async () => user }),
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
    globalThis.actions = { api, createSubscription, refreshSubscription, cancelSubscription, normalizeSubscription, getSession, onAction };
    globalThis.state = () => ({ subscription: currentSubscription, error: pageError });
    globalThis.switchAccount = (uid) => { session = { user: { uid } }; };
  `, context);
  return { context, requests, tokens };
}

const subscription = { status: 'active', uid: 'user-1', subscription_id: 'period-id', categories: ['economy'],
  keywords: ['AI'], delivery_hour_kst: 10, duration_days: 14, start_date: '2026-10-08', end_date_exclusive: '2026-10-22' };

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

test('cancel uses owner-scoped PATCH and normalizes result', async () => {
  const h = harness(async () => ({ body: { subscription: { ...subscription, status: 'cancelled' } } }));
  await h.context.actions.cancelSubscription();
  assert.equal(h.requests[0].url, 'http://127.0.0.1:8000/subscriptions/cancel');
  assert.equal(h.requests[0].options.method, 'PATCH');
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
