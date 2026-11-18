// HanaMedia Short - background orchestrator (MV3 service worker).
// One task at a time: claim from the native host -> fill Flow -> ask host (pre_submit) -> click Generate ->
// confirm a new tile -> watch -> download -> hand the file to the host.
// Anything unexpected BEFORE pre_submit returns the scene to READY. Anything unexpected AFTER it is reported
// as unknown_submit / blocked and the scene is never re-sent automatically.

const HOST = "com.hanamedia.short";
const FLOW = "https://flow.google.com/";
let port = null;
let seq = 0;
const pending = new Map();
let running = false;     // a task is in progress
let autoRun = false;     // keep claiming tasks
let status = { text: "Chưa kết nối", at: Date.now() };
let expectDownload = null; // {token, resolve, since}

function setStatus(text, extra = {}) {
  status = { text, at: Date.now(), ...extra };
  chrome.storage.session.set({ status });
  chrome.action.setBadgeText({ text: extra.badge || "" });
  chrome.runtime.sendMessage({ type: "status", status }).catch(() => {});
}

// ---------------- native host ----------------
function connect() {
  if (port) return port;
  port = chrome.runtime.connectNative(HOST);
  port.onMessage.addListener((m) => {
    const p = pending.get(m.id);
    if (p) { pending.delete(m.id); p(m); }
  });
  port.onDisconnect.addListener(() => {
    const err = chrome.runtime.lastError && chrome.runtime.lastError.message;
    port = null;
    for (const [, p] of pending) p({ ok: false, error: "host disconnected: " + err });
    pending.clear();
    setStatus("Mất kết nối native host: " + (err || ""), { badge: "ERR" });
  });
  return port;
}

function host(msg, timeoutMs = 30000) {
  return new Promise((resolve) => {
    const id = ++seq;
    const t = setTimeout(() => { pending.delete(id); resolve({ ok: false, error: "host timeout" }); }, timeoutMs);
    pending.set(id, (m) => { clearTimeout(t); resolve(m); });
    try { connect().postMessage({ ...msg, id }); }
    catch (e) { clearTimeout(t); pending.delete(id); resolve({ ok: false, error: String(e) }); }
  });
}

async function hello() {
  const { activeToken } = await chrome.storage.session.get("activeToken");
  const r = await host({ cmd: "hello", active_token: activeToken || null });
  if (r.ok) setStatus(r.blocked ? `Đang bị chặn: ${r.blocked.reason}` : "Đã kết nối host", { badge: r.blocked ? "!" : "", blocked: r.blocked, recovered: r.recovered });
  else setStatus("Không kết nối được host: " + r.error, { badge: "ERR" });
  return r;
}

const log = (text, level = "info") => host({ cmd: "log", text, level });

// ---------------- tab & page calls ----------------
async function flowTab(projectUrl) {
  const tabs = await chrome.tabs.query({ url: ["https://flow.google.com/*", "https://labs.google/fx/*"] });
  let tab = tabs.find((t) => projectUrl && t.url && t.url.startsWith(projectUrl)) ||
            tabs.find((t) => /\/project\//.test(t.url || "")) || tabs[0];
  if (!tab) tab = await chrome.tabs.create({ url: projectUrl || FLOW, active: true });
  else if (projectUrl && !(tab.url || "").startsWith(projectUrl)) await chrome.tabs.update(tab.id, { url: projectUrl });
  await waitTabComplete(tab.id);
  return tab.id;
}

function waitTabComplete(tabId, timeout = 45000) {
  return new Promise((resolve) => {
    const end = Date.now() + timeout;
    const tick = async () => {
      const t = await chrome.tabs.get(tabId).catch(() => null);
      if (!t || t.status === "complete" || Date.now() > end) return resolve(t);
      setTimeout(tick, 500);
    };
    tick();
  });
}

async function call(tabId, name, ...args) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
  const [res] = await chrome.scripting.executeScript({
    target: { tabId }, func: (n, a) => window.__hana[n](...a), args: [name, args],
  });
  return res && res.result;
}

