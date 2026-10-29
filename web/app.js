// 대시보드 화면 동작. 서버(app.py)의 /api/* 만 부르고, 판정은 하지 않는다 — 받은 결과를 그리기만 한다.
// 흐름: ① 불러오기 → 영상 클릭 → ② 초안 받아 표시(없으면 AI가 그 자리에서 씀)
//       → ③ 띠의 [댓글 달기] → 진행 상태를 0.5초마다 물어 갱신 → 끝나면 머리 표시줄·기록 새로고침
// 배치(B안): 세 칸은 늘 셋 다 보이고 지금 칸만 펼친다. 어느 칸을 펼칠지는 activeStep()이 화면 상태로만 정한다.

// polling: 게시 진행 확인 타이머(누른 순간부터 "starting"), postVideo: 그 게시의 영상 — 다른 영상을 보는 중엔 결과를 그리지 않는다.
// view: 사용자가 [다른 영상으로 바꾸기]로 ①을 다시 펼쳤으면 1. postShown: 지금 영상의 ③ 진행·결과를 펼쳐 보이는 중인가.
const state = { videos: [], selected: null, draft: null, status: null, polling: null, postVideo: null,
                checked: new Set(), autoPolling: null, tab: "all", profile: null, view: null, postShown: false,
                lastRoot: null, drafting: false };  // drafting: ②가 초안을 받는 중(이때는 [댓글 달기]를 끈다)
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const CHECK_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l5 5L20 7"/></svg>';
const LOCK_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';
const initials = (name) => (name ? `${[...name][0]}${[...name].slice(-1)[0]}` : "");
const chars = (t) => [...(t || "")];  // 서버 문지기(파이썬)와 같게 코드포인트 단위로 센다

// 서버 오류는 이유(detail)를 담은 Error로 던진다. 설정 파일 오류면 맨 위 빨간 띠도 띄운다.
async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (body.reasons) showConfigAlert(body);
    throw new Error(body.detail || `요청 실패 (${res.status})`);
  }
  return body;
}

function showConfigAlert(body) {
  $("config-alert").hidden = false;
  $("config-alert").innerHTML = `${esc(body.detail)}<ul>${body.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`;
}

// ── 머리 표시줄 ──────────────────────────────────────────────────────────────
// 개발 모드의 [초기화]: 오늘 게시 수를 0부터 다시 센다. 게시 기록은 지워지지 않는다.
async function resetQuota() {
  if (!window.confirm("오늘 게시 수를 0부터 다시 셀까요? 게시 기록은 지워지지 않아요.")) return;
  try {
    await api("/api/quota/reset", { method: "POST" });
    await loadStatus();
  } catch (e) {
    window.alert(e.message);
  }
}

async function loadStatus() {
  const s = (state.status = await api("/api/status"));
  const left = Math.max(0, s.daily_max - s.today);  // 오늘 게시는 게시 버튼을 누른 시도만 센다(서버 값 그대로)
  $("quota").innerHTML = `오늘 남은 댓글 <b>${left}개</b> / ${s.daily_max}`;
  $("quota").classList.toggle("empty", left === 0);
  $("quota").title = "모든 프로필 합산. 게시 버튼을 누른 시도만 셉니다.";
  $("quota-reset").hidden = !s.dev_mode;  // 하루 상한은 운영 규칙이라 개발 모드에서만 초기화할 수 있다(서버도 막는다)
  const chips = [
    ["게시 모드", s.post_mode === "manual" ? "수동" : "자동", "수동: 초안을 보고 [댓글 달기]를 눌러 게시 / 자동: 체크한 영상들을 차례로 게시", false],
    ["게시 대상", s.channel_posting ? "지정 채널 + 테스트 영상" : "테스트 영상만", "지금 댓글을 달 수 있는 영상이에요. 다른 영상도 초안은 볼 수 있어요.", false],
    ["AI 키", s.ai_key ? "설정됨" : "설정 안 됨", "AI 키가 없으면 댓글 초안을 만들 수 없어요.", false, !s.ai_key],
    ["YouTube 키", s.youtube_key ? "설정됨" : "없음 · 인기 댓글 제외", "YouTube 키가 없으면 인기 댓글 없이 초안을 쓰고, 뜨기 직전 영상은 찾을 수 없어요.", !s.youtube_key, false],
  ];
  // 개발 모드는 켜져 있을 때만 알린다 — 꺼져 있으면 보이지 않게.
  if (s.dev_mode) chips.splice(1, 0, ["개발 모드", "켜짐", "테스트 영상에는 같은 영상에 여러 번 달 수 있어요(영상당 1개 예외). 오늘 남은 댓글 [초기화]도 이때만 보여요. 실제로 쓸 때는 꺼요.", true]);
  $("status-chips").innerHTML = chips
    .map(([k, v, tip, warn, danger]) =>
      `<span class="chip${warn ? " warn" : ""}${danger ? " danger" : ""}" title="${esc(tip)}">${k}<b>${esc(v)}</b></span>`)
    .join("");
  // 프로필: 고를 수 있는 것은 로그인함 프로필뿐(서버의 profiles). 기본은 첫 프로필, 새로고침해도 고른 것 유지.
  if (!s.profiles.includes(state.profile)) state.profile = s.profiles[0] ?? null;
  renderProfiles();
  updateAutoButton();
  const r = s.rising;  // 뜨기 직전 기준 — 화면에 숫자를 박아 두지 않고 설정을 따른다
  $("rising-hint").textContent = `「${r.keyword}」로 최근 ${r.hours}시간 영상을 찾아 조회수 ${r.min_views.toLocaleString()}~${r.max_views.toLocaleString()} · 시간당 ${r.min_per_hour.toLocaleString()} 이상만 보여 줘요. 초안까지만 만들어요.`;
  $("btn-rising").disabled = !s.youtube_key;
  $("btn-rising").title = s.youtube_key ? "" : "YouTube API 키가 있어야 검색할 수 있어요.";
}

const rotate = () => state.status?.profile_assign === "rotate";
const postingProfile = () => (rotate() ? state.status?.next_profile : state.profile);

// 프로필 로그인 상태 — 서버가 크롬 쿠키로 확인한 값(login)을 화면 말로 바꾼다. 화면은 상태를 판정하지 않는다.
const LOGIN = {
  logged_in: { text: "로그인되어 있음", cls: "on" },
  needs_login: { text: "로그인 필요", cls: "off" },
  window_open: { text: "크롬 창 열림 — 닫으면 확인해요", cls: "open" },
};
const loginOf = (p) => LOGIN[p.login] ?? LOGIN.needs_login;

