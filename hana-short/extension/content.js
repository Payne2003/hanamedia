// HanaMedia Short - Flow page driver (isolated world). Injected by background.js and called through
// chrome.scripting.executeScript(func: (n, a) => window.__hana[n](...a)).
// Selectors were checked against flow.google.com on 2026-11-18 (Vietnamese UI). Every lookup tries the
// Vietnamese label first, then English, then a structural fallback, so a UI language switch still works.
(() => {
  if (window.__hana && window.__hana.version === "0.1.0") return;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const txt = (e) => ((e && (e.innerText || e.textContent)) || "").replace(/\s+/g, " ").trim();
  const visible = (e) => {
    if (!e) return false;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== "hidden";
  };
  const byAria = (labels, root = document) =>
    [...root.querySelectorAll("button,[role=button],[role=textbox]")].find((b) =>
      labels.some((l) => (b.getAttribute("aria-label") || "").toLowerCase() === l.toLowerCase()));

  const SEL = {
    promptBox: () => document.querySelector('flow-prompt-box [role="textbox"][contenteditable="true"]') ||
      document.querySelector('.ProseMirror[contenteditable="true"]') ||
      byAria(["Bạn muốn tạo những gì?", "What do you want to create?"]),
    generateBtn: () => document.querySelector("flow-generate-icon-button button") ||
      byAria(["Bắt đầu tạo", "Generate", "Create"]),
    settingsBtn: () => document.querySelector("button.settings-trigger-button") ||
      byAria(["Điều kiện kích hoạt cài đặt", "Settings trigger"]),
    addBtn: () => byAria(["Thêm thành phần vào ô nhập câu lệnh", "Add ingredients to prompt", "Add to prompt"]),
    modelBtn: () => byAria(["Chọn nhóm mô hình", "Select model family", "Choose model"]),
    overlay: () => [...document.querySelectorAll(".cdk-overlay-pane")].filter(visible).pop() || null,
    tiles: () => [...document.querySelectorAll("flow-grid-tile-container")],
    newProjectBtn: () => [...document.querySelectorAll("button,a")].find((b) => /^(\+|add)?\s*(Dự án mới|New project)$/i.test(txt(b))),
    avatar: () => document.querySelector('button[aria-label^="Tài khoản Google"], button[aria-label^="Google Account"], flow-header-user-icon'),
  };
  const RX = {
    quota: /(hết|không đủ|đã dùng hết)\s+(tín dụng|lượt)|out of credits|not enough credits|insufficient credits|reached (your|the) (daily )?limit|đã đạt (đến )?giới hạn|quota/i,
    failed: /(tạo|quá trình tạo).{0,20}(không thành công|thất bại)|generation failed|couldn.t generate|không thể tạo|vi phạm|violat|policy|something went wrong|đã xảy ra lỗi/i,
    signin: /^(Đăng nhập|Sign in)$/i,
    percent: /\b\d{1,3}\s?%/,
    download: /^(download\s+)?(Tải xuống|Download)$/i,
    addToPrompt: /(Thêm vào câu lệnh|Add to prompt)/i,
    moreOpts: ["Tuỳ chọn khác", "Tùy chọn khác", "More options"],
  };

  function alertsText() {
    return [...document.querySelectorAll('mat-snack-bar-container,[role=alert],[role=status],.mat-mdc-snack-bar-label,flow-credit-banner,flow-banner')]
      .filter(visible).map(txt).join(" | ");
  }

  function captchaVisible() {
    return [...document.querySelectorAll('iframe[src*="recaptcha"]')].some((f) => {
      const r = f.getBoundingClientRect();
      return /bframe|challenge/.test(f.src) && r.width > 100 && r.height > 100;
    });
  }

  function state() {
    const host = location.host;
    const signinBtn = [...document.querySelectorAll("button,a")].find((b) => RX.signin.test(txt(b)) && visible(b));
    const login = /accounts\.google\.com/.test(host) || (!!signinBtn && !SEL.avatar());
    const alerts = alertsText();
    return {
      url: location.href, host,
      onProject: /\/project\//.test(location.pathname),
      login, captcha: captchaVisible(), quota: RX.quota.test(alerts), alerts: alerts.slice(0, 300),
      hasPrompt: !!SEL.promptBox(), hasGenerate: !!SEL.generateBtn(), hasSettings: !!SEL.settingsBtn(),
      tiles: SEL.tiles().length,
    };
  }

  async function waitFor(fn, timeout = 15000, step = 250) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      const v = fn();
      if (v) return v;
      await sleep(step);
    }
    return null;
  }

  function inspect() {
    const s = state();
    const settings = SEL.settingsBtn();
    return {
      ...s, at: new Date().toISOString(),
      selectors: {
        promptBox: !!SEL.promptBox(), generateBtn: !!SEL.generateBtn(), settingsBtn: !!settings,
        addBtn: !!SEL.addBtn(), modelBtn: !!SEL.modelBtn(), tiles: SEL.tiles().length,
        flowTags: [...new Set([...document.querySelectorAll("*")].map((e) => e.tagName.toLowerCase()).filter((t) => t.startsWith("flow-")))].slice(0, 80),
      },
      settingsText: txt(settings),
      generateLabel: SEL.generateBtn() && SEL.generateBtn().getAttribute("aria-label"),
    };
  }

  async function openNewProject() {
    const b = await waitFor(SEL.newProjectBtn, 15000);
    if (!b) return { ok: false, reason: "ui_changed", detail: "Không thấy nút 'Dự án mới'" };
    b.click();
    const ok = await waitFor(() => /\/project\//.test(location.pathname) && SEL.promptBox(), 30000);
    return ok ? { ok: true, url: location.href } : { ok: false, reason: "ui_changed", detail: "Không mở được dự án mới" };
  }

  // ---------- settings popover ------------------------------------------------------------------
  function radioByText(root, label) {
    const want = label.toLowerCase();
    return [...root.querySelectorAll('[role=radio], button.mat-button-toggle-button')].find((b) => {
      const t = txt(b).toLowerCase().replace(/^(image|videocam|crop_free|chrome_extension|crop_16_9|crop_9_16|info)\s*/g, "");
      return t === want || t.endsWith(" " + want) || t.startsWith(want + " ");
    });
  }

  async function ensureRadio(label) {
    const pane = SEL.overlay();
    if (!pane) return `không mở được bảng cài đặt`;
    const b = radioByText(pane, label);
    if (!b) return `không thấy lựa chọn '${label}'`;
    if (b.getAttribute("aria-checked") !== "true") {
      b.click();
      await sleep(350);
    }
    const again = radioByText(SEL.overlay() || pane, label);
    return again && again.getAttribute("aria-checked") === "true" ? null : `không bật được '${label}'`;
  }

  async function applySettings(t) {
    const trig = SEL.settingsBtn();
    if (!trig) return { ok: false, reason: "ui_changed", detail: "Không thấy nút cài đặt (Video · 720p ...)" };
    trig.click();
    if (!(await waitFor(SEL.overlay, 5000))) return { ok: false, reason: "ui_changed", detail: "Bảng cài đặt không mở" };
    const aspect = t.aspect_ratio || "9:16";
    const steps = ["Video", t.mode, aspect, t.duration_label, t.outputs].filter(Boolean);
    const errs = [];
    for (const label of steps) {
      const e = await ensureRadio(label);
      if (e) errs.push(e);
    }
    // model
    if (t.model) {
      const mb = SEL.modelBtn();
      if (!mb) errs.push("không thấy nút chọn mô hình");
      else if (!txt(mb).toLowerCase().startsWith(t.model.toLowerCase())) {
        mb.click();
        const item = await waitFor(() => [...document.querySelectorAll("[role=menuitem]")]
          .find((m) => txt(m).toLowerCase().startsWith(t.model.toLowerCase())), 4000);
        if (!item) errs.push(`không có mô hình '${t.model}'`);
        else { item.click(); await sleep(500); }
        const mb2 = SEL.modelBtn();
        if (!mb2 || !txt(mb2).toLowerCase().startsWith(t.model.toLowerCase())) errs.push(`chưa chọn được mô hình '${t.model}'`);
      }
      // a model switch can reset other toggles -> re-check
      if (!errs.length) for (const label of steps) { const e = await ensureRadio(label); if (e) errs.push(e); }
    }
    const pane = SEL.overlay();
    const cost = pane ? (txt(pane).match(/tốn\s+(\d+)\s+tín dụng|(\d+)\s+credits?/i) || [])[0] || "" : "";
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    (document.activeElement || document.body).dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await sleep(400);
    const summary = txt(SEL.settingsBtn());
    if (errs.length) return { ok: false, reason: "ui_changed", detail: errs.join("; "), summary };
    const aspectIcon = aspect === "9:16" ? "crop_9_16" : "crop_16_9";
    if (!summary.includes(aspectIcon) && !summary.includes(aspect)) {
      return { ok: false, reason: "ui_changed", detail: `Nút cài đặt không báo ${aspect}: '${summary}'`, summary };
    }
    return { ok: true, summary, cost };
  }

  // ---------- prompt & ingredients ---------------------------------------------------------------
  function promptChips() {
    const pb = document.querySelector("flow-prompt-box") || SEL.promptBox()?.closest("form") || document;
    return pb.querySelectorAll("img").length;
  }

  async function setPrompt(text) {
    const box = SEL.promptBox();
    if (!box) return { ok: false, reason: "ui_changed", detail: "Không thấy ô prompt" };
    box.focus();
    document.execCommand("selectAll", false, null);
    document.execCommand("delete", false, null);
    const lines = text.split("\n");
    lines.forEach((line, i) => {
      if (i > 0) document.execCommand("insertParagraph", false, null);
      document.execCommand("insertText", false, line);
    });
    await sleep(300);
    const got = txt(box).replace(/\s+/g, " ");
    const want = text.replace(/\s+/g, " ").trim();
    if (!got.includes(want.slice(0, 60)) || got.length < want.length * 0.95) {
      return { ok: false, reason: "ui_changed", detail: `Prompt nhập không khớp (${got.length}/${want.length} ký tự)` };
    }
    return { ok: true, chars: got.length };
  }

  function tileKey(t) {
    const m = t.querySelector("[data-media-id]");
    return (m && m.getAttribute("data-media-id")) || t.getAttribute("aria-label") || "";
  }

  function tileInfo(t) {
    const text = txt(t);
    const media = t.querySelector("[data-media-id]");
    return {
      key: tileKey(t), label: t.getAttribute("aria-label") || "", mediaId: media && media.getAttribute("data-media-id"),
      isVideo: !!t.querySelector("video, flow-video-tile, [class*=video]") || /videocam|play_arrow|play_circle/.test(text),
      percent: (text.match(RX.percent) || [null])[0],
      progress: !!t.querySelector("[role=progressbar], mat-progress-bar, mat-spinner, mat-progress-spinner"),
      failed: RX.failed.test(text), text: text.slice(0, 160),
    };
  }

  function snapshot() {
    const tiles = SEL.tiles();
    return { count: tiles.length, keys: tiles.map(tileKey), first: tiles[0] ? tileInfo(tiles[0]) : null };
  }

  // ---------- generate (point of no return is the click) ------------------------------------------
  async function clickGenerateAndConfirm(timeoutSec, before) {
    const btn = SEL.generateBtn();
    if (!btn) return { ok: false, unknown: false, reason: "ui_changed", detail: "Không thấy nút Tạo" };
    if (btn.disabled || btn.getAttribute("aria-disabled") === "true") {
      return { ok: false, unknown: false, reason: "ui_changed", detail: "Nút Tạo đang bị vô hiệu hoá" };
    }
    btn.click();
    const t0 = Date.now();
    const beforeKeys = new Set(before.keys || []);
    while (Date.now() - t0 < timeoutSec * 1000) {
      await sleep(1000);
      if (captchaVisible()) return { ok: false, unknown: true, reason: "captcha", detail: "Hiện reCAPTCHA sau khi bấm Tạo" };
      const al = alertsText();
      if (RX.quota.test(al)) return { ok: false, unknown: true, reason: "quota", detail: al.slice(0, 200) };
      const tiles = SEL.tiles();
      const fresh = tiles.filter((t) => !beforeKeys.has(tileKey(t)));
      const pending = fresh.map(tileInfo);
      const promptCleared = !txt(SEL.promptBox() || document.body).length;
      if (tiles.length > (before.count || 0) || pending.some((p) => p.percent || p.progress)) {
        return { ok: true, evidence: { before_count: before.count, after_count: tiles.length, new_tiles: pending.slice(0, 4),
          prompt_cleared: promptCleared, seconds: Math.round((Date.now() - t0) / 1000), url: location.href } };
      }
      if (RX.failed.test(al)) return { ok: false, unknown: true, reason: "rejected", detail: al.slice(0, 200) };
    }
    return { ok: false, unknown: true, reason: "no_evidence",
      detail: `Sau ${timeoutSec}s không thấy ô kết quả mới (trước ${before.count}, giờ ${SEL.tiles().length})` };
  }

  // ---------- watch ---------------------------------------------------------------------------
  function findTarget(evidence, beforeKeys) {
    const tiles = SEL.tiles();
    const known = new Set(beforeKeys || []);
    const wantKeys = new Set((evidence.new_tiles || []).map((n) => n.key).filter(Boolean));
    let t = tiles.find((x) => wantKeys.has(tileKey(x)));
    if (!t) t = tiles.find((x) => !known.has(tileKey(x)) && tileInfo(x).isVideo);
    if (!t && evidence.media_id) t = tiles.find((x) => tileKey(x) === evidence.media_id);
    return t || null;
  }

  async function watch(evidence, beforeKeys, chunkSec) {
    const end = Date.now() + chunkSec * 1000;
    let last = null;
    while (Date.now() < end) {
      const al = alertsText();
      if (RX.quota.test(al)) return { state: "blocked", reason: "quota", detail: al.slice(0, 200) };
      const t = findTarget(evidence, beforeKeys);
      if (t) {
        const info = tileInfo(t);
        last = info;
        if (info.failed) return { state: "failed", detail: info.text };
        const hasMedia = info.mediaId || t.querySelector("video");
        if (hasMedia && !info.percent && !info.progress) return { state: "done", tile: { ...info, key: tileKey(t) } };
      }
      await sleep(3000);
    }
    return { state: "running", tile: last };
  }

  // ---------- download ------------------------------------------------------------------------
  async function openTileMenu(tile) {
    tile.scrollIntoView({ block: "center" });
    tile.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    await sleep(300);
    const b = [...tile.querySelectorAll("button")].find((x) => RX.moreOpts.includes(x.getAttribute("aria-label") || ""));
    if (!b) return null;
    b.click();
    return await waitFor(() => [...document.querySelectorAll("[role=menuitem]")].filter(visible), 4000);
  }

  async function download(key, resIndex) {
    const tile = SEL.tiles().find((t) => tileKey(t) === key);
    if (!tile) return { ok: false, detail: "Không còn thấy ô kết quả để tải" };
    const items = await openTileMenu(tile);
    if (!items) return { ok: false, detail: "Không mở được menu 'Tuỳ chọn khác'" };
    const before = new Set(items.map(txt));
    const dl = items.find((m) => RX.download.test(txt(m)));
    if (!dl) return { ok: false, detail: "Menu không có 'Tải xuống'" };
    dl.dispatchEvent(new MouseEvent("mouseenter", { bubbles: true }));
    dl.click();
    const sub = await waitFor(() => {
      const all = [...document.querySelectorAll("[role=menuitem]")].filter(visible);
      const extra = all.filter((m) => !before.has(txt(m)));
      return extra.length ? extra : null;
    }, 4000);
    if (sub) {
      const pick = sub[Math.min(resIndex || 0, sub.length - 1)];
      const label = txt(pick);
      pick.click();
      return { ok: true, option: label };
    }
    return { ok: true, option: "direct" }; // some media download straight from the first click
  }

  async function addTilesToPrompt(keys) {
    let added = 0;
    for (const k of keys) {
      const tile = SEL.tiles().find((t) => tileKey(t) === k);
      if (!tile) continue;
      const items = await openTileMenu(tile);
      const it = items && items.find((m) => RX.addToPrompt.test(txt(m)));
      if (it) { it.click(); added++; await sleep(500); }
      else document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    }
    return added;
  }

  window.__hana = {
    version: "0.1.0", state, inspect, openNewProject, applySettings, setPrompt, snapshot, promptChips,
    clickGenerateAndConfirm, watch, download, addTilesToPrompt,
    waitTilesSettled: async (sec) => { await sleep(sec * 1000); return snapshot(); },
  };
})();