// Runs in the PAGE (main) world: Flow creates its <input type=file> on click and immediately calls
// input.click(). We suppress the file chooser, keep the input and assign our files to it instead.
async function mainWorldUpload(refs) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const vis = (e) => e && e.getBoundingClientRect().width > 0;
  const add = [...document.querySelectorAll("button")].find((b) =>
    ["Thêm thành phần vào ô nhập câu lệnh", "Add ingredients to prompt", "Add to prompt"].includes(b.getAttribute("aria-label")));
  if (!add) return { ok: false, detail: "Không thấy nút + (thêm thành phần)" };
  add.click();
  await sleep(700);
  const up = [...document.querySelectorAll(".cdk-overlay-pane button")].find((b) => /Tải nội dung nghe nhìn lên|Upload media|Upload/i.test(b.innerText) && vis(b));
  if (!up) return { ok: false, detail: "Không thấy nút 'Tải nội dung nghe nhìn lên'" };
  let captured = null;
  const origClick = HTMLInputElement.prototype.click;
  const origPicker = HTMLInputElement.prototype.showPicker;
  HTMLInputElement.prototype.click = function () { if (this.type === "file") { captured = this; return; } return origClick.call(this); };
  HTMLInputElement.prototype.showPicker = function () { if (this.type === "file") { captured = this; return; } return origPicker && origPicker.call(this); };
  try { up.click(); await sleep(600); }
  finally { HTMLInputElement.prototype.click = origClick; HTMLInputElement.prototype.showPicker = origPicker; }
  captured = captured || document.querySelector('input[type=file][accept*=".png"]');
  if (!captured) return { ok: false, detail: "Không bắt được input[type=file]" };
  const dt = new DataTransfer();
  for (const r of refs) {
    const bin = Uint8Array.from(atob(r.b64), (c) => c.charCodeAt(0));
    dt.items.add(new File([bin], r.filename, { type: r.mime }));
  }
  captured.files = dt.files;
  captured.dispatchEvent(new Event("input", { bubbles: true }));
  captured.dispatchEvent(new Event("change", { bubbles: true }));
  await sleep(500);
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  return { ok: true, files: refs.length };
}

// ---------------- downloads ----------------
chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  if (expectDownload && /flow\.google\.com|googleusercontent|googleapis|storage\.googleapis/.test(item.url + item.referrer)) {
    const ext = (item.filename.match(/\.[a-z0-9]+$/i) || [".mp4"])[0];
    suggest({ filename: `HanaFlow/${expectDownload.title}${ext}`, conflictAction: "uniquify" });
    expectDownload.id = item.id;
  } else suggest();
});

chrome.downloads.onChanged.addListener(async (d) => {
  if (!expectDownload || d.id !== expectDownload.id || !d.state) return;
  if (d.state.current === "complete") {
    const [item] = await chrome.downloads.search({ id: d.id });
    expectDownload.resolve({ ok: true, path: item.filename });
  } else if (d.state.current === "interrupted") {
    expectDownload.resolve({ ok: false, detail: "download interrupted: " + (d.error && d.error.current) });
  }
});

function awaitDownload(token, title, timeoutMs) {
  return new Promise((resolve) => {
    const t = setTimeout(() => { expectDownload = null; resolve({ ok: false, detail: "download timeout" }); }, timeoutMs);
    expectDownload = { token, title, since: Date.now(), resolve: (r) => { clearTimeout(t); expectDownload = null; resolve(r); } };
  });
}

// ---------------- task runner ----------------
async function stopWith(reason, detail, token) {
  await host({ cmd: "blocked", reason, detail, token });
  setStatus(`Dừng: ${reason} - ${detail}`, { badge: "!" });
  autoRun = false;
}