// ③ 띠의 프로필 줄: 직접 고르기면 버튼, 자동 배정이면 서버가 알려 준 다음 차례.
// 고를 수 있는 것은 서버가 준 profiles(지금 로그인되어 있는 프로필)뿐이다. 나머지는 상태를 붙여 흐리게 보이고,
// 누르면 고르는 대신 [프로필 관리]를 펼쳐 로그인 방법을 보여 준다.
function renderProfiles() {
  const s = state.status;
  if (!s) return;
  $("profile-buttons").hidden = rotate();
  $("profile-auto").hidden = !rotate();
  $("profile-auto").textContent = !rotate() ? ""
    : s.next_profile ? `자동 배정 — 이번엔 ${s.next_profile} (로그인한 프로필 ${s.profiles.length}개를 돌아가며)`
    : "로그인한 프로필이 없어요 — [프로필 관리]에서 로그인해 주세요";
  $("profile-buttons").innerHTML = s.all_profiles.map((p) => s.profiles.includes(p.name)
    ? `<button class="profile-btn" type="button" data-profile="${esc(p.name)}" aria-pressed="${p.name === state.profile}">${esc(p.name)}</button>`
    : `<button class="profile-btn need-login" type="button" data-manage="1" title="로그인하는 방법 보기">${esc(p.name)} · ${loginOf(p).cls === "open" ? "크롬 창 열림" : "로그인 필요"}</button>`).join("");
  $("profile-count").textContent = `프로필 관리 · 로그인 ${s.profiles.length}개`;
  renderPreviewWho();
  renderProfilePanel();
}

// ── 프로필 관리 ──────────────────────────────────────────────────────────────
// 프로필마다 로그인 상태와 [크롬 열기](상태와 상관없이 늘 — 로그인할 때도, 확인할 때도 쓴다).
// 상태는 2초마다 서버에 물어 저절로 바뀐다 — 로그인하고 창을 닫으면 「로그인되어 있음」이 된다.
function renderProfilePanel() {
  const s = state.status;
  $("profile-rows").innerHTML = s.all_profiles.map((p) => `
    <li class="profile-row">
      <span class="name">${esc(p.name)}</span>
      <span class="state ${loginOf(p).cls}">${loginOf(p).text}</span>
      <span class="actions"><button class="btn small" type="button" data-open="${esc(p.name)}">크롬 열기</button></span>
    </li>`).join("");
}

