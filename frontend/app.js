import { renderCardTemplate, cardTemplateFixture } from "./card-template.js";
import { createFirebaseAuth, loginError } from "./firebase-auth.js";

const CONFIG = window.APP_CONFIG || {};
const BASE = (CONFIG.apiBaseUrl || "").replace(/\/$/, "");
const API_ROOT = BASE;
const CATEGORY_LABELS = {
  economy: "경제", it_science: "IT/과학", politics: "정치",
  society: "사회", world: "국제", culture: "문화",
};
const DURATION_LABELS = { 7: "1주", 14: "2주", 28: "4주" };
const app = document.querySelector("#app");

let session = null;
let catalog = { categories: [], durations: [7, 14, 28], consent_version: "", capabilities: {} };
let currentSubscription = null;
let pageError = "";
let subscriptionReloading = false;
let feedbackToken = null;
let initialFeedbackRating = null;
let subscriptionAttempt = null;
let setup = { categories: [], keywords: [], delivery_hour_kst: 9, duration_days: 14 };
let draftSettings = null;
let isMenuOpen = false;
let savingSubscription = false;
let loginPending = false;

const esc = (value = "") => String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const categoryName = (id) => CATEGORY_LABELS[id] || id;
const dateLabel = (value, opts = {}) => {
  if (!value) return "확인 중";
  const d = new Date(`${String(value).slice(0, 10)}T00:00:00+09:00`);
  if (Number.isNaN(d.getTime())) return "확인 중";
  return new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", year: "numeric", month: "long", day: "numeric", ...opts }).format(d);
};
const dateTimeKst = (value) => { const d=new Date(value); return Number.isNaN(d.getTime())?"확인 중":new Intl.DateTimeFormat("ko-KR",{timeZone:"Asia/Seoul",year:"numeric",month:"long",day:"numeric",hour:"numeric",minute:"2-digit",hour12:true}).format(d); };
const hourLabel = (hour) => `${Number(hour) < 12 ? "오전" : "오후"} ${Number(hour) % 12 || 12}시`;
const currentPath = () => location.pathname.replace(/\/+$/, "") || "/";
const pageFromPath = () => ({ "/": "home", "/login": "login", "/privacy": "privacy", "/subscribe": "setup", "/complete": "complete", "/manage": "manage", "/ended": "ended", "/feedback": "feedback", "/service": "service" })[currentPath()] || "home";
async function renderRoute() {
  const path = currentPath();
  if (pageFromPath() === "setup") {
    render();
    if (BASE) {
      await loadCatalog();
      if (currentPath() === path) render();
    }
    return;
  }
  if (["manage", "ended", "complete"].includes(pageFromPath()) && session) await refreshSubscription();
  if (currentPath() === path) render();
}
const go = async (path) => { history.pushState({}, "", path); isMenuOpen = false; pageError = ""; await renderRoute(); window.scrollTo(0, 0); };
const urlFor = (path) => path;

const firebaseAuth = createFirebaseAuth(CONFIG.firebase, (user) => {
  const previousUid = session?.user?.uid;
  session = user ? { user } : null;
  if (previousUid !== user?.uid) {
    currentSubscription = null;
    draftSettings = null;
    subscriptionAttempt = null;
    setup = { categories: [], keywords: [], delivery_hour_kst: 9, duration_days: 14 };
    if (pageFromPath() !== "feedback") render();
  }
});

async function getSession() {
  if (pageFromPath() === "feedback") return null;
  if (!CONFIG.firebase?.apiKey) return null;
  try {
    const user = await firebaseAuth.restore();
    session = user ? { user } : null;
    if (user) await api("/users/sync", { method: "POST" });
  } catch (error) { pageError = error.status ? error.message : loginError(error); }
  return session;
}

