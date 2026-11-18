"""Động cơ Veo trên Google Flow cho tool (Selenium, hồ sơ Chrome google-flow của tool).

Port từ extension HanaMedia Short: PAGE_JS = extension/content.js (bộ chọn Flow kiểm tra 18/11/2026, giao diện tiếng
Việt, có dự phòng tiếng Anh) và trình tự background.js runTask/watchAndDownload. Máy trạng thái chống gửi trùng nằm ở
flow.py: điền form → pre_submit (ghi SUBMITTING TRƯỚC khi bấm Tạo) → bấm Tạo → thấy ô kết quả mới (GENERATING) → tải về
(DOWNLOADED). Mọi bất thường TRƯỚC pre_submit trả cảnh về READY; SAU đó thì BLOCKED unknown_submit, không tự gửi lại.
Gặp đăng nhập / CAPTCHA / hết tín dụng → chặn toàn cục và dừng; tool không giải CAPTCHA, không tự đăng nhập.
"""
from __future__ import annotations

import time
from pathlib import Path

from . import flow
from .jobs import Job, JobError

PAGE_JS = '// HanaMedia Short - Flow page driver (isolated world). Injected by background.js and called through\n// chrome.scripting.executeScript(func: (n, a) => window.__hana[n](...a)).\n// Selectors were checked against flow.google.com on 2026-11-18 (Vietnamese UI). Every lookup tries the\n// Vietnamese label first, then English, then a structural fallback, so a UI language switch still works.\n(() => {\n  if (window.__hana && window.__hana.version === "0.1.0") return;\n\n  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));\n  const txt = (e) => ((e && (e.innerText || e.textContent)) || "").replace(/\\s+/g, " ").trim();\n  const visible = (e) => {\n    if (!e) return false;\n    const r = e.getBoundingClientRect();\n    return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== "hidden";\n  };\n  const byAria = (labels, root = document) =>\n    [...root.querySelectorAll("button,[role=button],[role=textbox]")].find((b) =>\n      labels.some((l) => (b.getAttribute("aria-label") || "").toLowerCase() === l.toLowerCase()));\n\n  const SEL = {\n    promptBox: () => document.querySelector(\'flow-prompt-box [role="textbox"][contenteditable="true"]\') ||\n      document.querySelector(\'.ProseMirror[contenteditable="true"]\') ||\n      byAria(["Bạn muốn tạo những gì?", "What do you want to create?"]),\n    generateBtn: () => document.querySelector("flow-generate-icon-button button") ||\n      byAria(["Bắt đầu tạo", "Generate", "Create"]),\n    settingsBtn: () => document.querySelector("button.settings-trigger-button") ||\n      byAria(["Điều kiện kích hoạt cài đặt", "Settings trigger"]),\n    addBtn: () => byAria(["Thêm thành phần vào ô nhập câu lệnh", "Add ingredients to prompt", "Add to prompt"]),\n    modelBtn: () => byAria(["Chọn nhóm mô hình", "Select model family", "Choose model"]),\n    overlay: () => [...document.querySelectorAll(".cdk-overlay-pane")].filter(visible).pop() || null,\n    tiles: () => [...document.querySelectorAll("flow-grid-tile-container")],\n    newProjectBtn: () => [...document.querySelectorAll("button,a")].find((b) => /^(\\+|add)?\\s*(Dự án mới|New project)$/i.test(txt(b))),\n    avatar: () => document.querySelector(\'button[aria-label^="Tài khoản Google"], button[aria-label^="Google Account"], flow-header-user-icon\'),\n  };\n  const RX = {\n    quota: /(hết|không đủ|đã dùng hết)\\s+(tín dụng|lượt)|out of credits|not enough credits|insufficient credits|reached (your|the) (daily )?limit|đã đạt (đến )?giới hạn|quota/i,\n    failed: /(tạo|quá trình tạo).{0,20}(không thành công|thất bại)|generation failed|couldn.t generate|không thể tạo|vi phạm|violat|policy|something went wrong|đã xảy ra lỗi/i,\n    signin: /^(Đăng nhập|Sign in)$/i,\n    percent: /\\b\\d{1,3}\\s?%/,\n    download: /^(download\\s+)?(Tải xuống|Download)$/i,\n    addToPrompt: /(Thêm vào câu lệnh|Add to prompt)/i,\n    moreOpts: ["Tuỳ chọn khác", "Tùy chọn khác", "More options"],\n  };\n\n  function alertsText() {\n    return [...document.querySelectorAll(\'mat-snack-bar-container,[role=alert],[role=status],.mat-mdc-snack-bar-label,flow-credit-banner,flow-banner\')]\n      .filter(visible).map(txt).join(" | ");\n  }\n\n  function captchaVisible() {\n    return [...document.querySelectorAll(\'iframe[src*="recaptcha"]\')].some((f) => {\n      const r = f.getBoundingClientRect();\n      return /bframe|challenge/.test(f.src) && r.width > 100 && r.height > 100;\n    });\n  }\n\n  function state() {\n    const host = location.host;\n    const signinBtn = [...document.querySelectorAll("button,a")].find((b) => RX.signin.test(txt(b)) && visible(b));\n    const login = /accounts\\.google\\.com/.test(host) || (!!signinBtn && !SEL.avatar());\n    const alerts = alertsText();\n    return {\n      url: location.href, host,\n      onProject: /\\/project\\//.test(location.pathname),\n      login, captcha: captchaVisible(), quota: RX.quota.test(alerts), alerts: alerts.slice(0, 300),\n      hasPrompt: !!SEL.promptBox(), hasGenerate: !!SEL.generateBtn(), hasSettings: !!SEL.settingsBtn(),\n      tiles: SEL.tiles().length,\n    };\n  }\n\n  async function waitFor(fn, timeout = 15000, step = 250) {\n    const end = Date.now() + timeout;\n    while (Date.now() < end) {\n      const v = fn();\n      if (v) return v;\n      await sleep(step);\n    }\n    return null;\n  }\n\n  function inspect() {\n    const s = state();\n    const settings = SEL.settingsBtn();\n    return {\n      ...s, at: new Date().toISOString(),\n      selectors: {\n        promptBox: !!SEL.promptBox(), generateBtn: !!SEL.generateBtn(), settingsBtn: !!settings,\n        addBtn: !!SEL.addBtn(), modelBtn: !!SEL.modelBtn(), tiles: SEL.tiles().length,\n        flowTags: [...new Set([...document.querySelectorAll("*")].map((e) => e.tagName.toLowerCase()).filter((t) => t.startsWith("flow-")))].slice(0, 80),\n      },\n      settingsText: txt(settings),\n      generateLabel: SEL.generateBtn() && SEL.generateBtn().getAttribute("aria-label"),\n    };\n  }\n\n  async function openNewProject() {\n    const b = await waitFor(SEL.newProjectBtn, 15000);\n    if (!b) return { ok: false, reason: "ui_changed", detail: "Không thấy nút \'Dự án mới\'" };\n    b.click();\n    const ok = await waitFor(() => /\\/project\\//.test(location.pathname) && SEL.promptBox(), 30000);\n    return ok ? { ok: true, url: location.href } : { ok: false, reason: "ui_changed", detail: "Không mở được dự án mới" };\n  }\n\n  // ---------- settings popover ------------------------------------------------------------------\n  function radioByText(root, label) {\n    const want = label.toLowerCase();\n    return [...root.querySelectorAll(\'[role=radio], button.mat-button-toggle-button\')].find((b) => {\n      const t = txt(b).toLowerCase().replace(/^(image|videocam|crop_free|chrome_extension|crop_16_9|crop_9_16|info)\\s*/g, "");\n      return t === want || t.endsWith(" " + want) || t.startsWith(want + " ");\n    });\n  }\n\n  async function ensureRadio(label) {\n    const pane = SEL.overlay();\n    if (!pane) return `không mở được bảng cài đặt`;\n    const b = radioByText(pane, label);\n    if (!b) return `không thấy lựa chọn \'${label}\'`;\n    if (b.getAttribute("aria-checked") !== "true") {\n      b.click();\n      await sleep(350);\n    }\n    const again = radioByText(SEL.overlay() || pane, label);\n    return again && again.getAttribute("aria-checked") === "true" ? null : `không bật được \'${label}\'`;\n  }\n\n  async function applySettings(t) {\n    const trig = SEL.settingsBtn();\n    if (!trig) return { ok: false, reason: "ui_changed", detail: "Không thấy nút cài đặt (Video · 720p ...)" };\n    trig.click();\n    if (!(await waitFor(SEL.overlay, 5000))) return { ok: false, reason: "ui_changed", detail: "Bảng cài đặt không mở" };\n    const aspect = t.aspect_ratio || "9:16";\n    const steps = ["Video", t.mode, aspect, t.duration_label, t.outputs].filter(Boolean);\n    const errs = [];\n    for (const label of steps) {\n      const e = await ensureRadio(label);\n      if (e) errs.push(e);\n    }\n    // model\n    if (t.model) {\n      const mb = SEL.modelBtn();\n      if (!mb) errs.push("không thấy nút chọn mô hình");\n      else if (!txt(mb).toLowerCase().startsWith(t.model.toLowerCase())) {\n        mb.click();\n        const item = await waitFor(() => [...document.querySelectorAll("[role=menuitem]")]\n          .find((m) => txt(m).toLowerCase().startsWith(t.model.toLowerCase())), 4000);\n        if (!item) errs.push(`không có mô hình \'${t.model}\'`);\n        else { item.click(); await sleep(500); }\n        const mb2 = SEL.modelBtn();\n        if (!mb2 || !txt(mb2).toLowerCase().startsWith(t.model.toLowerCase())) errs.push(`chưa chọn được mô hình \'${t.model}\'`);\n      }\n      // a model switch can reset other toggles -> re-check\n      if (!errs.length) for (const label of steps) { const e = await ensureRadio(label); if (e) errs.push(e); }\n    }\n    const pane = SEL.overlay();\n    const cost = pane ? (txt(pane).match(/tốn\\s+(\\d+)\\s+tín dụng|(\\d+)\\s+credits?/i) || [])[0] || "" : "";\n    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));\n    (document.activeElement || document.body).dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));\n    await sleep(400);\n    const summary = txt(SEL.settingsBtn());\n    if (errs.length) return { ok: false, reason: "ui_changed", detail: errs.join("; "), summary };\n    const aspectIcon = aspect === "9:16" ? "crop_9_16" : "crop_16_9";\n    if (!summary.includes(aspectIcon) && !summary.includes(aspect)) {\n      return { ok: false, reason: "ui_changed", detail: `Nút cài đặt không báo ${aspect}: \'${summary}\'`, summary };\n    }\n    return { ok: true, summary, cost };\n  }\n\n  // ---------- prompt & ingredients ---------------------------------------------------------------\n  function promptChips() {\n    const pb = document.querySelector("flow-prompt-box") || SEL.promptBox()?.closest("form") || document;\n    return pb.querySelectorAll("img").length;\n  }\n\n  async function setPrompt(text) {\n    const box = SEL.promptBox();\n    if (!box) return { ok: false, reason: "ui_changed", detail: "Không thấy ô prompt" };\n    box.focus();\n    document.execCommand("selectAll", false, null);\n    document.execCommand("delete", false, null);\n    const lines = text.split("\\n");\n    lines.forEach((line, i) => {\n      if (i > 0) document.execCommand("insertParagraph", false, null);\n      document.execCommand("insertText", false, line);\n    });\n    await sleep(300);\n    const got = txt(box).replace(/\\s+/g, " ");\n    const want = text.replace(/\\s+/g, " ").trim();\n    if (!got.includes(want.slice(0, 60)) || got.length < want.length * 0.95) {\n      return { ok: false, reason: "ui_changed", detail: `Prompt nhập không khớp (${got.length}/${want.length} ký tự)` };\n    }\n    return { ok: true, chars: got.length };\n  }\n\n  function tileKey(t) {\n    const m = t.querySelector("[data-media-id]");\n    return (m && m.getAttribute("data-media-id")) || t.getAttribute("aria-label") || "";\n  }\n\n  function tileInfo(t) {\n    const text = txt(t);\n    const media = t.querySelector("[data-media-id]");\n    return {\n      key: tileKey(t), label: t.getAttribute("aria-label") || "", mediaId: media && media.getAttribute("data-media-id"),\n      isVideo: !!t.querySelector("video, flow-video-tile, [class*=video]") || /videocam|play_arrow|play_circle/.test(text),\n      percent: (text.match(RX.percent) || [null])[0],\n      progress: !!t.querySelector("[role=progressbar], mat-progress-bar, mat-spinner, mat-progress-spinner"),\n      failed: RX.failed.test(text), text: text.slice(0, 160),\n    };\n  }\n\n  function snapshot() {\n    const tiles = SEL.tiles();\n    return { count: tiles.length, keys: tiles.map(tileKey), first: tiles[0] ? tileInfo(tiles[0]) : null };\n  }\n\n  // ---------- generate (point of no return is the click) ------------------------------------------\n  async function clickGenerateAndConfirm(timeoutSec, before) {\n    const btn = SEL.generateBtn();\n    if (!btn) return { ok: false, unknown: false, reason: "ui_changed", detail: "Không thấy nút Tạo" };\n    if (btn.disabled || btn.getAttribute("aria-disabled") === "true") {\n      return { ok: false, unknown: false, reason: "ui_changed", detail: "Nút Tạo đang bị vô hiệu hoá" };\n    }\n    btn.click();\n    const t0 = Date.now();\n    const beforeKeys = new Set(before.keys || []);\n    while (Date.now() - t0 < timeoutSec * 1000) {\n      await sleep(1000);\n      if (captchaVisible()) return { ok: false, unknown: true, reason: "captcha", detail: "Hiện reCAPTCHA sau khi bấm Tạo" };\n      const al = alertsText();\n      if (RX.quota.test(al)) return { ok: false, unknown: true, reason: "quota", detail: al.slice(0, 200) };\n      const tiles = SEL.tiles();\n      const fresh = tiles.filter((t) => !beforeKeys.has(tileKey(t)));\n      const pending = fresh.map(tileInfo);\n      const promptCleared = !txt(SEL.promptBox() || document.body).length;\n      if (tiles.length > (before.count || 0) || pending.some((p) => p.percent || p.progress)) {\n        return { ok: true, evidence: { before_count: before.count, after_count: tiles.length, new_tiles: pending.slice(0, 4),\n          prompt_cleared: promptCleared, seconds: Math.round((Date.now() - t0) / 1000), url: location.href } };\n      }\n      if (RX.failed.test(al)) return { ok: false, unknown: true, reason: "rejected", detail: al.slice(0, 200) };\n    }\n    return { ok: false, unknown: true, reason: "no_evidence",\n      detail: `Sau ${timeoutSec}s không thấy ô kết quả mới (trước ${before.count}, giờ ${SEL.tiles().length})` };\n  }\n\n  // ---------- watch ---------------------------------------------------------------------------\n  function findTarget(evidence, beforeKeys) {\n    const tiles = SEL.tiles();\n    const known = new Set(beforeKeys || []);\n    const wantKeys = new Set((evidence.new_tiles || []).map((n) => n.key).filter(Boolean));\n    let t = tiles.find((x) => wantKeys.has(tileKey(x)));\n    if (!t) t = tiles.find((x) => !known.has(tileKey(x)) && tileInfo(x).isVideo);\n    if (!t && evidence.media_id) t = tiles.find((x) => tileKey(x) === evidence.media_id);\n    return t || null;\n  }\n\n  async function watch(evidence, beforeKeys, chunkSec) {\n    const end = Date.now() + chunkSec * 1000;\n    let last = null;\n    while (Date.now() < end) {\n      const al = alertsText();\n      if (RX.quota.test(al)) return { state: "blocked", reason: "quota", detail: al.slice(0, 200) };\n      const t = findTarget(evidence, beforeKeys);\n      if (t) {\n        const info = tileInfo(t);\n        last = info;\n        if (info.failed) return { state: "failed", detail: info.text };\n        const hasMedia = info.mediaId || t.querySelector("video");\n        if (hasMedia && !info.percent && !info.progress) return { state: "done", tile: { ...info, key: tileKey(t) } };\n      }\n      await sleep(3000);\n    }\n    return { state: "running", tile: last };\n  }\n\n  // ---------- download ------------------------------------------------------------------------\n  async function openTileMenu(tile) {\n    tile.scrollIntoView({ block: "center" });\n    tile.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));\n    await sleep(300);\n    const b = [...tile.querySelectorAll("button")].find((x) => RX.moreOpts.includes(x.getAttribute("aria-label") || ""));\n    if (!b) return null;\n    b.click();\n    return await waitFor(() => [...document.querySelectorAll("[role=menuitem]")].filter(visible), 4000);\n  }\n\n  async function download(key, resIndex) {\n    const tile = SEL.tiles().find((t) => tileKey(t) === key);\n    if (!tile) return { ok: false, detail: "Không còn thấy ô kết quả để tải" };\n    const items = await openTileMenu(tile);\n    if (!items) return { ok: false, detail: "Không mở được menu \'Tuỳ chọn khác\'" };\n    const before = new Set(items.map(txt));\n    const dl = items.find((m) => RX.download.test(txt(m)));\n    if (!dl) return { ok: false, detail: "Menu không có \'Tải xuống\'" };\n    dl.dispatchEvent(new MouseEvent("mouseenter", { bubbles: true }));\n    dl.click();\n    const sub = await waitFor(() => {\n      const all = [...document.querySelectorAll("[role=menuitem]")].filter(visible);\n      const extra = all.filter((m) => !before.has(txt(m)));\n      return extra.length ? extra : null;\n    }, 4000);\n    if (sub) {\n      const pick = sub[Math.min(resIndex || 0, sub.length - 1)];\n      const label = txt(pick);\n      pick.click();\n      return { ok: true, option: label };\n    }\n    return { ok: true, option: "direct" }; // some media download straight from the first click\n  }\n\n  async function addTilesToPrompt(keys) {\n    let added = 0;\n    for (const k of keys) {\n      const tile = SEL.tiles().find((t) => tileKey(t) === k);\n      if (!tile) continue;\n      const items = await openTileMenu(tile);\n      const it = items && items.find((m) => RX.addToPrompt.test(txt(m)));\n      if (it) { it.click(); added++; await sleep(500); }\n      else document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));\n    }\n    return added;\n  }\n\n  window.__hana = {\n    version: "0.1.0", state, inspect, openNewProject, applySettings, setPrompt, snapshot, promptChips,\n    clickGenerateAndConfirm, watch, download, addTilesToPrompt,\n    waitTilesSettled: async (sec) => { await sleep(sec * 1000); return snapshot(); },\n  };\n})();\n'

