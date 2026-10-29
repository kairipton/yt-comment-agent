// 옵션 페이지. 서버(/api/options)에서 설정 값을 받아 칸으로 그리고, [저장]을 누르면 칸 값을 모아 보낸다.
// 판정(값이 맞는지)은 서버가 한다 — 화면은 그리기 · 모으기만. 옵션 설명의 주인은 이 파일이다(설정 파일에는 주석이 없다).
// 칸마다 get(값에서 꺼내기) · set(값에 넣기)을 함수로 둔다 — 설정 키 이름을 문자열로 흩어 두지 않으려고.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const state = { values: null, prompt: "", dirty: false, promptDirty: false };

// ── 칸 정의 ───────────────────────────────────────────────────────────────────
// kind: bool(스위치) · int · float · text · select(choices) · list(글 목록) · rows(여러 칸 줄 목록, cols)
// locked: 보여 주기만 하는 칸(꺼짐) — 서버도 저장 때 이 값을 바꾸지 않는다. 바꾸려면 설정 파일을 직접 고친다.
const SECTIONS = [
  {
    title: "동작 방식",
    fields: [
      { label: "지정 채널 영상에도 달기", kind: "bool", get: (v) => v.channel_posting, set: (v, x) => { v.channel_posting = x; },
        desc: "끄면 테스트 영상에만 달고, 지정 채널 영상은 초안까지만 봐요." },
      { label: "댓글 다는 방식", kind: "select", get: (v) => v.post_mode, set: (v, x) => { v.post_mode = x; },
        choices: [["manual", "직접 — 초안을 읽고 사람이 [댓글 달기]를 눌러요"], ["auto", "자동 — 목록에서 체크한 영상들에 차례로 달아요(하루 제한에 닿으면 멈춰요)"]],
        desc: "" },
    ],
  },
  {
    title: "크롬 프로필",
    desc: "댓글을 다는 유튜브 계정이에요. 프로필마다 크롬 창이 따로 있고, 각각 다른 구글 계정으로 로그인해 둡니다. 로그인은 대시보드의 [프로필 관리]에서 해요.",
    fields: [
      { label: "프로필 목록", kind: "rows", key: "profiles", get: (v) => v.profiles, set: (v, x) => { v.profiles = x; },
        cols: [["name", "이름", "화면에 보이는 이름"], ["user_data_dir", "크롬 정보 폴더", "예: C:/chrome-profiles/yt1"]],
        desc: "크롬 정보 폴더는 그 프로필의 로그인 정보가 저장되는 곳이에요(없으면 처음 열 때 만들어져요). 평소 쓰는 크롬 기본 프로필은 자동 조작이 막혀 있어 쓸 수 없으니, 꼭 별도 폴더를 지정하세요." },
      { label: "프로필 고르는 방식", kind: "select", get: (v) => v.profile_assign, set: (v, x) => { v.profile_assign = x; },
        choices: [["manual", "직접 — [댓글 달기] 위에서 골라요"], ["rotate", "돌아가며 — 로그인한 프로필 중 가장 오래 안 쓴 것으로 알아서 골라요"]],
        desc: "" },
    ],
  },
  {
    title: "영상 목록",
    fields: [
      { label: "채널", kind: "rows", key: "channels", get: (v) => v.channels, set: (v, x) => { v.channels = x; },
        cols: [["id", "채널 ID", "UC로 시작하는 24글자"], ["name", "이름", "화면에 보이는 이름"]],
        desc: "영상을 불러올 유튜브 채널이에요. 이 채널들의 영상에만 댓글을 달 수 있어요." },
      { label: "테스트 영상", kind: "list", key: "test_video_ids", get: (v) => v.test_video_ids, set: (v, x) => { v.test_video_ids = x; },
        placeholder: "영상 ID(주소의 v= 뒤 11글자)",
        desc: "연습용으로 쓰는 내 영상이에요. 이 영상에도 댓글을 달 수 있어요." },
      { label: "채널마다 불러올 영상 수", kind: "int", key: "fetch.per_channel", get: (v) => v.fetch.per_channel, set: (v, x) => { v.fetch.per_channel = x; },
        desc: "채널마다 가장 최근 영상을 몇 개 보여 줄지(1 이상)." },
      { label: "유튜브 응답 기다리는 시간(초)", kind: "float", key: "fetch.timeout_seconds", get: (v) => v.fetch.timeout_seconds, set: (v, x) => { v.fetch.timeout_seconds = x; },
        desc: "유튜브에서 정보를 받아 올 때 최대 몇 초까지 기다릴지." },
    ],
  },
  {
    title: "댓글 규칙",
    desc: "AI가 쓴 초안이 이 규칙을 어기면 이유를 붙여 다시 쓰게 하고, 끝내 못 맞추면 그 초안으로는 댓글을 달 수 없어요.",
    fields: [
      { label: "서비스 이름", kind: "text", key: "comment.brand", get: (v) => v.comment.brand, set: (v, x) => { v.comment.brand = x; },
        desc: "댓글에 꼭 들어가야 하는 이름이에요." },
      { label: "서비스 소개", kind: "text", key: "comment.brand_desc", get: (v) => v.comment.brand_desc, set: (v, x) => { v.comment.brand_desc = x; },
        desc: "AI에게 알려 주는 서비스 소개예요." },
      { label: "최소 글자 수", kind: "int", key: "comment.min_chars", get: (v) => v.comment.min_chars, set: (v, x) => { v.comment.min_chars = x; }, desc: "" },
      { label: "최소 글자 수를 셀 때", kind: "select", key: "comment.min_count", get: (v) => v.comment.min_count, set: (v, x) => { v.comment.min_count = x; },
        choices: [["no_spaces", "띄어쓰기 빼고"], ["with_spaces", "띄어쓰기 포함"]], desc: "" },
      { label: "최대 글자 수", kind: "int", key: "comment.max_chars", get: (v) => v.comment.max_chars, set: (v, x) => { v.comment.max_chars = x; }, desc: "" },
      { label: "최대 글자 수를 셀 때", kind: "select", key: "comment.max_count", get: (v) => v.comment.max_count, set: (v, x) => { v.comment.max_count = x; },
        choices: [["no_spaces", "띄어쓰기 빼고"], ["with_spaces", "띄어쓰기 포함"]], desc: "" },
      { label: "다른 댓글과 비슷해도 되는 정도", kind: "float", key: "comment.max_similarity", get: (v) => v.comment.max_similarity, set: (v, x) => { v.comment.max_similarity = x; },
        desc: "다른 영상에 쓴 댓글과 얼마나 비슷하면 거절할지(0~1). 낮을수록 엄격해요." },
      { label: "다시 쓰는 최대 횟수", kind: "int", key: "comment.max_rewrites", get: (v) => v.comment.max_rewrites, set: (v, x) => { v.comment.max_rewrites = x; },
        desc: "규칙에 안 맞는 초안을 AI가 다시 쓰는 최대 횟수예요." },
      { label: "영상 내용과 겹쳐야 하는 정도", kind: "int", key: "comment.grounding_min_overlap", get: (v) => v.comment.grounding_min_overlap, set: (v, x) => { v.comment.grounding_min_overlap = x; },
        desc: "댓글이 자막·제목·설명과 겹쳐야 하는 최소 정도(1 이상). 높을수록 엄격해요." },
      { label: "금지 표현", kind: "list", key: "comment.forbidden_phrases", get: (v) => v.comment.forbidden_phrases, set: (v, x) => { v.comment.forbidden_phrases = x; },
        placeholder: "예: 받아봤",
        desc: "서비스를 직접 이용해 본 것처럼 꾸며 쓴 표현이에요. 댓글에 이 중 하나라도 들어 있으면 거절해요. 「잘 본다던데」처럼 남에게 들은 말투나, 「친구가 다녀왔는데 잘 본대요」처럼 남의 경험을 전해 들은 말 속에 있으면 괜찮아요. 비워 둘 수 없어요." },
    ],
  },
  {
    title: "제한",
    fields: [
      { label: "하루에 달 수 있는 댓글 수", kind: "int", key: "limits.daily_max_comments", get: (v) => v.limits.daily_max_comments, set: (v, x) => { v.limits.daily_max_comments = x; },
        desc: "모든 프로필을 합친 수예요. 게시 버튼까지 누른 것만 세요." },
      { label: "실패하면 다시 해 보는 횟수", kind: "int", key: "limits.max_retries_per_post", get: (v) => v.limits.max_retries_per_post, set: (v, x) => { v.limits.max_retries_per_post = x; },
        desc: "게시 버튼을 누르기 전에 실패하면 알아서 다시 해 보는 횟수예요." },
      { label: "자동 방식에서 영상 사이 쉬는 시간(초)", kind: "int", key: "auto.gap_seconds", get: (v) => v.auto.gap_seconds, set: (v, x) => { v.auto.gap_seconds = x; },
        desc: "댓글 다는 방식이 「자동」일 때만 써요." },
    ],
  },
  {
    title: "뜨기 직전 영상 찾기",
    desc: "막 올라와 조회수가 빠르게 오르는 영상을 찾아 줘요. 찾은 영상은 초안까지만 쓰고 댓글은 달지 않아요. YouTube API 키가 있어야 쓸 수 있어요.",
    fields: [
      { label: "검색어", kind: "text", key: "rising.keyword", get: (v) => v.rising.keyword, set: (v, x) => { v.rising.keyword = x; }, desc: "" },
      { label: "몇 시간 안에 올라온 영상만", kind: "int", key: "rising.published_within_hours", get: (v) => v.rising.published_within_hours, set: (v, x) => { v.rising.published_within_hours = x; }, desc: "" },
      { label: "최소 조회수", kind: "int", key: "rising.min_views", get: (v) => v.rising.min_views, set: (v, x) => { v.rising.min_views = x; }, desc: "" },
      { label: "최대 조회수", kind: "int", key: "rising.max_views", get: (v) => v.rising.max_views, set: (v, x) => { v.rising.max_views = x; }, desc: "" },
      { label: "한 시간에 최소 조회수", kind: "float", key: "rising.min_views_per_hour", get: (v) => v.rising.min_views_per_hour, set: (v, x) => { v.rising.min_views_per_hour = x; },
        desc: "올라온 뒤 한 시간에 평균 이만큼 이상 조회된 영상만 보여 줘요." },
      { label: "[다시 측정] 버튼", kind: "bool", key: "rising.remeasure", get: (v) => v.rising.remeasure, set: (v, x) => { v.rising.remeasure = x; },
        desc: "켜면 조회수를 한 번 더 재서 최근에 빨리 오르는 순으로 보여 주는 버튼이 생겨요." },
      { label: "다시 측정까지 기다리는 시간(분)", kind: "int", key: "rising.remeasure_min_gap_minutes", get: (v) => v.rising.remeasure_min_gap_minutes, set: (v, x) => { v.rising.remeasure_min_gap_minutes = x; }, desc: "" },
    ],
  },
  {
    title: "AI 초안",
    fields: [
      { label: "AI 모델", kind: "text", locked: true, key: "llm.model", get: (v) => v.llm.model, set: (v, x) => { v.llm.model = x; },
        desc: "댓글 초안을 쓰는 AI 모델 이름이에요." },
      { label: "생각하는 깊이", kind: "select", locked: true, key: "llm.effort", get: (v) => v.llm.effort, set: (v, x) => { v.llm.effort = x; },
        choices: [["low", "낮음"], ["medium", "보통"], ["high", "높음"], ["xhigh", "아주 높음"], ["max", "최대"]],
        desc: "높을수록 꼼꼼하지만 느리고 비용이 늘어요." },
      { label: "영상을 고르자마자 초안 쓰기", kind: "bool", key: "llm.draft_on_select", get: (v) => v.llm.draft_on_select, set: (v, x) => { v.llm.draft_on_select = x; },
        desc: "끄면 [초안 쓰기]를 눌러야 써요(AI 비용 절약)." },
      { label: "댓글 작성 지침 파일", kind: "text", key: "llm.prompt_file", get: (v) => v.llm.prompt_file, set: (v, x) => { v.llm.prompt_file = x; },
        desc: "지침 글은 이 페이지 맨 아래에서 바로 고칠 수 있어요. 파일 위치를 바꿀 때만 고치세요." },
    ],
  },
  {
    title: "저장 위치",
    desc: "바꾸면 새 파일·폴더를 써요. 지금까지의 기록은 옮겨지지 않고 옛 위치에 남아요.",
    fields: [
      { label: "기록 파일", kind: "text", key: "storage.db_path", get: (v) => v.storage.db_path, set: (v, x) => { v.storage.db_path = x; },
        desc: "게시 기록과 초안을 저장하는 파일이에요." },
      { label: "화면 캡처 폴더", kind: "text", key: "storage.screenshot_dir", get: (v) => v.storage.screenshot_dir, set: (v, x) => { v.storage.screenshot_dir = x; },
        desc: "게시할 때 찍은 화면을 저장하는 폴더예요." },
    ],
  },
];
const FIELDS = SECTIONS.flatMap((s) => s.fields);
const PROMPT_HELP = { brand: "서비스 이름", brand_desc: "서비스 소개", min_chars: "최소 글자 수", max_chars: "최대 글자 수",
                      min_count: "최소 글자 수를 세는 법", max_count: "최대 글자 수를 세는 법", forbidden_examples: "금지 표현 예시" };