async function api(path, { method = "GET", body, idempotencyKey } = {}) {
  if (!API_ROOT) throw new Error("Backend API 주소가 아직 설정되지 않았습니다. frontend/config.js를 확인해 주세요.");
  const requestUid = session?.user?.uid;
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (path !== "/catalog" && !path.startsWith("/feedback")) {
    headers.Authorization = `Bearer ${await firebaseAuth.getToken()}`;
  }
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  const checkAccount = () => {
    if (headers.Authorization && requestUid !== session?.user?.uid) throw new Error("로그인 계정이 변경되었습니다. 다시 시도해 주세요.");
  };
  const send = () => { checkAccount(); return fetch(`${API_ROOT}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store", credentials: "omit" }); };
  let response = await send();
  if (response.status === 401 && headers.Authorization) {
    headers.Authorization = `Bearer ${await firebaseAuth.getToken(true)}`;
    response = await send();
  }
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  checkAccount();
  if (!response.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : null;
    const error = new Error(data?.error?.message || detail || (response.status === 422 ? "입력한 구독 설정을 확인해 주세요." : "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."));
    error.status = response.status; error.code = data?.error?.code;
    throw error;
  }
  return data;
}

async function loadCatalog() {
  try {
    const data = await api("/catalog");
    const rawCategories = data?.categories || data?.items || [];
    const ids = rawCategories.map((item) => typeof item === "string" ? item : item.code).filter((id) => CATEGORY_LABELS[id]);
    catalog = { categories: ids, durations: data?.durations || data?.duration_days || [7, 14, 28], consent_version: data?.consent_version || data?.policy?.consent_version || "", capabilities: data?.capabilities || {} };
  } catch (error) { pageError = error.message; }
}

function header(active = "") {
  const label = session?.user?.email ? "로그아웃" : "로그인";
  return `<header class="site-header"><div class="nav-wrap"><a class="brand" href="/" data-go="/"><span class="brand-mark" aria-hidden="true"></span>뉴스 브리핑</a><nav class="nav-links ${isMenuOpen ? "open" : ""}" aria-label="주요 메뉴"><a class="nav-link ${active === "service" ? "active" : ""}" href="/service" data-go="/service">서비스 소개</a><a class="nav-link ${active === "manage" ? "active" : ""}" href="/manage" data-go="/manage">구독 관리</a><a class="nav-link ${active === "privacy" ? "active" : ""}" href="/privacy" data-go="/privacy">개인정보 안내</a></nav><div class="nav-actions"><button class="btn btn-outline btn-sm" data-action="auth">${label}</button><button class="mobile-menu" aria-label="메뉴 열기" data-action="menu">☰</button></div></div></header>`;
}
function footer(){return `<footer class="footer"><div class="footer-inner"><a class="brand" href="/" data-go="/"><span class="brand-mark" aria-hidden="true"></span>뉴스 브리핑</a><nav class="footer-nav"><a href="/service" data-go="/service">서비스 소개</a><span>·</span><a href="/manage" data-go="/manage">구독 관리</a><span>·</span><a href="/privacy" data-go="/privacy">개인정보 안내</a></nav><span>AI가 생성한 설명은 원문과 함께 확인해 주세요.</span></div></footer>`;}
function shell(content, active = ""){return `${header(active)}<main>${content}</main>${footer()}`;}
function stepper(active){const steps=["개인정보 동의","구독 설정","완료"];return `<div class="stepper">${steps.map((s,i)=>`${i?'<span class="step-line"></span>':''}<div class="step ${i+1===active?'active':i+1<active?'done':''}"><span class="step-num">${i+1<active?'✓':i+1}</span><span>${s}</span></div>`).join("")}</div>`;}
function heroImage(){return `<div class="hero-art hero-image"><img src="/assets/hero-mail.jpg" alt="" width="720" height="720" decoding="async"></div>`;}
function art(extra=""){return `<div class="hero-art ${extra}"><div class="art-orbit"><span class="spark one">✦</span><span class="spark two">✦</span><div class="mail-illustration"></div><div class="art-badge">✓</div></div></div>`;}
function errorNotice(){return pageError?`<div class="notice error" role="alert">${esc(pageError)}</div>`:"";}

function homePage(){return shell(`<section class="container hero"><div><p class="eyebrow">내 관심 뉴스, 매일 한눈에</p><h1>매일 아침,<br>관심 뉴스를 <span class="hero-title-final">간결하게<span class="orange-dot">.</span></span></h1><p>관심 분야의 뉴스를 카드로 정리해 이메일로 보내드려요.</p><div class="hero-actions"><button class="btn btn-primary" data-action="start">구독 시작하기 <span>→</span></button><a class="btn btn-quiet" href="/service" data-go="/service">어떻게 만들어지나요?</a></div><p class="small">Google 계정으로 시작하고, 다음 날부터 선택한 시간대에 받아보세요.</p></div>${heroImage()}</section>`);}

function loginPage(){return shell(`<section class="container login-layout"><div class="hero-copy"><p class="eyebrow">한눈에 모아보는 나만의 뉴스</p><h1 class="page-title">내 관심 뉴스의 시작<span class="orange-dot">.</span></h1><p class="page-subtitle">Google 계정으로 간편하게 시작하세요.</p>${heroImage()}</div><section class="card login-card"><h2>로그인</h2><p>오늘의 뉴스를 더 쉽게 읽어보세요.</p>${errorNotice()}<button class="google-btn" data-action="google-login"><span class="google-g">G</span>Google로 계속하기</button><p class="center small muted">별도의 비밀번호를 만들지 않아요.</p><div class="login-links"><a href="/service" data-go="/service">서비스 소개</a><span>│</span><a href="/privacy" data-go="/privacy">개인정보 안내</a></div><div class="notice" style="margin-top:24px">구독 설정은 로그인 후 진행할 수 있어요.</div></section></section>`,"");}

function privacyPage(){return shell(`<section class="container page"><div class="center">${stepper(1)}<h1 class="page-title">필요한 정보만, 안전하게<span class="orange-dot">.</span></h1><p class="page-subtitle">구독 전에 개인정보 안내를 확인해 주세요.</p></div>${errorNotice()}<div class="privacy-list"><div class="card privacy-items"><article class="privacy-item"><div class="privacy-icon">♙</div><div><h3>어떤 정보를 사용하나요?</h3><p>로그인 식별 정보와 이메일, 선택한 관심 분야·키워드·수신 시간·구독 기간, 동의 및 피드백 정보를 처리합니다.</p></div></article><article class="privacy-item"><div class="privacy-icon">▤</div><div><h3>어디에 사용하나요?</h3><p>로그인 확인, 구독 설정 저장, 뉴스 브리핑 발송과 서비스 운영에 사용합니다.</p></div></article><article class="privacy-item"><div class="privacy-icon">◷</div><div><h3>언제 정리하나요?</h3><p>구독 만료·해제 후 30일 이내 개인정보를 삭제하거나 익명화합니다. 계정 삭제 요청은 별도 처리합니다.</p></div></article><article class="privacy-item"><div class="privacy-icon">⚙</div><div><h3>계정 삭제를 원하시나요?</h3><p>계정 삭제 요청 채널과 본인 확인 절차는 Backend에서 확정한 안내 문구를 이곳에 연결합니다.</p></div></article></div><aside class="card privacy-aside"><div class="lock">♙</div><h3>소중한 관심 정보,<br><span style="color:var(--orange-dark)">안전하게</span> 지켜요.</h3><p class="muted small">뉴스를 맞춤 전달하는 데 필요한 정보만 사용하고 관리합니다.</p></aside></div><div class="consent-box"><label class="checkline"><input type="checkbox" id="consent" ${setup.consent ? "checked" : ""}><span><strong>[필수]</strong> 개인정보 수집·이용 안내를 확인하고 동의합니다.</span></label><button class="btn btn-primary btn-block" data-action="consent-next" ${setup.consent ? "" : "disabled"}>동의하고 설정하기 <span>→</span></button><p class="form-help">동의해야 구독을 시작할 수 있어요.</p></div></section>`,"privacy");}

function setupPage(){const categories=catalog.categories.filter((id)=>CATEGORY_LABELS[id]);return shell(`<section class="container page"><div>${stepper(2)}<div class="setup-layout"><section><p class="eyebrow">나에게 맞는 브리핑</p><h1 class="page-title" style="font-size:clamp(34px,4vw,46px)">어떤 뉴스를 받아볼까요?</h1><p class="page-subtitle" style="margin-bottom:22px">관심 분야와 수신 시간을 선택해 주세요.</p>${errorNotice()}<div class="card setup-card"><div class="group-title"><span class="group-number">1</span><div><h3>관심 분야</h3><p>한 개 이상 선택해 주세요.</p></div></div><div class="category-grid">${categories.map((id)=>`<button class="choice ${setup.categories.includes(id)?"selected":""}" data-category="${id}" aria-pressed="${setup.categories.includes(id)}">${setup.categories.includes(id)?"✓ ":""}${esc(categoryName(id))}</button>`).join("")}</div><p class="form-help" style="padding-left:41px">${categories.length ? "현재 선택 가능한 분야 목록을 표시합니다." : "현재 선택 가능한 분야가 없습니다. 잠시 후 다시 확인해 주세요."}</p><div class="form-group"><div class="group-title"><span class="group-number">2</span><div><h3>관심 키워드</h3><p>선택 사항이에요. 최대 5개, 키워드당 1~20자.</p></div></div><div class="keyword-row"><input id="keyword-input" class="field" maxlength="80" placeholder="관심 있는 키워드를 입력해 주세요" value="${esc(document.querySelector("#keyword-input")?.value||"")}"><button class="btn btn-primary" data-action="add-keyword">추가</button></div><div class="chips">${setup.keywords.map((word,i)=>`<span class="chip">${esc(word)}<button aria-label="${esc(word)} 삭제" data-remove-keyword="${i}">×</button></span>`).join("")}</div><p class="form-help" style="padding-left:41px">키워드가 없거나 일치하는 뉴스가 없으면 선택 분야의 뉴스로 준비합니다.</p></div><div class="form-group"><div class="group-title"><span class="group-number">3</span><div><h3>받는 시간</h3><p>한국 시간 기준, 하루 한 번 받아보세요.</p></div></div><div style="padding-left:41px"><select class="select" id="delivery-hour">${Array.from({length:24},(_,hour)=>`<option value="${hour}" ${Number(setup.delivery_hour_kst)===hour?"selected":""}>${hourLabel(hour)}</option>`).join("")}</select><p class="form-help">발송은 지연될 수 있습니다.</p></div></div><div class="form-group"><div class="group-title"><span class="group-number">4</span><div><h3>구독 기간</h3><p>원하는 기간을 선택해 주세요.</p></div></div><div class="duration-grid">${[7,14,28].map((days)=>`<button class="duration ${Number(setup.duration_days)===days?"selected":""}" data-duration="${days}" aria-pressed="${Number(setup.duration_days)===days}"><strong>${DURATION_LABELS[days]}</strong><span>${days}일 동안 받아봐요</span></button>`).join("")}</div></div></div></section><aside class="card summary-card"><h3>내 구독 미리보기</h3><p>선택한 설정을 한눈에 확인해 보세요.</p><div class="summary-rows"><div class="summary-row"><span>관심 분야</span><strong>${setup.categories.length?setup.categories.map(categoryName).map(esc).join(" · "):"선택해 주세요"}</strong></div><div class="summary-row"><span>키워드</span><strong>${setup.keywords.length?setup.keywords.map(esc).join(" · "):"선택 사항"}</strong></div><div class="summary-row"><span>받는 시간</span><strong>${hourLabel(setup.delivery_hour_kst)}</strong></div><div class="summary-row"><span>구독 기간</span><strong>${DURATION_LABELS[setup.duration_days]||"2주"}</strong></div></div><button class="btn btn-primary btn-block" style="margin-top:18px" data-action="create-subscription" ${categories.length ? "" : "disabled"}>구독 시작하기 <span>→</span></button><p class="summary-foot">· 첫 메일은 가입 다음 날부터 발송돼요.<br>· 메일 도착은 지연될 수 있어요.</p></aside></div></div></section>`,"");}

function completePage(){const sub=currentSubscription||{};const c=sub.current_settings||sub.settings||{};return shell(`<section class="container page"><div class="success-hero"><div><p class="eyebrow">설정 완료</p><h1 class="page-title">내일, 첫 브리핑이<br>도착해요<span class="orange-dot">!</span></h1><p class="page-subtitle">관심 뉴스의 핵심과 배경을 카드로 보내드릴게요.</p></div><div class="success-art">${art()}</div></div><section class="card" style="padding:8px 12px"><div class="details-grid"><div class="detail"><label>관심 분야</label><strong>${(c.categories||setup.categories).map(categoryName).map(esc).join(" · ")||"확인 중"}</strong></div><div class="detail"><label>관심 키워드</label><strong>${(c.keywords||setup.keywords).map(esc).join(" · ")||"없음"}</strong></div><div class="detail"><label>수신 시간 · 한국 시간</label><strong>${hourLabel(c.delivery_hour_kst??setup.delivery_hour_kst)}</strong></div><div class="detail"><label>구독 기간</label><strong>${DURATION_LABELS[sub.duration_days??setup.duration_days]||"확인 중"}</strong></div><div class="detail"><label>첫 발송 예정</label><strong>${sub.first_delivery_at?dateTimeKst(sub.first_delivery_at):dateLabel(sub.first_delivery_date||sub.first_send_date)}</strong></div><div class="detail"><label>마지막 구독 날짜</label><strong>${dateLabel(sub.last_delivery_date||sub.last_date)}</strong></div></div></section><p class="small muted center" style="margin:16px">ⓘ 발송 상황에 따라 도착 시간이 늦어질 수 있어요.</p><div class="success-actions"><button class="btn btn-primary" data-go="/manage">구독 관리로 이동 <span>→</span></button><button class="btn btn-quiet" data-go="/service">서비스 소개 보기</button></div></section>`,"");}

function managePage(){
  const sub=currentSubscription;
  if(!sub?.status)return `<section class="container page empty-subscription"><section class="card empty-subscription-hero"><div class="empty-subscription-copy"><p class="eyebrow">나만의 뉴스 브리핑</p><h1 class="page-title">첫 브리핑을<br>준비해 볼까요<span class="orange-dot">?</span></h1><p class="page-subtitle">관심 분야와 받는 시간을 정하면, 선택한 뉴스의 핵심을 이메일로 보내드려요.</p>${errorNotice()}<div class="hero-actions"><button class="btn btn-primary" data-go="/privacy">새 구독 시작하기 <span>→</span></button><button class="btn btn-quiet" data-action="reload-subscription" ${subscriptionReloading?"disabled":""}>${subscriptionReloading?"불러오는 중…":"다시 불러오기"}</button></div><p class="small muted empty-subscription-note" aria-live="polite">${subscriptionReloading?"구독 상태를 확인하고 있어요.":"구독 설정은 언제든지 관리할 수 있어요."}</p></div><div class="empty-subscription-art"><img src="/assets/empty-subscription.png" alt="뉴스 카드가 담긴 봉투에서 빼꼼 나온 주황색 캐릭터"></div></section><section class="empty-subscription-steps" aria-label="구독 시작 순서"><article><span>01</span><div><strong>관심 분야를 골라요</strong><p>궁금한 주제를 선택해요.</p></div></article><i aria-hidden="true">→</i><article><span>02</span><div><strong>받는 시간을 정해요</strong><p>하루 한 번 받을 시간을 정해요.</p></div></article><i aria-hidden="true">→</i><article><span>03</span><div><strong>메일로 브리핑을 받아요</strong><p>핵심 내용을 카드로 읽어요.</p></div></article></section><p class="empty-service-link"><a href="/service" data-go="/service">서비스 이용 방법 보기 <span>→</span></a></p></section>`;
  if(sub.status!=="active")return endedPageContent(sub);
  const settings=sub.current_settings;
  return `<section class="container page"><p class="eyebrow">구독 관리</p><h1 class="page-title">내 뉴스 구독</h1><div class="status-pill" style="margin:18px 0">구독 중 · ${DURATION_LABELS[sub.duration_days]||"기간 확인 중"}</div>${errorNotice()}<section class="card manage-settings"><h2 class="section-heading">현재 구독 설정</h2><div class="details-grid"><div class="detail"><label>관심 분야</label><strong>${settings.categories.map(categoryName).map(esc).join(" · ")||"설정 정보 없음"}</strong></div><div class="detail"><label>관심 키워드</label><strong>${settings.keywords.map(esc).join(" · ")||"없음"}</strong></div><div class="detail"><label>수신 시간 · 한국 시간</label><strong>${Number.isInteger(settings.delivery_hour_kst)?hourLabel(settings.delivery_hour_kst):"설정 정보 없음"}</strong></div><div class="detail"><label>첫 발송 예정</label><strong>${sub.first_delivery_at?dateTimeKst(sub.first_delivery_at):dateLabel(sub.first_delivery_date)}</strong></div><div class="detail"><label>마지막 구독 날짜</label><strong>${dateLabel(sub.last_delivery_date)}</strong></div></div><p class="notice" style="margin-top:18px">구독 설정 변경은 준비 중입니다. 현재 설정으로 브리핑을 받아볼 수 있어요.</p></section><section class="card cancel-band" style="margin-top:22px"><div><h3>구독을 중단하고 싶으신가요?</h3><p>구독을 해제하면 이후 브리핑 발송이 중단됩니다.</p></div><button class="btn btn-outline" data-action="open-cancel">구독 해제</button></section></section>`;
}
function endedPageContent(sub){return `<section class="container page center"><div class="success-art" style="height:210px"><div class="art-orbit"><span class="spark one">✦</span><div class="mail-illustration" style="background:linear-gradient(145deg,#8abdf7,#498ce4)"></div><div class="art-badge" style="background:var(--orange)">✓</div></div></div><h1 class="page-title">구독이 ${sub.status==="cancelled"?"해제":"종료"}되었어요</h1><p class="page-subtitle">관심 뉴스가 다시 필요할 때 새 구독을 시작하세요.</p><div class="card" style="max-width:560px;margin:28px auto;padding:24px;text-align:left"><div class="status-pill">${sub.status==="cancelled"?"구독 해제":"구독 종료"}</div><h2 class="section-heading" style="margin-top:15px">이전 구독</h2><div class="pending-line"><span>관심 분야</span><strong>${(sub.current_settings?.categories||sub.settings?.categories||[]).map(categoryName).map(esc).join(" · ")||"-"}</strong></div><div class="pending-line"><span>구독 기간</span><strong>${DURATION_LABELS[sub.duration_days]||"-"}</strong></div><div class="pending-line"><span>마지막 구독 날짜</span><strong>${dateLabel(sub.last_delivery_date||sub.last_date)}</strong></div></div><div class="notice" style="max-width:420px;margin:0 auto 20px">다시 구독하면 다음 날부터 새 브리핑을 받아요.</div><button class="btn btn-primary" data-go="/privacy">새 구독 시작하기 <span>→</span></button><p><a href="/service" data-go="/service" class="small muted">서비스 소개 보기</a></p></section>`;}
function endedPage(){return shell(endedPageContent(currentSubscription||{}),"manage");}
function cancelModal(){return `<div class="modal-backdrop" role="presentation"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="cancel-title"><button class="modal-close" aria-label="닫기" data-action="close-modal">×</button><div style="font-size:48px;margin:0 auto 12px;color:var(--blue)">✉</div><h2 id="cancel-title">구독을 해제할까요?</h2><p>해제하면 이후 뉴스 메일 발송이 중단돼요.<br>이미 발송된 메일은 회수할 수 없어요.</p><p class="small" style="margin-top:13px">필요할 때 다시 구독할 수 있어요.</p><div class="modal-actions"><button class="btn btn-quiet" data-action="close-modal">계속 구독하기</button><button class="btn btn-danger" data-action="confirm-cancel">구독 해제하기</button></div></section></div>`;}

function feedbackPage(){const token=feedbackToken;return `<main class="feedback-page"><article class="feedback-shell"><div class="email-brand"><a class="brand" href="/" data-go="/"><span class="brand-mark" aria-hidden="true"></span>뉴스 브리핑</a><span>메일 수신 예시</span></div><h1 class="feedback-title">오늘의 브리핑</h1><div class="feedback-date">원문과 함께 읽는 뉴스 브리핑</div>${token?`<div id="feedback-status" class="notice blue" style="margin-top:16px">링크를 확인하고 있어요.</div>`:`<div class="notice" style="margin-top:16px">피드백 링크를 다시 열면 평가를 남길 수 있어요. 보안상 링크 토큰은 브라우저에 저장하지 않습니다.</div>`}<section class="news-card"><span class="card-label">오늘의 핵심</span><span class="ai-label">AI 생성</span><h2>관심 뉴스의 핵심을 이 카드에서 확인해요</h2><p>실제 뉴스 카드의 제목과 설명, 근거 시점은 검증된 기사 데이터에서 전달됩니다. 원문과 함께 내용을 확인해 주세요.</p><div class="notice blue">과거 정보는 근거 날짜 또는 보도일을 함께 표시합니다.</div><p class="small muted">출처와 기사 링크는 실제 브리핑 데이터로 채워집니다.</p></section><div class="center"><strong>오늘의 브리핑이 도움이 됐나요?</strong></div><div class="feedback-vote"><button class="vote" data-rating="up" disabled>👍 도움이 됐어요</button><button class="vote" data-rating="down" disabled>👎 아쉬웠어요</button></div><div id="feedback-reasons" class="hidden"><label class="form-label" for="reasons">어떤 점이 그랬나요? (선택)</label><div class="chips" style="padding:0;margin-bottom:12px">${["관심과 달라요","이해하기 어려워요","배경이 도움돼요","정확성이 걱정돼요"].map((x)=>`<label class="chip"><input type="checkbox" name="reason" value="${esc(x)}">${esc(x)}</label>`).join("")}</div><label class="form-label" for="feedback-comment">짧은 의견 (선택)</label><textarea id="feedback-comment" class="textarea" maxlength="500" placeholder="의견을 적어 주세요"></textarea><button class="btn btn-primary btn-block" style="margin-top:14px" data-action="submit-feedback">평가 제출하기</button></div><div class="divider"></div><p class="center"><a href="/manage" data-go="/manage" style="color:var(--blue)">웹사이트에서 구독 관리하기 →</a></p><p class="small muted center">AI가 기사 내용을 요약·설명했어요. 정확한 맥락은 원문을 확인해 주세요.</p></article></main>`;}

function servicePage(){return shell(`<section class="container page"><p class="eyebrow">서비스 소개</p><h1 class="page-title">뉴스 브리핑은<br>이렇게 만들어져요<span class="orange-dot">.</span></h1><p class="page-subtitle">관심 분야를 고르면, 원문 근거를 바탕으로 오늘의 핵심과 필요한 배경을 정리해 이메일로 전달합니다.</p><div class="feature-grid" style="margin-top:34px"><article class="card feature"><div class="feature-icon">01</div><h3>관심 분야와 키워드 선택</h3><p>검증되어 공개된 분야 중 관심 있는 주제를 골라 브리핑을 맞춥니다.</p></article><article class="card feature"><div class="feature-icon">02</div><h3>오늘의 핵심, 필요하면 과거 배경</h3><p>오늘 기사 한 장과, 관련된 과거 근거가 있을 때 배경 카드 한 장을 제공합니다.</p></article><article class="card feature"><div class="feature-icon">↗</div><h3>원문과 함께 확인</h3><p>AI 생성 설명과 기사 출처를 표시합니다. 모든 사실을 완전히 판별한다고 보장하지 않습니다.</p></article></div><div class="card" style="padding:26px;margin-top:22px"><h2 class="section-heading">이용 흐름</h2><p class="muted">Google 로그인 → 개인정보 안내와 동의 → 관심 분야·키워드·시간·기간 설정 → 다음 날부터 이메일 수신 → 웹에서 구독 관리와 피드백</p><div class="notice blue">구독 기간은 1주·2주·4주이며, 날짜 기준으로 운영됩니다. 메일 도착은 지연될 수 있습니다.</div></div><section class="card" style="padding:24px;margin-top:22px"><p class="eyebrow" style="margin-bottom:7px">카드 템플릿 미리보기</p><h2 class="section-heading">최대 입력 길이에도 읽기 좋게</h2><p class="small muted">아래는 연결 확인용 가상 데이터이며 실제 뉴스가 아닙니다.</p><div id="card-template-fixture" class="card-template-preview"></div></section><div class="center" style="margin-top:24px"><button class="btn btn-primary" data-action="start">구독 시작하기 →</button></div></section>`,"service");}

function render(){let page=pageFromPath();if(["privacy","setup","complete","manage","ended"].includes(page)&&!session)page="login";if(page==="setup"&&!setup.consent)page="privacy";if(page==="complete"&&currentSubscription?.status!=="active")page="manage";if(page==="feedback"){app.innerHTML=feedbackPage();wire();return;}if(page==="login")app.innerHTML=loginPage();else if(page==="privacy")app.innerHTML=privacyPage();else if(page==="setup")app.innerHTML=setupPage();else if(page==="complete")app.innerHTML=completePageContent();else if(page==="manage")app.innerHTML=shell(managePage(),"manage");else if(page==="ended")app.innerHTML=endedPage();else if(page==="service")app.innerHTML=servicePage();else app.innerHTML=homePage();if(page==="service"){const preview=document.querySelector("#card-template-fixture");if(preview)preview.innerHTML=renderCardTemplate(cardTemplateFixture);}wire();}
function completePageContent(){return completePage();}

function wire(){document.querySelectorAll("[data-go]").forEach((el)=>el.addEventListener("click",(e)=>{e.preventDefault();go(el.dataset.go);}));document.querySelectorAll("[data-category]").forEach((el)=>el.addEventListener("click",()=>{const id=el.dataset.category;setup.categories=setup.categories.includes(id)?setup.categories.filter((x)=>x!==id):[...setup.categories,id];render();}));document.querySelectorAll("[data-duration]").forEach((el)=>el.addEventListener("click",()=>{setup.duration_days=Number(el.dataset.duration);render();}));document.querySelectorAll("[data-remove-keyword]").forEach((el)=>el.addEventListener("click",()=>{setup.keywords.splice(Number(el.dataset.removeKeyword),1);render();}));document.querySelectorAll("[data-manage-category]").forEach((el)=>el.addEventListener("click",()=>{const id=el.dataset.manageCategory;draftSettings.categories=draftSettings.categories.includes(id)?draftSettings.categories.filter((x)=>x!==id):[...draftSettings.categories,id];render();}));document.querySelectorAll("[data-remove-manage-keyword]").forEach((el)=>el.addEventListener("click",()=>{draftSettings.keywords.splice(Number(el.dataset.removeManageKeyword),1);render();}));document.querySelectorAll("[data-rating]").forEach((el)=>el.addEventListener("click",()=>chooseRating(el.dataset.rating)));document.querySelectorAll("[data-action]").forEach((el)=>el.addEventListener("click",()=>onAction(el.dataset.action)));document.querySelector("#consent")?.addEventListener("change",(e)=>{setup.consent=e.target.checked;const button=document.querySelector('[data-action="consent-next"]');if(button)button.disabled=!setup.consent;});document.querySelector("#delivery-hour")?.addEventListener("change",(e)=>setup.delivery_hour_kst=Number(e.target.value));document.querySelector("#manage-hour")?.addEventListener("change",(e)=>draftSettings.delivery_hour_kst=Number(e.target.value));}

async function onAction(action){if(action==="menu"){isMenuOpen=!isMenuOpen;render();return;}if(action==="start"){if(session){go("/privacy");return;}go("/login");return;}if(action==="auth"){if(session){try{await firebaseAuth.signOut();session=null;currentSubscription=null;draftSettings=null;toast("로그아웃했어요.");await go("/");}catch{toast("로그아웃하지 못했습니다.");}}else go("/login");return;}if(action==="google-login"){
  if(loginPending)return;
  loginPending=true;
  try {
    const user=await firebaseAuth.signIn();session={user};
    await api("/users/sync",{method:"POST"});
    await refreshSubscription();
    await go(currentSubscription?.status==="active"?"/manage":"/privacy");
  } catch(error){pageError=error.status?error.message:(error.code?loginError(error):error.message);render();}
  finally {loginPending=false;}
  return;
}if(action==="consent-next"){setup.consent=Boolean(document.querySelector("#consent")?.checked);if(!setup.consent)return;go("/subscribe");return;}if(action==="add-keyword"){const input=document.querySelector("#keyword-input");addKeyword(input?.value,setup.keywords);if(input)input.value="";render();return;}if(action==="create-subscription"){await createSubscription();return;}if(action==="reload-subscription"){await reloadSubscription();return;}if(action==="open-cancel"){document.body.insertAdjacentHTML("beforeend",cancelModal());document.querySelector('[data-action="close-modal"]')?.focus();return;}if(action==="close-modal"){document.querySelector(".modal-backdrop")?.remove();return;}if(action==="confirm-cancel"){await cancelSubscription();return;}if(action==="submit-feedback"){await submitFeedback();}}

function addKeyword(raw,list){const value=(raw||"").normalize("NFKC").trim();const length=[...value].length;if(length<1||length>20){toast("키워드는 1~20자로 입력해 주세요.");return;}if(list.length>=5){toast("키워드는 최대 5개까지 추가할 수 있어요.");return;}if(list.includes(value)){toast("이미 추가한 키워드예요.");return;}list.push(value);}
function uuid(){return crypto.randomUUID?.()||`${Date.now()}-${Math.random().toString(16).slice(2)}`;}
async function createSubscription(){
  if(savingSubscription)return;
  if(!session){await go("/login");return;}
  if(!catalog.categories.length||setup.categories.some((id)=>!catalog.categories.includes(id))){toast("현재 선택 가능한 관심 분야를 확인해 주세요.");return;}
  if(!setup.categories.length){toast("관심 분야를 한 개 이상 선택해 주세요.");return;}
  if(!setup.consent){toast("개인정보 안내에 동의해 주세요.");return;}
  if(!catalog.consent_version){toast("동의 안내를 불러온 뒤 다시 시도해 주세요.");return;}
  const body={plan:"basic",engine_settings:{consent_version:catalog.consent_version,categories:setup.categories,keywords:setup.keywords,delivery_hour_kst:Number(document.querySelector("#delivery-hour")?.value??setup.delivery_hour_kst),duration_days:Number(setup.duration_days)}};
  const serialized=JSON.stringify(body);
  if(!subscriptionAttempt||subscriptionAttempt.payload!==serialized)subscriptionAttempt={key:uuid(),payload:serialized};
  savingSubscription=true;
  const button=document.querySelector('[data-action="create-subscription"]');
  if(button)button.disabled=true;
  try{
    await api("/users/sync",{method:"POST"});
    const data=await api("/subscriptions/save",{method:"POST",body,idempotencyKey:subscriptionAttempt.key});
    currentSubscription=normalizeSubscription(data);subscriptionAttempt=null;
    await go("/complete");
  }catch(error){pageError=error.message;render();}
  finally{savingSubscription=false;if(button)button.disabled=false;}
}
async function refreshSubscription(){
  currentSubscription=null;draftSettings=null;
  if(!session)return;
  try{const data=await api("/subscriptions/me");currentSubscription=normalizeSubscription(data);pageError="";}
  catch(error){pageError=error.status===404?"":error.message;}
}
async function reloadSubscription(){if(subscriptionReloading)return;subscriptionReloading=true;pageError="";render();try{await refreshSubscription();if(!pageError)toast(currentSubscription?.status?"구독 정보를 새로 불러왔어요.":"현재 등록된 구독이 없어요.");}finally{subscriptionReloading=false;render();}}
function normalizeSubscription(data){
  const sub=data?.subscription??data?.current??data;
  if(!sub?.status)return null;
  const first=sub.first_delivery_date||sub.start_date;
  const end=sub.end_date_exclusive;
  const last=end?new Date(new Date(`${end}T00:00:00+09:00`).getTime()-86400000).toLocaleDateString("sv-SE",{timeZone:"Asia/Seoul"}):null;
  const settings=sub.current_settings||sub.settings||{categories:sub.categories||[],keywords:sub.keywords||[],delivery_hour_kst:sub.delivery_hour_kst};
  return {...sub,current_settings:settings,first_delivery_date:first,last_delivery_date:sub.last_delivery_date||last,first_delivery_at:sub.first_delivery_at||(first&&Number.isInteger(settings.delivery_hour_kst)?`${first}T${String(settings.delivery_hour_kst).padStart(2,"0")}:00:00+09:00`:null)};
}
async function cancelSubscription(){
  try{
    const data=await api("/subscriptions/cancel",{method:"PATCH"});
    currentSubscription=normalizeSubscription(data);
    document.querySelector(".modal-backdrop")?.remove();
    await go("/ended");
  }catch(error){document.querySelector(".modal-backdrop")?.remove();pageError=error.message;render();}
}

function consumeFeedbackToken(){if(currentPath()!=="/feedback")return;const params=new URLSearchParams(location.hash.slice(1));feedbackToken=params.get("t");initialFeedbackRating=["up","down"].includes(params.get("rating"))?params.get("rating"):null;if(location.hash)history.replaceState(null,"",location.pathname+location.search);}
async function resolveFeedback(){if(!feedbackToken)return;try{const data=await api("/feedback/resolve",{method:"POST",body:{token:feedbackToken}});const status=document.querySelector("#feedback-status");if(!data?.valid){if(status)status.className="notice error";if(status)status.textContent="피드백 링크가 만료되었거나 유효하지 않습니다.";feedbackToken=null;return;}if(status){status.className="notice blue";status.textContent="브리핑을 확인했어요. 아래에서 평가를 선택해 주세요.";}document.querySelectorAll("[data-rating]").forEach((b)=>b.disabled=false);if(initialFeedbackRating)chooseRating(initialFeedbackRating);}catch(error){const status=document.querySelector("#feedback-status");if(status){status.className="notice error";status.textContent=error.message;}feedbackToken=null;}}
let chosenRating=null;
function chooseRating(value){chosenRating=value;document.querySelectorAll("[data-rating]").forEach((b)=>b.classList.toggle("selected",b.dataset.rating===value));document.querySelector("#feedback-reasons")?.classList.remove("hidden");}
async function submitFeedback(){if(!feedbackToken||!chosenRating){toast("평가를 선택해 주세요.");return;}const reasons=[...document.querySelectorAll('input[name="reason"]:checked')].map((el)=>el.value);const comment=document.querySelector("#feedback-comment")?.value||"";try{await api("/feedback",{method:"POST",body:{token:feedbackToken,rating:chosenRating,reasons,comment}});feedbackToken=null;app.innerHTML=`<main class="feedback-page"><section class="feedback-shell center"><div class="feature-icon" style="margin:0 auto 16px">✓</div><h1 class="page-title" style="font-size:32px">의견을 보내주셔서 감사합니다</h1><p class="page-subtitle">남겨주신 피드백은 브리핑을 개선하는 데 활용할게요.</p></section></main>`;}catch(error){toast(error.message);}}
function toast(message){document.querySelector(".toast")?.remove();const node=document.createElement("div");node.className="toast";node.setAttribute("role","status");node.textContent=message;document.body.append(node);setTimeout(()=>node.remove(),3600);}

async function init(){
  consumeFeedbackToken();
  if(pageFromPath()==="feedback"){render();await resolveFeedback();return;}
  if(BASE)await loadCatalog();
  await getSession();
  await renderRoute();
}
window.addEventListener("popstate",()=>{pageError="";void renderRoute();});
init();