function toggleProfilePanel(open) {
  $("profile-panel").hidden = !open;
  $("profile-count").setAttribute("aria-expanded", String(open));
  if (open) $("profile-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function openProfile(btn) {
  $("profile-error").hidden = true;
  btn.disabled = true;
  try {
    await api(`/api/profiles/${encodeURIComponent(btn.dataset.open)}/open`, { method: "POST" });
  } catch (e) {
    $("profile-error").textContent = e.message;
    $("profile-error").hidden = false;
  } finally {
    btn.disabled = false;
  }
}

// 로그인 상태 감시 — 2초마다 묻고, 바뀌었을 때만 다시 그린다. 서버와 끊기면 다음 차례에 다시 묻는다(알림은 띄우지 않음).
async function watchLogins() {
  if (!state.status) return;
  let r;
  try {
    r = await api("/api/profiles");
  } catch {
    return;
  }
  if (JSON.stringify(r.all_profiles) === JSON.stringify(state.status.all_profiles)) return;
  state.status.all_profiles = r.all_profiles;
  state.status.profiles = r.profiles;
  if (!r.profiles.includes(state.profile)) state.profile = r.profiles[0] ?? null;  // 고른 프로필이 로그아웃·창 열림이면 다른 것으로
  if (rotate()) await loadStatus();  // 자동 배정의 「이번엔 …」은 서버가 다시 정한다
  renderProfiles();
  updatePostButton();
}

// ── ① 영상 불러오기 ──────────────────────────────────────────────────────────
async function loadVideos() {
  $("btn-load").disabled = true;
  $("btn-load").textContent = "불러오는 중…";
  try {
    const { videos, failures } = await api("/api/videos/load", { method: "POST" });
    state.videos = videos;
    pruneChecked();
    $("list-hint").textContent = `${videos.length}개를 불러왔어요. 영상을 눌러 고르세요.`;
    $("list-failures").hidden = failures.length === 0;
    $("list-failures").innerHTML = failures.map((f) => `못 불러온 곳 — ${esc(f)}`).join("<br>");
    renderVideos();
  } finally {
    $("btn-load").disabled = false;
    $("btn-load").textContent = "다시 불러오기";
  }
}

// 종류 탭 — 화면 안에서만 거른다(서버가 준 source). 링크로 넣은 영상은 「전체」에만 보인다.
// 테스트 영상은 연습용이라 맨 오른쪽에 둔다.
const TABS = [["all", "전체", () => true], ["channel", "지정 채널", (v) => v.source === "channel"],
              ["rising", "뜨기 직전", (v) => v.source === "rising"], ["test", "테스트 영상", (v) => v.source === "test"]];

function renderTabs() {
  $("tabs").innerHTML = TABS.map(([key, name, keep]) =>
    `<button class="tab" type="button" role="tab" data-tab="${key}" aria-selected="${state.tab === key}">${name}<span class="count">${state.videos.filter(keep).length}</span></button>`).join("");
}

const viewCount = (v) => v.view_count ?? v.views ?? null;  // 목록의 조회수, 뜨기 직전이면 그때 잰 값

function renderVideos() {
  renderTabs();
  const keep = TABS.find(([key]) => key === state.tab)[2];
  $("video-list").innerHTML = state.videos.filter(keep)
    .map((v) => `
      <li class="video${v.id === state.selected ? " selected" : ""}${v.posted ? " dim" : ""}" data-id="${esc(v.id)}">
        ${autoMode() ? `<input type="checkbox" class="pick" data-id="${esc(v.id)}" ${v.postable ? "" : "disabled"} ${state.checked.has(v.id) ? "checked" : ""} title="${v.postable ? "자동으로 달 영상으로 체크" : esc(v.block_reason)}" aria-label="자동으로 달 영상으로 체크">` : ""}
        ${v.id === state.selected ? '<span class="chosen">골랐어요</span>' : ""}
        <img src="${esc(v.thumbnail)}" alt="">
        <div class="title">${esc(v.title)}</div>
        <p class="meta">${esc(v.channel)}${v.date ? ` · ${esc(v.date)}` : ""}</p>
        <div class="badges">
          ${v.test ? '<span class="badge test">테스트 영상</span>' : ""}
          ${v.link ? '<span class="badge link">링크로 추가</span>' : ""}
          ${v.per_hour != null ? `<span class="badge rising">뜨기 직전 · 조회수 ${v.views.toLocaleString()} · 시간당 ${v.per_hour.toLocaleString()}${v.recent_per_hour != null ? ` · 최근 시간당 ${v.recent_per_hour.toLocaleString()}` : ""}</span>` : ""}
          ${v.posted ? '<span class="badge">이미 달았어요</span>' : ""}
        </div>
        ${v.postable ? "" : `<p class="block">${esc(v.block_reason)} · 초안은 볼 수 있어요</p>`}
      </li>`)
    .join("") || '<li class="hint">이 종류의 영상이 아직 없어요.</li>';
}

const selectedVideo = () => state.videos.find((x) => x.id === state.selected);
const autoMode = () => state.status?.post_mode === "auto";

// 목록에서 사라진 영상의 체크를 지운다 — 남아 있으면 자동 실행이 「영상을 먼저 불러와 주세요」로 통째로 실패한다.
function pruneChecked() {
  const ids = new Set(state.videos.filter((v) => v.postable).map((v) => v.id));
  state.checked = new Set([...state.checked].filter((id) => ids.has(id)));
  updateAutoButton();
}

// ── 세 칸 펼치기 ──────────────────────────────────────────────────────────────
// 판정이 아니라 화면 상태다: 영상을 안 골랐거나 ①을 다시 열었으면 1, ③을 눌러 진행·결과를 보는 중이면 3, 그 밖엔 2.
function activeStep() {
  if (state.view === 1 || !state.selected) return 1;
  return state.postShown ? 3 : 2;
}

function renderSteps() {
  const step = activeStep();
  const v = selectedVideo();
  // ① — 지금 칸이 아니면 고른 영상 요약 띠
  $("body1").hidden = step !== 1;
  $("bar1").hidden = step === 1 || !v;
  if (v) {
    $("bar1-thumb").src = v.thumbnail;
    $("bar1-text").innerHTML = `${esc(v.title)} <span class="hint">· ${esc(v.channel)}</span>`;
  }
  // ② — 아직이면 점선 띠, 지나갔으면 ✓ 띠
  $("body2").hidden = step !== 2;
  $("bar2").hidden = step === 2;
  const d = state.draft;
  $("bar2").className = step === 3 ? "bar done" : "bar";
  $("bar2").innerHTML = step === 3
    ? `<span class="mark ok" aria-hidden="true">${CHECK_SVG}</span><span class="bar-title">영상 선택</span>
       <span class="bar-text hint clip">${d && d.ai && !d.pending ? `${d.version}번째 초안 — ${esc(d.comment)}` : ""}</span>`
    : `<span class="mark dash">2</span><span class="bar-title">영상 선택</span>
       <span class="bar-text">영상을 고르면 열려요 — AI가 정리한 고민 한 줄과 댓글 초안</span>`;
  // ③ — ②를 보는 동안은 「프로필 + [댓글 달기]」 작은 칸(클릭을 늘리지 않는다), 그 전엔 점선 띠
  $("bar3-wait").hidden = step !== 1;
  $("bar3-ready").hidden = step !== 2;
  $("body3").hidden = step !== 3;
  // 단계 표시 — 지난 칸은 done, 지금 칸은 now
  for (const li of $("stepper").children) {
    const n = Number(li.dataset.step);
    li.classList.toggle("done", n < step);
    li.classList.toggle("now", n === step);
    if (n === step) li.setAttribute("aria-current", "step");
    else li.removeAttribute("aria-current");
  }
}

function openStep1() {
  changeSteps(() => {
    state.view = 1;
    renderSteps();
  }, $("step1"));
}

// ── 칸 바꾸기 애니메이션 ────────────────────────────────────────────────────
// 칸을 바꾸면 화면 구성이 크게 바뀐다. 한꺼번에 바꾸지 않고 두 박자로 보여 준다:
//   1박자 — 지금 펼쳐진 칸이 위로 접힌다(높이 · 투명도가 줄어든다). 동시에 화면을 scrollTo 칸으로 부드럽게 올린다.
//   2박자 — 새로 펼칠 칸이 아래로 펼쳐진다.
// ①이 접혔으면 끝에 「고른 영상」 띠를 잠깐 반짝여 「목록은 여기 접혀 있다」를 알린다.
// 「동작 줄이기」 설정이면 그냥 바로 바꾼다.
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const FOLD_MS = 380;
let stepQueue = Promise.resolve();  // 애니메이션 중에 또 누르면 앞의 것이 끝난 뒤 차례로 돈다

// update: 화면 상태를 바꾸는 함수(renderSteps까지). scrollTo: 올려 보일 칸(없으면 스크롤 안 함).
// 돌려주는 약속은 두 박자가 다 끝난 뒤 풀린다.
function changeSteps(update, scrollTo) {
  stepQueue = stepQueue.then(() => runSteps(update, scrollTo)).catch((e) => console.error(e));
  return stepQueue;
}

async function runSteps(update, scrollTo) {
  const openBodies = () => ["body1", "body2", "body3"].map($).filter((el) => !el.hidden);
  if (reduceMotion.matches || document.hidden) {  // 동작 줄이기 설정 · 숨긴 탭이면 애니메이션 없이
    update();
    scrollTo?.scrollIntoView({ block: "start" });
    return;
  }
  const wasStep1 = activeStep() === 1;
  const closing = openBodies();
  scrollTo?.scrollIntoView({ block: "start", behavior: "smooth" });  // 접히는 동안 함께 올라간다
  await Promise.all(closing.map((el) => fold(el)));                  // 1박자: 접기
  update();
  for (const el of closing) {  // 접힌 칸은 이제 hidden — 붙잡아 둔 「높이 0」을 풀어 다음에 다시 펼칠 수 있게
    el.getAnimations().forEach((a) => a.cancel());
    el.style.overflow = "";
  }
  const opening = openBodies().filter((el) => !closing.includes(el));
  await Promise.all(opening.map((el) => unfold(el)));                // 2박자: 펼치기
  if (wasStep1 && activeStep() !== 1) flash($("bar1"));
}

// 브라우저가 그림을 멈춘 상태(뒤로 간 탭 등)면 애니메이션이 끝나지 않아 화면이 멈춘다 —
// 정해진 시간이 지나면 끝 모습으로 건너뛴다.
function settle(anim) {
  const timer = setTimeout(() => anim.finish(), FOLD_MS + 300);
  return anim.finished.finally(() => clearTimeout(timer));
}

// 칸을 위로 접는다. 끝난 모습(높이 0)을 붙잡아 둔다 — 그사이 update가 칸을 숨긴다.
function fold(el) {
  el.style.overflow = "hidden";
  return settle(el.animate(
    [{ height: `${el.offsetHeight}px`, opacity: 1 },
     { height: "0px", opacity: 0, paddingTop: "0px", paddingBottom: "0px", borderTopWidth: "0px", borderBottomWidth: "0px" }],
    { duration: FOLD_MS, easing: "cubic-bezier(.4, 0, .2, 1)", fill: "forwards" }));
}

// 칸을 아래로 펼친다(높이 0 → 원래 높이). 끝나면 높이는 다시 내용에 맞게 늘고 준다.
function unfold(el) {
  el.style.overflow = "hidden";
  return settle(el.animate(
    [{ height: "0px", opacity: 0, paddingTop: "0px", paddingBottom: "0px", borderTopWidth: "0px", borderBottomWidth: "0px" },
     { height: `${el.offsetHeight}px`, opacity: 1 }],
    { duration: FOLD_MS, easing: "cubic-bezier(.2, 0, 0, 1)" })).then(() => { el.style.overflow = ""; });
}

function flash(el) {
  el.classList.remove("flash");
  void el.offsetWidth;  // 다시 반짝일 수 있게 애니메이션을 처음부터 다시 걸기
  el.classList.add("flash");
}

// ── 자동 모드 ────────────────────────────────────────────────────────────────
// 체크한 영상을 서버가 차례로 돌린다. 화면은 1초마다 상태를 물어 차례 목록을 그린다(판정은 서버·로봇).
function updateAutoButton() {
  $("auto-box").hidden = !autoMode();
  const running = state.autoPolling !== null;
  $("btn-auto").disabled = running || state.checked.size === 0 || !state.status?.ai_key;
  $("btn-auto").textContent = `체크한 영상 ${state.checked.size}개 자동으로 달기`;
  $("btn-auto-stop").hidden = !running;
  updatePostButton();  // 자동 실행 중엔 [댓글 달기]를 끈다
}

// 새로고침·재접속 때 돌고 있는 자동 실행을 다시 붙잡는다 — [멈추기]를 잃지 않게.
async function resumeAuto() {
  const job = await api("/api/auto");
  if (job.items.length === 0) return;
  renderAuto(job);
  if (!job.finished && state.autoPolling === null) {
    state.autoPolling = setInterval(pollAuto, 1000);
    updateAutoButton();
  }
}

async function startAuto() {
  const body = { video_ids: [...state.checked], profile: rotate() ? null : state.profile };
  renderAuto(await api("/api/auto", { method: "POST", body: JSON.stringify(body) }));
  state.autoPolling = setInterval(pollAuto, 1000);
  updateAutoButton();
}

async function pollAuto() {
  let job;
  try {
    job = await api("/api/auto");
  } catch (e) {
    clearInterval(state.autoPolling);
    state.autoPolling = null;
    $("auto-hint").textContent = `서버와 연결이 끊겼어요 — 하던 게시는 게시 로봇이 마쳐요. (${e.message})`;
    updateAutoButton();
    return;
  }
  renderAuto(job);
  if (job.finished) {
    clearInterval(state.autoPolling);
    state.autoPolling = null;
    updateAutoButton();
    await Promise.all([loadStatus(), loadHistory()]);
  }
}

function renderAuto(job) {
  $("auto-list").innerHTML = job.items.map((i) => `
    <li><b>${esc(i.state)}</b> ${esc(i.title)}
      ${i.reason ? `<span class="hint">— ${esc(i.reason)}</span>` : ""}
      ${i.comment_url ? ` <a href="${esc(i.comment_url)}" target="_blank" rel="noopener">댓글 보기 ↗</a>` : ""}</li>`).join("");
  if (job.stopping && !job.finished) $("auto-hint").textContent = "멈추는 중 — 지금 하던 영상까지만 해요.";
}

// 뜨기 직전 영상 찾기. 결과는 목록 맨 위로 — 지난 찾기 결과는 바꿔 끼운다. 게시 대상이 아니라 초안까지만.
async function findRising(path) {
  const btn = path.endsWith("remeasure") ? $("btn-remeasure") : $("btn-rising");
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "찾는 중…";
  try {
    const r = await api(path, { method: "POST" });
    // 같은 영상이 목록에도 있으면 한 줄로 — 뜨기 직전 영상은 지정 채널 영상이어도 게시하지 않는다(서버도 그렇게 본다).
    const found = new Set(r.videos.map((v) => v.id));
    state.videos = [...r.videos, ...state.videos.filter((v) => v.source !== "rising" && !found.has(v.id))];
    pruneChecked();
    $("rising-hint").textContent = r.videos.length
      ? `검색 ${r.searched}개 중 ${r.matched}개가 기준에 들어요${path.endsWith("remeasure") ? " — 최근 속도 순" : " — 평균 속도 순"}. 초안까지만 만들 수 있어요.`
      : `검색 ${r.searched}개 중 기준에 드는 영상이 없어요.`;
    $("btn-remeasure").hidden = !r.remeasure || r.videos.length === 0;
    state.tab = "rising";  // 찾은 결과를 바로 보여 준다
    renderVideos();
  } catch (e) {
    $("rising-hint").textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

// 링크로 영상 하나를 더한다. 넣은 영상은 목록 맨 위로 들어가고 바로 골라진다.
// 게시할 수 있는지는 서버가 준 postable을 그대로 쓴다 — 허용 채널 밖이면 [댓글 달기]가 꺼진다.
async function addLink(e) {
  e.preventDefault();
  const url = $("link-input").value.trim();
  if (!url) return;
  $("btn-link").disabled = true;
  $("link-error").hidden = true;
  try {
    const v = await api("/api/videos/link", { method: "POST", body: JSON.stringify({ url }) });
    state.videos = [v, ...state.videos.filter((x) => x.id !== v.id)];
    $("link-input").value = "";
    state.tab = "all";  // 링크로 넣은 영상은 「전체」에만 보인다
    await selectVideo(v.id);
  } catch (err) {
    $("link-error").textContent = err.message;
    $("link-error").hidden = false;
  } finally {
    $("btn-link").disabled = false;
  }
}

// ── ② 영상 선택 · 초안 ───────────────────────────────────────────────────────
async function selectVideo(id) {
  // ①을 접고 ②를 펼친 뒤에 초안을 받는다. 펼치는 동안은 「쓰는 중」을 보여 옛 영상의 초안이 비치지 않게 한다.
  await changeSteps(() => {
    state.selected = id;
    state.draft = null;
    state.view = null;
    state.postShown = false;
    renderVideos();
    resetPost();
    renderSteps();
    showDraftLoading();
  }, $("step1"));  // ①은 이제 「고른 영상」 띠 한 줄 — 띠와 ②가 함께 보이게 ①부터 올린다
  await fetchDraft(() => api(`/api/videos/${id}/draft`));
}

// ② 칸을 「AI가 쓰는 중」으로 — 옛 초안 · 오류 · 안내를 가리고 버튼을 끈다.
// AI 키가 있으면 완성 화면의 틀을 먼저 보여 준다: 영상 정보는 이미 아니까 채우고, AI가 쓸 자리(고민 · 댓글)는
// 회색 줄로 비워 둔다. 초안이 오면 그 자리에 글만 들어와서 화면이 「빡」 바뀌지 않는다. 키가 없으면 한 줄 안내.
function showDraftLoading() {
  $("key-alert").hidden = true;
  $("d-error").hidden = true;
  $("d-pending").hidden = true;
  setDraftButtons(true);
  const v = selectedVideo();
  const frame = Boolean(v && state.status?.ai_key);
  $("d-loading").hidden = frame;
  $("detail").hidden = !frame;
  if (!frame) return;
  fillVideoHeader(v);
  $("d-worry").innerHTML = '<span class="skeleton"></span><span class="skeleton short"></span>';
  $("d-version").textContent = "";
  $("d-comment").innerHTML = '<span class="skeleton"></span><span class="skeleton"></span><span class="skeleton short"></span>';
  $("d-facts").innerHTML = '<span class="writing">AI가 영상을 읽고 초안을 쓰는 중이에요… (10~20초)</span>';
  renderPreviewWho();
  renderDraftNotice();  // 게시 대상이 아닌 영상이면 그 안내는 미리 보여 준다(초안과 상관없는 이유)
}

// ② 머리의 영상 정보 — 초안을 기다리는 틀과 완성 화면이 같이 쓴다.
function fillVideoHeader(v) {
  $("d-thumb").src = v.thumbnail;
  $("d-thumb-link").href = `https://www.youtube.com/watch?v=${encodeURIComponent(v.id)}`;
  $("d-title").textContent = v.title;
  const views = viewCount(v);
  $("d-meta").textContent = [v.channel, v.date, views != null ? `조회수 ${views.toLocaleString()}회` : ""].filter(Boolean).join(" · ");
}

// 칸 안의 내용이 바뀔 때 높이가 「빡」 바뀌지 않게, 바뀌기 전 높이에서 바뀐 뒤 높이로 부드럽게 잇는다.
function smoothResize(el, change) {
  const from = el.offsetHeight;
  change();
  if (reduceMotion.matches || document.hidden || el.hidden) return;
  const to = el.offsetHeight;
  if (Math.abs(to - from) < 2) return;
  el.style.overflow = "hidden";
  settle(el.animate([{ height: `${from}px` }, { height: `${to}px` }], { duration: 320, easing: "cubic-bezier(.2, 0, 0, 1)" }))
    .then(() => { el.style.overflow = ""; });
}

// 글이 들어온 자리를 서서히 나타나게 한다(같은 칸이 여러 번 바뀌어도 매번 처음부터).
function fadeIn(...els) {
  for (const el of els) {
    el.classList.remove("fade-in");
    void el.offsetWidth;
    el.classList.add("fade-in");
  }
}

// 초안을 받아 오는 동안 「쓰는 중」을, 실패하면 이유와 [다시 시도]를 보여 준다.
async function fetchDraft(request) {
  const video = state.selected;  // 받는 사이 다른 영상을 고르면, 이 요청의 결과·오류는 그리지 않는다
  smoothResize($("body2"), showDraftLoading);
  // 새 초안을 받는 동안 [댓글 달기]를 끈다 — 화면엔 기다리는 틀이 보이는데 옛 초안이 게시되는 엇갈림을 막는다.
  state.drafting = true;
  updatePostButton();
  try {
    const draft = await request();
    if (state.selected !== video) return;
    setDraftButtons(false);
    smoothResize($("body2"), () => showDraft(draft));
  } catch (e) {
    if (state.selected !== video) return;
    setDraftButtons(false);
    smoothResize($("body2"), () => {
      $("detail").hidden = true;  // 비워 둔 틀은 거두고 이유만
      $("d-error-text").textContent = e.message;
      $("d-error").hidden = false;
    });
  } finally {
    if (state.selected === video) {
      $("d-loading").hidden = true;
      state.drafting = false;  // 다른 영상으로 넘어갔으면 그 영상의 요청이 끌 때까지 둔다
    }
    updatePostButton();
  }
}

function setDraftButtons(busy) {
  $("btn-rewrite").disabled = busy;
}

function renderPreviewWho() {
  const who = postingProfile();
  $("d-avatar").textContent = initials(who);
  $("d-who").innerHTML = who ? `<b>@${esc(who)}</b> · 방금 전` : "";
}

function showDraft(draft) {
  if (draft.video_id !== state.selected) return;  // 그 사이 다른 영상을 골랐다
  state.draft = draft;
  // AI 키가 없으면 초안 대신 경고를 크게 보여 주고, 게시로 가지 못하게 한다.
  $("key-alert").hidden = draft.ai;
  // 고르자마자 쓰기가 꺼져 있으면 초안 대신 [초안 쓰기] — 누를 때만 AI를 부른다.
  $("d-pending").hidden = !draft.pending;
  $("detail").hidden = !draft.ai || Boolean(draft.pending);
  if (!draft.ai || draft.pending) return;
  const v = selectedVideo();
  if (!v) return;  // 그 사이 목록을 다시 불러와 이 영상이 빠졌다
  fillVideoHeader(v);
  $("d-worry").textContent = `“${draft.worry}”`;
  $("d-version").textContent = `· ${draft.version}번째 초안`;
  $("d-comment").textContent = draft.comment;
  renderPreviewWho();

  // 댓글 아래 한 줄: 글자 수 · 규칙 통과 · AI가 읽은 양. 전부 서버가 준 값을 보여 주기만 한다(판정은 서버의 문지기).
  const c = state.status.comment;
  const way = (m) => (m === "no_spaces" ? "공백 제외" : "공백 포함");
  const noSpace = chars(draft.comment.replace(/\s/g, "")).length;
  const withSpace = chars(draft.comment).length;
  const countTip = `공백 제외 ${noSpace}자 · 공백 포함 ${withSpace}자 (기준: ${way(c.min_count)} ${c.min_chars}자 이상 · ${way(c.max_count)} ${c.max_chars}자 이하)`;
  const t = draft.transcript_chars;
  const n = draft.comments_read;
  // 둘 다 없으면 이 값을 남기기 전에 쓴 옛 초안 — 그 부분은 생략한다.
  const read = t == null && n == null ? ""
    : t ? `자막 ${t.toLocaleString()}자${n != null ? `와 인기 댓글 ${n}개` : ""}를 읽고 썼어요`
    : "자막이 없어 제목·설명으로 썼어요";
  $("d-facts").innerHTML = [
    `<span title="${esc(countTip)}">${withSpace}자</span>`,
    draft.issues.length === 0 ? '<span class="pass">✓ 댓글 규칙 통과</span>' : "",
    read ? `<span>${esc(read)}</span>` : "",
  ].filter(Boolean).join("");
  renderDraftNotice();
  fadeIn($("d-worry"), $("d-comment"), $("d-facts"));  // 회색 줄 자리에 글이 서서히 들어온다
}

// 댓글을 달 수 없을 때 ② 위에 이유를 크게 — 게시 대상이 아니거나(뜨기 직전 · 허용 채널 밖 등) 규칙에 떨어진 초안.
// 이유 글은 서버가 준 것(block_reason · issues)을 그대로 쓴다.
function renderDraftNotice() {
  const v = selectedVideo();
  const d = state.draft;
  const box = $("d-notice");
  if (v && !v.postable) {
    box.innerHTML = `<b>이 영상에는 댓글을 달 수 없어요</b><p>${esc(v.block_reason)} 초안은 참고로 볼 수 있어요.</p>`;
  } else if (d && d.ai && !d.pending && d.issues.length > 0) {
    box.innerHTML = `<b>규칙에 맞지 않는 초안이에요</b><ul>${d.issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul><p>[다시 써 주세요]를 눌러 새로 받아 주세요.</p>`;
  } else {
    box.hidden = true;
    return;
  }
  box.hidden = false;
}

async function rewriteDraft() {
  resetPost();
  await fetchDraft(() => api(`/api/videos/${state.selected}/rewrite`, { method: "POST" }));
}

// ── ③ 댓글 달기 ──────────────────────────────────────────────────────────────
// 버튼을 켜는 근거는 서버가 준 값뿐이다: AI 초안이 있고 · 규칙 검사를 통과했고 · 그 영상이 게시 대상일 때.
// 별도 [승인]은 없다 — 초안을 읽고 이 버튼을 누르는 것이 곧 결정이다.
// 「이미 단 영상」「오늘 상한」은 켜 둔다 — 누르면 서버가 거부하고 그 이유가 진행 칸과 기록에 남는다.
function updatePostButton() {
  const v = selectedVideo();
  const d = state.draft;
  const blocked = v && !v.postable;
  const pending = d && d.pending;
  const failed = d && d.ai && !pending && d.issues.length > 0;
  const ready = d && d.ai && !pending && !failed && !blocked;
  const autoRunning = state.autoPolling !== null;
  $("btn-post").disabled = !ready || state.drafting || state.polling !== null || autoRunning || (!rotate() && !state.profile);
  $("post-hint").textContent = autoRunning ? "자동 실행이 도는 동안에는 직접 달 수 없어요."
    : state.polling !== null ? "게시가 진행 중이에요 — 끝나면 다시 누를 수 있어요."
    : !state.selected ? "②에서 영상을 고르면 버튼이 켜져요."
    : state.drafting ? "AI가 초안을 쓰는 중이에요 — 다 쓰면 버튼이 켜져요."
    : blocked ? `이 영상에는 게시할 수 없어요 — ${v.block_reason}`
    : d && !d.ai ? "AI API 키가 설정되지 않아 댓글을 달 수 없어요."
    : pending ? "②에서 [초안 쓰기]를 누르면 초안이 나와요."
    : failed ? "초안이 규칙 검사를 통과하지 못했어요 — ②에서 [다시 쓰기]를 눌러 주세요."
    : !ready ? "②에서 초안이 나오면 버튼이 켜져요."
    : !rotate() && !state.profile ? "로그인한 프로필이 없어 댓글을 달 수 없어요."
    : "누르면 크롬이 열리고 한 글자씩 타이핑해요. 그 프로필의 크롬 창은 미리 닫아 주세요.";
  $("post-hint").classList.toggle("blocked", Boolean(state.selected && (blocked || failed || (d && !d.ai))));
  $("btn-again").disabled = $("btn-post").disabled;
}

function resetPost() {
  for (const id of ["post-done", "post-fail", "post-blocked", "post-retry", "p-typed-box"]) $(id).hidden = true;
  updatePostButton();
}

async function startPost() {
  if (state.polling !== null) return;  // 빠르게 두 번 눌러도 시도는 하나 — 누르는 순간 막는다
  resetPost();
  state.polling = "starting";
  state.postVideo = state.selected;
  updatePostButton();
  let attempt_id;
  try {
    ({ attempt_id } = await api("/api/posts", {
      method: "POST",
      // 자동 배정이면 프로필을 보내지 않는다 — 서버가 정한다.
      body: JSON.stringify({ video_id: state.selected, profile: rotate() ? null : state.profile }),
    }));
  } catch (e) {
    state.polling = null;
    updatePostButton();
    throw e;  // 이유는 알림으로 보인다
  }
  state.lastRoot = attempt_id;
  if (state.selected === state.postVideo) {
    // 화면이 바뀐 뒤에 진행을 묻기 시작한다 — 먼저 온 진행 응답을 첫 단계 그림이 덮어쓰지 않게.
    await changeSteps(() => {
      state.postShown = true;  // ③을 펼쳐 진행을 보여 준다
      renderPost({ tone: "progress", steps: ["프로필 여는 중", "입력 중", "게시 버튼 누름", "내 댓글 확인 중"], step: 0,
                   typed: 0, total: 0, text: "", profile: postingProfile(), retry_no: 0 });
      renderSteps();
    });
  }
  state.polling = setInterval(() => pollPost(attempt_id), 500);
  updatePostButton();
  pollPost(attempt_id);
}

// 서버에 진행 상태를 물어 단계 표시를 갱신한다. 끝나면 물어보기를 멈추고 머리·기록을 새로 읽는다.
async function pollPost(attemptId) {
  let p;
  try {
    p = await api(`/api/posts/${attemptId}`);
  } catch (e) {
    // 서버 창을 닫았거나 연결이 끊겼다 — 0.5초마다 알림이 뜨지 않게 물어보기를 멈추고 한 번만 알린다.
    if (state.polling === null) return;  // 겹친 요청이 이미 멈췄다
    clearInterval(state.polling);
    state.polling = null;
    if (state.selected === state.postVideo) showDisconnected(e);
    updatePostButton();
    return;
  }
  const here = state.selected === state.postVideo;  // 다른 영상을 보는 중이면 진행·결과를 그 칸에 그리지 않는다
  if (here) renderPost(p);
  $("post-retry").hidden = !here || p.retry_no === 0 || p.done;
  $("post-retry").textContent = `게시 버튼 전에 실패해서 자동으로 다시 하는 중 (${p.retry_no}/${p.max_retries})`;
  if (!p.done) return;

  if (state.polling === null) return;  // 겹친 요청이 이미 마무리했다
  clearInterval(state.polling);
  state.polling = null;
  $("post-retry").hidden = true;
  updatePostButton();
  await Promise.all([loadStatus(), loadHistory()]);  // 목록의 「이미 달았어요」 표시는 다시 불러올 때 갱신된다
}

// ③ 펼친 칸 — 색과 문구는 서버가 준 tone 하나로 고른다(끝남·손봐야·막힘·진행). 상태를 다시 나누지 않는다.
const TONE = { done: "끝났어요", fix: "손봐야 해요", blocked: "막혔어요" };

function renderPost(p) {
  const tone = p.tone;
  const afterSubmit = p.status === "failed_after_submit";  // 버튼은 눌렀다 — 실제로 달렸을 수 있다
  $("body3").className = `card current tone-${tone}`;
  $("p-badge").className = `num tone-${tone}`;
  $("p-badge").innerHTML = tone === "done" ? CHECK_SVG : tone === "fix" ? "!" : tone === "blocked" ? LOCK_SVG : "3";
  $("p-tone").hidden = tone === "progress";
  $("p-tone").className = `tone-pill ${tone}`;
  $("p-tone").textContent = TONE[tone] || "";
  $("p-title").textContent = tone === "done" ? "댓글을 달았어요"
    : tone === "fix" ? (afterSubmit ? "게시는 눌렀지만 확인하지 못했어요" : "댓글을 달지 못했어요")
    : tone === "blocked" ? (p.previous ? "이 영상엔 이미 댓글을 달았어요" : "게시 전 검사에서 막혔어요")
    : "댓글 다는 중";
  $("p-sub").textContent = tone === "done" ? `${p.profile}로 게시하고, 내 댓글이 영상 아래 보이는 것까지 확인했어요`
    : tone === "fix" ? (afterSubmit ? "게시 버튼은 눌렀어요 — 유튜브에서 달렸는지 확인해 주세요"
      : `게시 버튼을 누르기 전에 멈췄어요${p.retry_no ? ` — 알아서 ${p.retry_no}번 더 해 봤어요` : ""}`)
    : tone === "blocked" ? "크롬은 열지 않았어요"
    : `${p.profile}로 다는 중 — 크롬 창에서 진행 모습이 그대로 보여요. 창을 닫으면 멈춰요`;

  // 진행 막대 4칸(게시 완료 단계는 막대가 다 차는 것으로 보인다). 손봐야면 멈춘 칸을 표시한다 —
  // 버튼 후 실패는 「확인」에서, 버튼 전 실패는 친 글자가 있으면 「입력」, 없으면 「열기」에서 멈춘 것이다.
  const stopped = tone === "fix" ? (afterSubmit ? 3 : p.typed > 0 ? 1 : 0) : -1;
  $("progress").innerHTML = p.steps.slice(0, 4).map((name, i) => {
    const cls = tone === "done" ? "done" : tone === "blocked" ? ""
      : tone === "fix" ? (i < stopped ? "done" : i === stopped ? "stopped" : "")
      : i < p.step ? "done" : i === p.step ? "active" : "";
    return `<li class="${cls}">${esc(name)}${cls === "stopped" ? " — 여기서 멈춤" : ""}</li>`;
  }).join("");

  // 크롬에 입력되는 글 — 친 부분은 진하게, 남은 부분은 흐리게(코드포인트 단위).
  const showTyped = (tone === "progress" && p.step >= 1) || (tone === "fix" && !afterSubmit && p.typed > 0);
  $("p-typed-box").hidden = !showTyped;
  if (showTyped) {
    const all = chars(p.text);
    const typed = p.step >= 2 && tone === "progress" ? all.length : p.typed;
    $("p-typed-label").textContent = `${tone === "fix" ? "멈추기 전까지 입력한 글" : "지금 크롬에 입력되는 글"} · 공백 포함 ${p.total}자 중 ${typed}자`;
    $("p-avatar").textContent = initials(p.profile);
    $("p-typed").textContent = all.slice(0, typed).join("");
    $("p-rest").textContent = all.slice(typed).join("");
    $("p-caret").hidden = tone !== "progress" || typed >= all.length;
  }

  $("post-done").hidden = tone !== "done";
  if (tone === "done") {
    $("done-avatar").textContent = initials(p.profile);
    $("done-who").innerHTML = `<b>@${esc(p.profile)}</b> · 방금 전`;
    $("done-text").textContent = p.text;
  }

  $("post-fail").hidden = tone !== "fix";
  if (tone === "fix") {
    $("fail-what-title").textContent = afterSubmit ? "게시 버튼은 눌렀어요" : "댓글은 달리지 않았어요";
    $("fail-what").textContent = afterSubmit
      ? "실제로 달렸을 수 있어요. 유튜브에서 직접 확인해 주세요. 오늘 상한에는 셌어요."
      : `게시 버튼을 누르기 전에 멈췄어요. 오늘 상한에도 세지 않아요. ${p.screenshot ? "캡처를 보고 " : ""}원인을 고친 뒤 다시 달아 보세요.`;
    $("post-fail-reason").textContent = p.reason || "";
    $("post-fail-shot-link").hidden = !p.screenshot;
    $("post-fail-noshot").hidden = Boolean(p.screenshot);
    if (p.screenshot) {
      $("post-fail-shot").src = p.screenshot;
      $("post-fail-shot-link").href = p.screenshot;
    }
  }

  $("post-blocked").hidden = tone !== "blocked";
  if (tone === "blocked") {
    $("blocked-reason").textContent = p.reason || "";
    const prev = p.previous;
    $("prev-box").hidden = !prev;
    if (prev) {
      $("prev-label").textContent = `먼저 단 댓글 · ${prev.time} · ${prev.profile}`;
      $("prev-avatar").textContent = initials(prev.profile);
      $("prev-text").textContent = prev.comment;
      $("prev-link").hidden = !prev.comment_url;
      if (prev.comment_url) $("prev-link").href = prev.comment_url;
    }
  }

  // 아래 버튼 줄 — 진행 중엔 없음.
  const show = { "btn-view": tone === "done", "btn-next": tone === "done",
                 "btn-again": tone === "fix" && !afterSubmit, "btn-other": tone === "fix" || tone === "blocked",
                 "btn-history": (tone === "fix" && afterSubmit) || tone === "blocked" };
  for (const [id, on] of Object.entries(show)) $(id).hidden = !on;
  $("btn-other").className = tone === "blocked" ? "btn primary big" : "btn";
  if (tone === "done") $("btn-view").href = p.comment_url;
  $("p-actions").classList.toggle("empty", !Object.values(show).some(Boolean));
}

function showDisconnected(e) {
  renderPost({ tone: "fix", status: "failed_before_submit", steps: [...$("progress").querySelectorAll("li")].map((li) => li.textContent.replace(" — 여기서 멈춤", "")),
               step: -1, typed: 0, total: 0, text: "", profile: postingProfile(), retry_no: 0, screenshot: null, reason: e.message });
  $("p-title").textContent = "서버와 연결이 끊겼어요";
  $("p-sub").textContent = "하던 게시는 게시 로봇이 끝까지 마쳐요";
  $("fail-what-title").textContent = "결과는 게시 기록에서";
  $("fail-what").textContent = "run_dashboard.bat으로 다시 연 뒤 게시 기록에서 결과를 확인해 주세요.";
  $("post-fail-noshot").hidden = true;
  $("btn-again").hidden = true;
  $("post-retry").hidden = true;
}

// ── 기록 ──────────────────────────────────────────────────────────────────────
async function loadHistory() {
  const rows = await api("/api/history");
  $("history").innerHTML = rows.length === 0
    ? '<tr><td colspan="6" class="hint">아직 기록이 없어요.</td></tr>'
    : rows.map((h, i) => `
      <tr class="${i === 0 && h.root_id === state.lastRoot ? "latest" : ""}">
        <td>${esc(h.time)}</td>
        <td class="wrap">${esc(h.video)}</td>
        <td>${esc(h.profile)}</td>
        <td class="comment">${esc(h.comment)}</td>
        <td class="result ${esc(h.tone)}">${esc(h.label)}${h.retry_no ? ` (재시도 ${h.retry_no})` : ""}</td>
        <td class="wrap">${h.comment_url ? `<a href="${esc(h.comment_url)}" target="_blank" rel="noopener">댓글 보기 ↗</a>` : ""}
          ${esc(h.reason)}${h.screenshot ? ` <a href="${esc(h.screenshot)}" target="_blank" rel="noopener">캡처</a>` : ""}</td>
      </tr>`).join("");
}

// ── 연결 ──────────────────────────────────────────────────────────────────────
function wire() {
  $("btn-load").onclick = loadVideos;
  $("link-form").onsubmit = addLink;
  $("btn-rising").onclick = () => findRising("/api/rising/search");
  $("btn-remeasure").onclick = () => findRising("/api/rising/remeasure");
  $("tabs").onclick = (e) => {
    const tab = e.target.closest("[data-tab]");
    if (!tab) return;
    state.tab = tab.dataset.tab;  // 탭을 바꿔도 자동 모드 체크는 남는다
    renderVideos();
  };
  $("video-list").onclick = (e) => {
    if (e.target.classList.contains("pick")) {  // 체크칸은 고르기가 아니라 자동 모드 차례에 넣기/빼기
      e.target.checked ? state.checked.add(e.target.dataset.id) : state.checked.delete(e.target.dataset.id);
      updateAutoButton();
      return;
    }
    const item = e.target.closest(".video");
    if (item) selectVideo(item.dataset.id);
  };
  $("profile-buttons").onclick = (e) => {
    if (e.target.closest("[data-manage]")) return toggleProfilePanel(true);  // 로그인 전 프로필 → 로그인 방법
    const btn = e.target.closest("[data-profile]");
    if (!btn || state.polling !== null) return;
    state.profile = btn.dataset.profile;
    renderProfiles();
    updatePostButton();
  };
  $("profile-count").onclick = () => toggleProfilePanel($("profile-panel").hidden);
  $("quota-reset").onclick = resetQuota;
  $("profile-panel-close").onclick = () => toggleProfilePanel(false);
  $("profile-rows").onclick = (e) => {
    const btn = e.target.closest("[data-open]");
    if (btn) openProfile(btn);
  };
  $("btn-change").onclick = openStep1;  // 게시 중이어도 게시는 끊기지 않는다 — 결과는 기록에 남는다
  $("btn-other").onclick = openStep1;
  $("btn-next").onclick = () => {
    state.selected = null;
    state.draft = null;
    state.postShown = false;
    renderVideos();
    openStep1();
  };
  $("btn-again").onclick = startPost;
  $("btn-auto").onclick = startAuto;
  $("btn-auto-stop").onclick = async () => renderAuto(await api("/api/auto/stop", { method: "POST" }));
  $("btn-rewrite").onclick = rewriteDraft;
  $("btn-write").onclick = rewriteDraft;  // 첫 초안 쓰기 — 서버의 다시 쓰기가 초안이 없으면 1번째로 쓴다
  $("btn-retry-draft").onclick = () => selectVideo(state.selected);
  $("btn-post").onclick = startPost;
}

// 오류는 조용히 삼키지 않고 화면에 알린다.
window.addEventListener("unhandledrejection", (e) => alert(e.reason?.message || "알 수 없는 오류"));

wire();
renderTabs();
renderSteps();
loadStatus().then(() => Promise.all([loadHistory(), resumeAuto()]));
setInterval(watchLogins, 2000);  // 프로필 로그인 상태를 계속 지켜본다