UPLOAD_JS = 'async function mainWorldUpload(refs) {\n  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));\n  const vis = (e) => e && e.getBoundingClientRect().width > 0;\n  const add = [...document.querySelectorAll("button")].find((b) =>\n    ["Thêm thành phần vào ô nhập câu lệnh", "Add ingredients to prompt", "Add to prompt"].includes(b.getAttribute("aria-label")));\n  if (!add) return { ok: false, detail: "Không thấy nút + (thêm thành phần)" };\n  add.click();\n  await sleep(700);\n  const up = [...document.querySelectorAll(".cdk-overlay-pane button")].find((b) => /Tải nội dung nghe nhìn lên|Upload media|Upload/i.test(b.innerText) && vis(b));\n  if (!up) return { ok: false, detail: "Không thấy nút \'Tải nội dung nghe nhìn lên\'" };\n  let captured = null;\n  const origClick = HTMLInputElement.prototype.click;\n  const origPicker = HTMLInputElement.prototype.showPicker;\n  HTMLInputElement.prototype.click = function () { if (this.type === "file") { captured = this; return; } return origClick.call(this); };\n  HTMLInputElement.prototype.showPicker = function () { if (this.type === "file") { captured = this; return; } return origPicker && origPicker.call(this); };\n  try { up.click(); await sleep(600); }\n  finally { HTMLInputElement.prototype.click = origClick; HTMLInputElement.prototype.showPicker = origPicker; }\n  captured = captured || document.querySelector(\'input[type=file][accept*=".png"]\');\n  if (!captured) return { ok: false, detail: "Không bắt được input[type=file]" };\n  const dt = new DataTransfer();\n  for (const r of refs) {\n    const bin = Uint8Array.from(atob(r.b64), (c) => c.charCodeAt(0));\n    dt.items.add(new File([bin], r.filename, { type: r.mime }));\n  }\n  captured.files = dt.files;\n  captured.dispatchEvent(new Event("input", { bubbles: true }));\n  captured.dispatchEvent(new Event("change", { bubbles: true }));\n  await sleep(500);\n  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));\n  return { ok: true, files: refs.length };\n}\n'