// ── 그리기 ────────────────────────────────────────────────────────────────────
function inputFor(f, i) {
  const html = inputHtml(f, i);
  // 꺼진 칸: 같은 칸을 그리되 disabled를 붙인다.
  return f.locked ? html.replace(/<(input|select)\b/, "<$1 disabled") : html;
}

function inputHtml(f, i) {
  const id = `f${i}`;
  const value = f.get(state.values);
  if (f.kind === "bool") {
    return `<label class="switch"><input type="checkbox" id="${id}" ${value ? "checked" : ""}><span>${value ? "켜짐" : "꺼짐"}</span></label>`;
  }
  if (f.kind === "select") {
    return `<select id="${id}">${f.choices.map(([v, t]) => `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
  }
  if (f.kind === "list") {
    const items = (value || []).map((x) => `<div class="list-row"><input type="text" value="${esc(x)}" placeholder="${esc(f.placeholder || "")}"><button class="text-btn" type="button" data-remove>삭제</button></div>`).join("");
    return `<div class="list-box" id="${id}">${items}</div><button class="btn small" type="button" data-add-list="${id}" data-placeholder="${esc(f.placeholder || "")}">+ 추가</button>`;
  }
  if (f.kind === "rows") {
    const row = (item) => `<div class="list-row">${f.cols.map(([k, , ph]) => `<input type="text" data-col="${k}" value="${esc(item?.[k] ?? "")}" placeholder="${esc(ph)}">`).join("")}<button class="text-btn" type="button" data-remove>삭제</button></div>`;
    const head = `<div class="list-row head">${f.cols.map(([, name]) => `<span>${esc(name)}</span>`).join("")}<span></span></div>`;
    return `<div class="list-box" id="${id}">${head}${(value || []).map(row).join("")}</div>
      <button class="btn small" type="button" data-add-rows="${id}" data-field="${i}">+ 추가</button>`;
  }
  const type = f.kind === "int" || f.kind === "float" ? "number" : "text";
  const step = f.kind === "float" ? ' step="any"' : f.kind === "int" ? ' step="1"' : "";
  return `<input type="${type}"${step} id="${id}" value="${esc(value ?? "")}">`;
}