async function runTask(task) {
  const tok = task.token;
  const tabId = await flowTab(task.project_url);
  let st = await call(tabId, "state");
  if (st.login) return stopWith("login", "Flow yêu cầu đăng nhập - hãy đăng nhập trong tab Flow rồi chạy `hana flow-unblock`", tok);
  if (st.captcha) return stopWith("captcha", "Flow đang hiện reCAPTCHA - tự xử lý trong tab rồi chạy `hana flow-unblock`", tok);
  if (st.quota) return stopWith("quota", "Flow báo hết lượt/tín dụng: " + st.alerts, tok);

  if (task.resume) return watchAndDownload(tabId, task, task.evidence || {}, task.evidence?.before_keys || []);

  if (!st.onProject) {
    setStatus("Tạo dự án Flow mới…");
    const np = await call(tabId, "openNewProject");
    if (!np.ok) return stopWith(np.reason, np.detail, tok);
    await waitTabComplete(tabId);
  }
  setStatus(`${task.job_id}/${task.scene_id}: chỉnh cài đặt`);
  const s = await call(tabId, "applySettings", task);
  if (!s.ok) return stopWith(s.reason || "ui_changed", s.detail, tok);
  await log(`settings ok: ${s.summary} ${s.cost || ""}`);

  if (task.refs && task.refs.length) {
    setStatus(`${task.job_id}/${task.scene_id}: tải ảnh tham chiếu`);
    const chips0 = await call(tabId, "promptChips");
    const [up] = await chrome.scripting.executeScript({ target: { tabId }, world: "MAIN", func: mainWorldUpload, args: [task.refs] });
    if (!up.result || !up.result.ok) return stopWith("ui_changed", "Upload ảnh: " + (up.result && up.result.detail), tok);
    // wait until the references show up as chips in the prompt box (upload finished)
    let chips = chips0;
    for (let i = 0; i < 60 && chips < chips0 + task.refs.length; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      chips = await call(tabId, "promptChips");
    }
    if (chips < chips0 + task.refs.length) {
      return stopWith("ui_changed", `Ảnh tham chiếu chưa gắn vào ô prompt (${chips - chips0}/${task.refs.length})`, tok);
    }
  }
  const p = await call(tabId, "setPrompt", task.prompt);
  if (!p.ok) return stopWith(p.reason, p.detail, tok);
  const before = await call(tabId, "snapshot");
  st = await call(tabId, "state");
  if (st.quota) return stopWith("quota", st.alerts, tok);

  // ---- point of no return ----
  const pre = await host({ cmd: "pre_submit", token: tok });
  if (!pre.ok) { setStatus("Host từ chối gửi: " + (pre.error || ""), { badge: "!" }); autoRun = false; return; }
  await chrome.storage.session.set({ activeToken: tok });
  setStatus(`${task.job_id}/${task.scene_id}: đã bấm Tạo, chờ xác nhận`);
  const g = await call(tabId, "clickGenerateAndConfirm", task.submit_confirm_timeout_sec || 60, before);
  if (!g.ok) {
    if (["quota", "captcha", "login"].includes(g.reason)) await host({ cmd: "blocked", reason: g.reason, detail: g.detail, token: tok });
    else await host({ cmd: "submit_unknown", token: tok, detail: `${g.reason}: ${g.detail}` });
    await chrome.storage.session.remove("activeToken");
    setStatus(`KHÔNG RÕ đã gửi hay chưa (${g.reason}). Kiểm tra Flow, rồi \`hana resolve\`.`, { badge: "?" });
    autoRun = false;
    return;
  }
  const evidence = { ...g.evidence, before_keys: before.keys };
  await host({ cmd: "submitted", token: tok, evidence, project_url: g.evidence.url });
  return watchAndDownload(tabId, task, evidence, before.keys);
}

