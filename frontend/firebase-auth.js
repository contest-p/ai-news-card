// Load only Auth, and only outside the feedback page. Pin both CDN modules.
const loadSdk = async () => {
  const [app, auth] = await Promise.all([
    import("https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js"),
    import("https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js"),
  ]);
  return { ...app, ...auth };
};

export function createFirebaseAuth(config, onUserChanged, loader = loadSdk) {
  let ready;
  async function initialize() {
    if (!config || !["apiKey", "authDomain", "projectId", "appId"].every((key) => config[key])) {
      throw new Error("로그인 연결이 아직 준비되지 않았습니다. 잠시 후 다시 시도해 주세요.");
    }
    if (!ready) ready = (async () => {
      const sdk = await loader();
      const auth = sdk.getAuth(sdk.initializeApp(config));
      auth.languageCode = "ko";
      await auth.authStateReady();
      sdk.onAuthStateChanged(auth, onUserChanged);
      return { sdk, auth };
    })().catch((error) => { ready = null; throw error; });
    return ready;
  }
  return {
    async restore() { return (await initialize()).auth.currentUser; },
    async signIn() {
      const { sdk, auth } = await initialize();
      const provider = new sdk.GoogleAuthProvider();
      provider.setCustomParameters({ prompt: "select_account" });
      return (await sdk.signInWithPopup(auth, provider)).user;
    },
    async signOut() { const { sdk, auth } = await initialize(); await sdk.signOut(auth); },
    async getToken() {
      const { auth } = await initialize();
      if (!auth.currentUser) throw new Error("로그인 후 다시 시도해 주세요.");
      // Firebase refreshes an expiring ID token; never reuse a saved access token.
      return auth.currentUser.getIdToken();
    },
  };
}

export function loginError(error) {
  return ({
    "auth/popup-closed-by-user": "로그인을 취소했습니다. 다시 시도할 수 있어요.",
    "auth/cancelled-popup-request": "이미 로그인 창이 열려 있습니다. 열린 창을 확인해 주세요.",
    "auth/popup-blocked": "브라우저에서 팝업을 허용한 뒤 다시 로그인해 주세요.",
    "auth/unauthorized-domain": "이 사이트의 로그인 연결을 준비 중입니다. 담당자에게 알려 주세요.",
    "auth/operation-not-allowed": "Google 로그인 연결을 준비 중입니다.",
    "auth/network-request-failed": "인터넷 연결을 확인하고 다시 시도해 주세요.",
  })[error?.code] || "로그인하지 못했습니다. 잠시 후 다시 시도해 주세요.";
}