function renderForm() {
  let i = 0;
  $("sections").innerHTML = SECTIONS.map((s) => `
    <section class="card option-card">
      <div class="option-head"><h2>${esc(s.title)}</h2>${s.desc ? `<p class="sub">${esc(s.desc)}</p>` : ""}</div>
      ${s.fields.map((f) => {
        const idx = FIELDS.indexOf(f);
        i += 1;
        return `<div class="option-field${f.kind === "list" || f.kind === "rows" ? " wide" : ""}" id="field-${idx}">
          <label class="option-label" for="f${idx}">${esc(f.label)}</label>
          <div class="option-input">${inputFor(f, idx)}</div>
          ${f.desc || f.locked ? `<p class="option-desc">${esc(f.desc)}${f.locked ? ' <span class="locked-note">설정 파일을 직접 고쳐야 바뀌어요.</span>' : ""}</p>` : ""}
        </div>`;
      }).join("")}
    </section>`).join("");
  setDirty(false);
}

// ── 모으기 ────────────────────────────────────────────────────────────────────
function collectValues() {
  const values = structuredClone(state.values);  // 원래 값을 바탕으로 — 화면에 없는 키와 순서를 지킨다
  FIELDS.forEach((f, i) => {
    if (f.locked) return;  // 꺼진 칸은 원래 값 그대로
    const el = $(`f${i}`);
    let x;
    if (f.kind === "bool") x = el.checked;
    else if (f.kind === "int") x = el.value === "" ? null : Number.parseInt(el.value, 10);
    else if (f.kind === "float") x = el.value === "" ? null : Number(el.value);
    else if (f.kind === "list") x = [...el.querySelectorAll("input")].map((inp) => inp.value.trim()).filter(Boolean);
    else if (f.kind === "rows") {
      x = [...el.querySelectorAll(".list-row:not(.head)")].map((row) =>
        Object.fromEntries([...row.querySelectorAll("input")].map((inp) => [inp.dataset.col, inp.value.trim()])))
        .filter((item) => Object.values(item).some(Boolean));
    } else x = el.value;
    f.set(values, x);
  });
  return values;
}