CALL = PAGE_JS + """
const done = arguments[arguments.length - 1];
const name = arguments[0], args = arguments[1] || [];
Promise.resolve().then(() => window.__hana[name](...args))
  .then(r => done({ok: true, result: r === undefined ? null : r}))
  .catch(e => done({ok: false, error: String(e && e.stack || e)}));
"""

UPLOAD_CALL = UPLOAD_JS + """
const done = arguments[arguments.length - 1];
mainWorldUpload(arguments[0]).then(r => done(r)).catch(e => done({ok: false, detail: String(e)}));
"""


class Stop(Exception):
    """Dừng vòng chạy Flow (đã ghi trạng thái cảnh / chặn toàn cục)."""


class FlowVeo:
    def __init__(self, cfg, open_session, status=lambda text: None, should_stop=lambda: False):
        self.cfg = cfg
        self.open_session = open_session  # () -> FlowImageSession (Chrome, hồ sơ google-flow, thư mục tải)
        self.session = None
        self.status = status
        self.should_stop = should_stop

    # ---- trình duyệt ----
    @property
    def driver(self):
        if self.session is None:
            self.session = self.open_session()
            self.session.driver.set_script_timeout(30 * 60)
        return self.session.driver

    def close(self):
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None

    def call(self, name, *args):
        r = self.driver.execute_async_script(CALL, name, list(args))
        if not isinstance(r, dict) or not r.get("ok"):
            raise RuntimeError(f"Lỗi trang Flow ({name}): {(r or {}).get('error', r)}"[:500])
        return r["result"]

    def open_project(self, project_url):
        url = self.driver.current_url or ""
        if project_url and not url.startswith(project_url):
            self.driver.get(project_url)
        elif "flow.google" not in url and "labs.google" not in url:
            from ..flow import FLOW_URL
            self.driver.get(FLOW_URL)
        for _ in range(60):
            if self.driver.execute_script("return document.readyState") == "complete":
                break
            time.sleep(.5)

    # ---- một lượt: nhận cảnh READY (hoặc cảnh GENERATING cần theo dõi tiếp) và làm tới khi tải xong ----
    def run_once(self, job_filter=None):
        claim = flow.claim_task(self.cfg, job_filter)
        if claim.get("blocked"):
            b = claim["blocked"]
            raise Stop(f"Flow đang bị chặn ({b.get('reason')}): {b.get('detail')}")
        task = claim.get("task")
        if not task:
            return None
        try:
            self.run_task(task)
        except Stop:
            raise
        except Exception as error:  # lỗi không lường trước: cảnh đã bấm Tạo → unknown_submit, chưa bấm → READY
            self._crash(task, error)
            raise Stop(f"Lỗi khi chạy Flow: {error}"[:400])
        return task

    def _crash(self, task, error):
        try:
            job, sid = flow._find_token(self.cfg, task["token"])
            st = Job.scene(job.load(), sid)["status"]
            if st == "SUBMITTING":
                flow.submit_unknown(self.cfg, task["token"], f"exception: {error}"[:300])
            else:
                flow.blocked(self.cfg, "ui_changed", f"exception: {error}"[:300], task["token"])
        except JobError:
            pass

    def stop_with(self, reason, detail, token):
        flow.blocked(self.cfg, reason, detail, token)
        raise Stop(f"{reason}: {detail}")

    def run_task(self, task):
        tok = task["token"]
        self.open_project(task.get("project_url") or "")
        st = self.call("state")
        if st.get("login"):
            self.stop_with("login", "Flow yêu cầu đăng nhập - đăng nhập trong cửa sổ Chrome của tool rồi bấm Gỡ chặn Flow", tok)
        if st.get("captcha"):
            self.stop_with("captcha", "Flow đang hiện reCAPTCHA - tự xử lý trong cửa sổ Chrome rồi bấm Gỡ chặn Flow", tok)
        if st.get("quota"):
            self.stop_with("quota", "Flow báo hết lượt / tín dụng: " + (st.get("alerts") or ""), tok)
        if task.get("resume"):
            ev = task.get("evidence") or {}
            return self.watch_and_download(task, ev, ev.get("before_keys") or [])
        if not st.get("onProject"):
            self.status("Tạo dự án Flow mới…")
            np = self.call("openNewProject")
            if not np.get("ok"):
                self.stop_with(np.get("reason") or "ui_changed", np.get("detail"), tok)
        self.status(f"{task['job_id']}/{task['scene_id']}: chỉnh cài đặt")
        s = self.call("applySettings", task)
        if not s.get("ok"):
            self.stop_with(s.get("reason") or "ui_changed", s.get("detail"), tok)
        Job(self.cfg, task["job_id"]).log("flow_settings", summary=s.get("summary"), cost=s.get("cost"))
        if task.get("refs"):
            self.status(f"{task['job_id']}/{task['scene_id']}: tải ảnh tham chiếu")
            chips0 = self.call("promptChips")
            up = self.driver.execute_async_script(UPLOAD_CALL, task["refs"])
            if not up or not up.get("ok"):
                self.stop_with("ui_changed", "Upload ảnh: " + str((up or {}).get("detail")), tok)
            chips = chips0
            for _ in range(60):  # chờ ảnh thành chip trong ô prompt (tải xong)
                if chips >= chips0 + len(task["refs"]):
                    break
                time.sleep(1)
                chips = self.call("promptChips")
            if chips < chips0 + len(task["refs"]):
                self.stop_with("ui_changed", f"Ảnh tham chiếu chưa gắn vào ô prompt ({chips - chips0}/{len(task['refs'])})", tok)
        p = self.call("setPrompt", task["prompt"])
        if not p.get("ok"):
            self.stop_with(p.get("reason") or "ui_changed", p.get("detail"), tok)
        before = self.call("snapshot")
        st = self.call("state")
        if st.get("quota"):
            self.stop_with("quota", st.get("alerts"), tok)
        if self.should_stop():
            self.stop_with("stopped", "Người dùng bấm Dừng trước khi gửi", tok)  # chưa bấm Tạo → cảnh về READY
        # ---- điểm không quay lại: ghi SUBMITTING trước khi bấm ----
        pre = flow.pre_submit(self.cfg, tok)
        if not pre.get("ok"):
            raise Stop("Không gửi: " + str(pre.get("error")))
        self.status(f"{task['job_id']}/{task['scene_id']}: đã bấm Tạo, chờ Flow xác nhận")
        g = self.call("clickGenerateAndConfirm", task.get("submit_confirm_timeout_sec") or 60, before)
        if not g.get("ok"):
            if g.get("reason") in ("quota", "captcha", "login"):
                flow.blocked(self.cfg, g["reason"], g.get("detail") or "", tok)
            else:
                flow.submit_unknown(self.cfg, tok, f"{g.get('reason')}: {g.get('detail')}")
            raise Stop(f"KHÔNG RÕ Flow đã nhận cảnh {task['scene_id']} hay chưa ({g.get('reason')}). Xem Flow rồi chọn "
                       "Chờ tải hoặc Tạo lại.")
        evidence = dict(g.get("evidence") or {}, before_keys=before.get("keys") or [])
        flow.submitted(self.cfg, tok, evidence, (g.get("evidence") or {}).get("url") or "")
        return self.watch_and_download(task, evidence, before.get("keys") or [])

    def watch_and_download(self, task, evidence, before_keys):
        tok = task["token"]
        deadline = time.time() + float(task.get("generation_timeout_sec") or 900)
        w = None
        while time.time() < deadline:
            w = self.call("watch", evidence, before_keys, 50)
            pct = (w.get("tile") or {}).get("percent") or ""
            self.status(f"{task['job_id']}/{task['scene_id']}: Veo đang tạo {pct}".strip())
            if w.get("state") != "running":
                break
        if not w or w.get("state") == "running":
            flow.blocked(self.cfg, "generation_timeout", "timeout", tok)  # giữ GENERATING, lần sau theo dõi tiếp
            raise Stop("Quá thời gian chờ Flow tạo xong - cảnh vẫn GENERATING, bấm Chạy để theo dõi tiếp")
        if w.get("state") == "blocked":
            self.stop_with(w.get("reason"), w.get("detail"), tok)
        if w.get("state") == "failed":
            flow.gen_failed(self.cfg, tok, w.get("detail") or "")
            self.status(f"{task['scene_id']}: Flow báo tạo lỗi")
            return
        self.status(f"{task['job_id']}/{task['scene_id']}: tải clip")
        folder = Path(self.cfg["_downloads"])
        before = {p.name for p in folder.glob("*")} if folder.is_dir() else set()
        d = self.call("download", w["tile"]["key"], task.get("download_resolution_index") or 0)
        if not d.get("ok"):
            self.stop_with("ui_changed", "Tải xuống: " + str(d.get("detail")), tok)
        path = wait_download(folder, before, 300)
        if not path:
            self.stop_with("download_failed", "Không thấy file tải về trong 5 phút", tok)
        r = flow.downloaded(self.cfg, tok, str(path))
        if not r.get("ok"):
            raise Stop("Không nhận được file: " + str(r.get("error")))
        self.status(f"{task['scene_id']}: đã tải ({d.get('option')}) → chờ duyệt")


def wait_download(folder: Path, before: set, timeout: int):
    end = time.time() + timeout
    while time.time() < end:
        if folder.is_dir():
            for p in folder.iterdir():
                if p.name in before or p.suffix.lower() in (".crdownload", ".tmp", ".part") or not p.is_file():
                    continue
                size = p.stat().st_size
                time.sleep(1.5)
                if p.exists() and p.stat().st_size == size and size > 0:
                    return p
        time.sleep(1)
    return None
