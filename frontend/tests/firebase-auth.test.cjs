const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '..');
const moduleReady = import('data:text/javascript;base64,' + fs.readFileSync(path.join(root, 'firebase-auth.js')).toString('base64'));

test('Firebase restores user, reads fresh ID tokens, observes logout and signs in with Google', async () => {
  const { createFirebaseAuth } = await moduleReady;
  let listener, tokens = 0, loads = 0;
  const refreshFlags = [];
  const user = { uid: 'firebase-uid', getIdToken: async (force) => { refreshFlags.push(force); return `id-${++tokens}`; } };
  const auth = { currentUser: user, authStateReady: async () => {} };
  const changes = [];
  const sdk = {
    initializeApp: (config) => config, getAuth: () => auth,
    onAuthStateChanged: (_, callback) => { listener = callback; callback(auth.currentUser); },
    GoogleAuthProvider: class { setCustomParameters(value) { assert.equal(value.prompt, 'select_account'); } },
    signInWithPopup: async () => { auth.currentUser = user; listener(user); return { user }; },
    signOut: async () => { auth.currentUser = null; listener(null); },
  };
  const client = createFirebaseAuth({ apiKey: 'public', authDomain: 'test', projectId: 'test', appId: 'test' }, (value) => changes.push(value), async () => { loads++; return sdk; });
  assert.equal(await client.restore(), user);
  assert.equal(await client.getToken(), 'id-1');
  assert.equal(await client.getToken(), 'id-2');
  assert.equal(await client.getToken(true), 'id-3');
  assert.deepEqual(refreshFlags, [false, false, true]);
  await client.signOut();
  await assert.rejects(client.getToken(), /로그인/);
  assert.equal(await client.signIn(), user);
  assert.deepEqual(changes, [user, null, user]);
  assert.equal(loads, 1);
});

test('Missing configuration does not load external SDK; popup errors have Korean guidance', async () => {
  const { createFirebaseAuth, loginError } = await moduleReady;
  const client = createFirebaseAuth({}, () => {}, async () => { throw Error('must not load'); });
  await assert.rejects(client.restore(), /준비/);
  assert.match(loginError({ code: 'auth/popup-closed-by-user' }), /취소/);
  assert.match(loginError({ code: 'auth/popup-blocked' }), /팝업/);
});

test('API sends Firebase ID token only for protected routes; feedback stays anonymous', async () => {
  let tokenCalls = 0;
  const requests = [];
  const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/init\(\);\s*$/, '');
  const context = {
    location: { pathname: '/', hash: '', search: '' },
    window: { APP_CONFIG: { apiBaseUrl: 'https://api.example.test/api/v1', firebase: {} }, addEventListener() {} },
    document: { querySelector: () => ({}) },
    createFirebaseAuth: () => ({ getToken: async () => { tokenCalls++; return 'firebase-id-token'; } }),
    fetch: async (url, options) => { requests.push({ url, options }); return { ok: true, status: 200, json: async () => ({}) }; },
  };
  vm.createContext(context);
  vm.runInContext(app + '\nglobalThis.callApi = api;', context);
  await context.callApi('/catalog');
  await context.callApi('/feedback/resolve', { method: 'POST', body: { token: 'feedback-only' } });
  await context.callApi('/subscriptions/current');
  assert.equal(tokenCalls, 1);
  assert.equal(requests[0].options.headers.Authorization, undefined);
  assert.equal(requests[1].options.headers.Authorization, undefined);
  assert.equal(requests[2].options.headers.Authorization, 'Bearer firebase-id-token');
  assert.equal(JSON.parse(requests[1].options.body).token, 'feedback-only');
});