// ── 저장 ──────────────────────────────────────────────────────────────────────
async function send(path, body) {
  const res = await fetch(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, data };
}

// 서버의 이유 글은 「설정경로: 설명」 꼴이다(예: "comment.min_chars: comment.max_chars보다 작아야 해요", 목록이면 "channels[1]: …").
// 앞의 경로로 칸을 찾아 칸 이름(목록이면 「n번째 줄」까지)으로 바꾸고 칸을 붉게 표시한다 — 화면에 설정 이름이 보이지 않게.
// 설명 안의 다른 경로는 「칸 이름」으로, 목록 칸의 열 이름(name 등)은 그 열의 화면 이름으로 바꾼다.
// 경로를 못 알아보면(칸이 없는 설정) 서버 글을 그대로 보여 준다.
function explain(reasons) {
  document.querySelectorAll(".option-field.has-error").forEach((el) => el.classList.remove("has-error"));
  // 긴 경로부터 바꾼다 — 한 경로가 다른 경로의 앞부분일 때 짧은 것이 먼저 걸리지 않게.
  const keyed = FIELDS.filter((f) => f.key).sort((a, b) => b.key.length - a.key.length);
  return reasons.map((r) => {
    const m = r.match(/^([\w.]+)(?:\[(\d+)\])?: ([\s\S]*)$/);  // 1: 경로 · 2: 목록의 줄 번호(0부터) · 3: 설명
    const f = m && FIELDS.find((x) => x.key === m[1]);
    if (!f) return esc(r);
    $(`field-${FIELDS.indexOf(f)}`).classList.add("has-error");
    let detail = m[3];
    for (const g of keyed) detail = detail.split(g.key).join(`「${g.label}」`);
    for (const [col, name] of f.cols || []) detail = detail.replace(new RegExp(`\\b${col}\\b`, "g"), `「${name}」`);
    const row = m[2] === undefined ? "" : ` ${Number(m[2]) + 1}번째 줄`;
    return `<b>${esc(f.label + row)}</b> — ${esc(detail)}`;
  });
}