async function watchAndDownload(tabId, task, evidence, beforeKeys) {
  const tok = task.token;
  const deadline = Date.now() + (task.generation_timeout_sec || 900) * 1000;
  let w = null;
  while (Date.now() < deadline) {
    w = await call(tabId, "watch", evidence, beforeKeys, 50);
    const pct = w.tile && w.tile.percent ? ` ${w.tile.percent}` : "";
    setStatus(`${task.job_id}/${task.scene_id}: đang tạo${pct}`);
    if (w.state !== "running") break;
  }
  if (!w || w.state === "running") {
    await chrome.storage.session.remove("activeToken");
    setStatus("Quá thời gian chờ Flow tạo xong - giữ GENERATING, chạy lại để tiếp tục theo dõi", { badge: "…" });
    await host({ cmd: "blocked", reason: "generation_timeout", detail: "timeout", token: tok });
    autoRun = false;
    return;
  }
  if (w.state === "blocked") return stopWith(w.reason, w.detail, tok);
  if (w.state === "failed") {
    await host({ cmd: "gen_failed", token: tok, detail: w.detail });
    await chrome.storage.session.remove("activeToken");
    setStatus(`${task.scene_id}: Flow báo tạo lỗi`, { badge: "!" });
    return;
  }
  setStatus(`${task.job_id}/${task.scene_id}: tải clip`);
  const title = (task.title || `${task.job_id}__${task.scene_id}`).replace(/[^\w.-]+/g, "_");
  const wait = awaitDownload(tok, title, 300000);
  const d = await call(tabId, "download", w.tile.key, task.download_resolution_index || 0);
  if (!d.ok) { expectDownload = null; return stopWith("ui_changed", "Tải xuống: " + d.detail, tok); }
  const res = await wait;
  if (!res.ok) return stopWith("download_failed", res.detail, tok);
  const r = await host({ cmd: "downloaded", token: tok, path: res.path });
  await chrome.storage.session.remove("activeToken");
  setStatus(r.ok ? `${task.scene_id}: đã tải (${d.option}) → chờ duyệt` : "Host không nhận file: " + r.error, { badge: r.ok ? "" : "!" });
}

async function loop() {
  if (running) return;
  running = true;
  try {
    const h = await hello();
    if (!h.ok) return;
    do {
      const r = await host({ cmd: "claim_task" }, 60000);
      if (!r.ok) { setStatus("Lỗi host: " + r.error, { badge: "ERR" }); break; }
      if (r.blocked) { setStatus(`Đang bị chặn (${r.blocked.reason}): ${r.blocked.detail}`, { badge: "!" }); break; }
      if (!r.task) { setStatus(r.busy ? `Đang chờ cảnh khác của ${r.busy.job}` : "Không có cảnh nào chờ gửi"); break; }
      try { await runTask(r.task); }
      catch (e) {
        const { activeToken } = await chrome.storage.session.get("activeToken");
        if (activeToken === r.task.token) await host({ cmd: "submit_unknown", token: r.task.token, detail: "exception: " + e });
        else await host({ cmd: "blocked", reason: "ui_changed", detail: "exception: " + e, token: r.task.token });
        await chrome.storage.session.remove("activeToken");
        setStatus("Lỗi: " + e, { badge: "ERR" });
        autoRun = false;
      }
    } while (autoRun);
  } finally { running = false; }
}

// ---------------- popup API ----------------
chrome.runtime.onMessage.addListener((m, _s, reply) => {
  (async () => {
    if (m.type === "get") return reply({ status, autoRun, running });
    if (m.type === "start") { autoRun = !!m.auto; loop(); return reply({ ok: true }); }
    if (m.type === "stop") { autoRun = false; return reply({ ok: true }); }
    if (m.type === "hello") return reply(await hello());
    if (m.type === "inspect") {
      const tabId = await flowTab("");
      const rep = await call(tabId, "inspect");
      const saved = await host({ cmd: "ui_report", report: rep });
      return reply({ report: rep, saved: saved.saved });
    }
  })();
  return true;
});

chrome.runtime.onStartup.addListener(() => hello());
chrome.runtime.onInstalled.addListener(() => hello());