async function save() {
  $("save-ok").hidden = true;
  $("save-error").hidden = true;
  $("btn-save").disabled = true;
  try {
    const values = collectValues();
    const { ok, data } = await send("/api/options", { values });
    if (ok) {
      state.values = values;
      setDirty(false);
      $("save-ok").textContent = "저장했어요. 대시보드에 바로 반영돼요.";
      $("save-ok").hidden = false;
    } else {
      $("save-error").innerHTML = `<p>${esc(data.detail || "저장하지 못했어요.")}</p><ul>${explain(data.reasons || []).map((r) => `<li>${r}</li>`).join("")}</ul>`;
      $("save-error").hidden = false;
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  } finally {
    $("btn-save").disabled = false;
  }
}

async function savePrompt() {
  $("prompt-ok").hidden = true;
  $("prompt-error").hidden = true;
  const text = $("prompt-text").value;
  const { ok, data } = await send("/api/options/prompt", { text });
  if (ok) {
    state.prompt = text;
    state.promptDirty = false;
    $("prompt-ok").textContent = "지침을 저장했어요. 다음 초안부터 쓰여요.";
    $("prompt-ok").hidden = false;
  } else {
    $("prompt-error").innerHTML = `<p>${esc(data.detail || "저장하지 못했어요.")}</p><ul>${(data.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`;
    $("prompt-error").hidden = false;
  }
}

function setDirty(on) {
  state.dirty = on;
  $("dirty-hint").textContent = on ? "바꾼 것이 있어요 — [저장]을 눌러야 반영돼요." : "바뀐 것이 없어요.";
  $("dirty-hint").classList.toggle("blocked", on);
}

// ── 연결 ──────────────────────────────────────────────────────────────────────
async function load() {
  const res = await fetch("/api/options");
  const data = await res.json();
  if (!res.ok) {
    $("save-error").innerHTML = `<p>${esc(data.detail || "옵션을 불러오지 못했어요.")}</p><ul>${(data.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`;
    $("save-error").hidden = false;
    return;
  }
  state.values = data.values;
  state.prompt = data.prompt;
  renderForm();
  $("prompt-text").value = data.prompt;
  $("prompt-fields").innerHTML = "지침 안에서 쓸 수 있는 자리표시(실제 값으로 바뀌어요): "
    + data.prompt_fields.map((n) => `<code>{${esc(n)}}</code> ${esc(PROMPT_HELP[n] || "")}`).join(" · ");
}

function wire() {
  $("sections").addEventListener("input", (e) => {
    if (e.target.type === "checkbox") e.target.nextElementSibling.textContent = e.target.checked ? "켜짐" : "꺼짐";
    setDirty(true);
  });
  $("sections").addEventListener("click", (e) => {
    const remove = e.target.closest("[data-remove]");
    if (remove) { remove.parentElement.remove(); setDirty(true); return; }
    const addList = e.target.closest("[data-add-list]");
    if (addList) {
      $(addList.dataset.addList).insertAdjacentHTML("beforeend",
        `<div class="list-row"><input type="text" placeholder="${esc(addList.dataset.placeholder)}"><button class="text-btn" type="button" data-remove>삭제</button></div>`);
      setDirty(true);
      return;
    }
    const addRows = e.target.closest("[data-add-rows]");
    if (addRows) {
      const f = FIELDS[Number(addRows.dataset.field)];
      $(addRows.dataset.addRows).insertAdjacentHTML("beforeend",
        `<div class="list-row">${f.cols.map(([k, , ph]) => `<input type="text" data-col="${k}" placeholder="${esc(ph)}">`).join("")}<button class="text-btn" type="button" data-remove>삭제</button></div>`);
      setDirty(true);
    }
  });
  $("btn-save").onclick = save;
  $("btn-reset").onclick = () => { renderForm(); $("save-error").hidden = true; };
  $("prompt-text").addEventListener("input", () => { state.promptDirty = true; });
  $("btn-prompt-save").onclick = savePrompt;
  $("btn-prompt-reset").onclick = () => { $("prompt-text").value = state.prompt; state.promptDirty = false; };
  // 저장하지 않고 떠나려 하면 묻는다.
  window.addEventListener("beforeunload", (e) => {
    if (state.dirty || state.promptDirty) e.preventDefault();
  });
}

wire();
load();
