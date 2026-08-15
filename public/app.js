const BASE = location.origin;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  convs: [],
  cur: null,
  model: "standard",
  attachments: [],
  imgMode: false,
  key: null,
};

/* ---------- key ---------- */
async function ensureKey() {
  if (state.key) {
    try {
      const r = await fetch(BASE + "/api/v1/keys", { headers: { Authorization: "Bearer " + state.key } });
      if (r.status !== 401) { renderKeys(); return state.key; }
    } catch (e) {}
    state.key = null;
  }
  try {
    const r = await fetch(BASE + "/api/v1/keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
    const j = await r.json();
    state.key = j.key;
    persist();
    renderKeys();
  } catch (e) { state.key = null; }
  return state.key;
}
function auth() { return { Authorization: "Bearer " + state.key }; }

// Browser-side tryit key for calling DeepAI image APIs directly (uses the
// visitor's own IP/quota and a UA-matched key, avoiding the server-IP limit).
let _tryitCache = null;
async function tryitKey() {
  if (_tryitCache) return _tryitCache;
  const r = await fetch(BASE + "/api/v1/tryit", { headers: { "X-Tryit-Ua": navigator.userAgent } });
  const j = await r.json();
  _tryitCache = j.api_key;
  return _tryitCache;
}

/* ---------- models ---------- */
async function loadModels() {
  try {
    const r = await fetch(BASE + "/api/v1/models");
    const j = await r.json();
    state.models = j.data || [];
    const sel = $("#modelSelect");
    sel.innerHTML = "";
    state.models.filter(m => ["text", "chat", "web"].includes(m.architecture.modality) || (m.architecture.input_modalities || []).includes("text") && !(m.architecture.output_modalities || []).includes("image")).forEach(m => {
      const o = document.createElement("option");
      o.value = m.id; o.textContent = m.name; sel.appendChild(o);
    });
    if (![...sel.options].some(o => o.value === state.model)) state.model = sel.value;
    sel.value = state.model;
    state.allModels = state.models;
  } catch (e) { console.warn("models", e); }
}

/* ---------- conversations ---------- */
function newConv() {
  const c = { id: "c" + Date.now(), title: "New chat", msgs: [], model: state.model };
  state.convs.unshift(c); state.cur = c.id; persist(); renderConvs(); renderChat();
}
function curConv() { return state.convs.find(c => c.id === state.cur); }
function renderConvs() {
  const list = $("#convList");
  list.innerHTML = "";
  if (!state.convs.length) { list.innerHTML = '<div class="empty">No chats yet</div>'; }
  state.convs.forEach(c => {
    const el = document.createElement("div");
    el.className = "conv-item" + (c.id === state.cur ? " active" : "");
    el.innerHTML = `<span class="ct">${esc(c.title)}</span><span class="acts">
      <button class="icon-mini rename" title="Rename">${ic("pen")}</button>
      <button class="icon-mini del" title="Delete">${ic("trash")}</button></span>`;
    el.onclick = (e) => { if (e.target.closest(".acts")) return; state.cur = c.id; persist(); renderConvs(); renderChat(); };
    el.querySelector(".rename").onclick = (e) => { e.stopPropagation(); const t = prompt("Rename", c.title); if (t) { c.title = t; persist(); renderConvs(); } };
    el.querySelector(".del").onclick = (e) => { e.stopPropagation(); state.convs = state.convs.filter(x => x.id !== c.id); if (state.cur === c.id) { state.cur = state.convs[0]?.id || null; if (!state.cur) newConv(); else { persist(); renderConvs(); renderChat(); } } else { persist(); renderConvs(); } };
    list.appendChild(el);
  });
}
function persist() { try { localStorage.setItem("tnxbd", JSON.stringify({ convs: state.convs, cur: state.cur, key: state.key })); } catch (e) {} }
function restore() { try { const s = JSON.parse(localStorage.getItem("tnxbd") || "{}"); state.convs = s.convs || []; state.cur = s.cur || null; state.key = s.key || null; } catch (e) {} }

/* ---------- chat render ---------- */
function renderChat() {
  const c = curConv();
  const wrap = $("#chatScroll");
  wrap.innerHTML = '<div class="chat-inner" id="chatInner"></div>';
  const inner = $("#chatInner");
  $("#convTitle").textContent = c ? c.title : "New chat";
  if (!c || !c.msgs.length) { inner.innerHTML = `<div class="empty">Start a conversation with ${state.model}.<br>Attach images/audio/files, or toggle the image button for on-the-fly generation.</div>`; return; }
  c.msgs.forEach((m, i) => inner.appendChild(msgEl(m, i)));
  scrollChat();
}
function msgEl(m, i) {
  const el = document.createElement("div");
  el.className = "msg " + m.role + (m.isImage ? " image" : "");
  let body;
  if (m.isImage) {
    body = `<img src="${m.content}" alt="generated" />`;
  } else {
    body = esc(m.content);
  }
  el.innerHTML = `<div class="bubble">${body}<div class="acts">
    <button class="edit" title="Edit">${ic("pen")}</button>
    <button class="del" title="Delete">${ic("trash")}</button></div></div>`;
  el.querySelector(".del").onclick = () => { const c = curConv(); c.msgs.splice(i, 1); persist(); renderChat(); };
  el.querySelector(".edit").onclick = () => {
    const c = curConv(); const t = prompt("Edit message", m.content);
    if (t !== null) { m.content = t; persist(); renderChat(); }
  };
  return el;
}
function scrollChat() { const s = $("#chatScroll"); s.scrollTop = s.scrollHeight; }

/* ---------- send ---------- */
async function send() {
  const c = curConv() || newConv();
  const txt = $("#msgInput").value.trim();
  if (!txt && !state.attachments.length) return;
  await ensureKey();
  const parts = [];
  if (txt) parts.push({ role: "user", content: txt });
  for (const a of state.attachments) parts.push({ role: "user", content: `[${a.type}] ${a.name}` });
  c.msgs.push(...parts);
  if (c.title === "New chat" && txt) c.title = txt.slice(0, 40);
  const images = state.attachments.filter(a => a.type.startsWith("image")).map(a => a.data);
  const files = state.attachments.filter(a => a.type.startsWith("audio") || a.type === "file").map(a => a.data);
  state.attachments = []; renderAtt(); $("#msgInput").value = ""; resizeTa(); persist(); renderConvs(); renderChat();

  if (state.imgMode && !files.length) {
    const ai = { role: "assistant", isImage: true, content: "" };
    c.msgs.push(ai); const idx = c.msgs.length - 1; persist(); renderChat();
    try {
      const r = await callImg(txt, 640);
      ai.content = r; persist(); renderChat();
    } catch (e) { ai.content = "⚠ " + e.message; persist(); renderChat(); }
    return;
  }
  const ai = { role: "assistant", content: "" };
  c.msgs.push(ai); const idx = c.msgs.length - 1; persist(); renderChat();
  $("#typing").hidden = false; scrollChat();
  try {
    const resp = await fetch(BASE + "/api/v1/chat/completions", {
      method: "POST", headers: { ...auth(), "Content-Type": "application/json" },
      body: JSON.stringify({ model: c.model, stream: true, messages: c.msgs.filter(m => !m.isImage).map(m => ({ role: m.role, content: m.content.replace(/^\[[^\]]+\]\s*/, "") })), images: images.length ? images : undefined, files: files.length ? files : undefined })
    });
    const reader = resp.body.getReader(); const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let p;
      while ((p = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, p).trim(); buf = buf.slice(p + 1);
        if (!line.startsWith("data:")) continue;
        const d = line.slice(5).trim(); if (d === "[DONE]") continue;
        try { const o = JSON.parse(d); const dd = o.choices?.[0]?.delta?.content; if (dd) { ai.content += dd; renderTail(ai); } } catch (e) {}
      }
    }
  } catch (e) { ai.content = "⚠ " + e.message; }
  $("#typing").hidden = true; persist(); renderChat();
}
function renderTail(m) { const inner = $("#chatInner"); const last = inner?.lastElementChild; if (last && last.classList.contains("assistant") && !last.classList.contains("image")) { last.querySelector(".bubble").firstChild ? (last.querySelector(".bubble").childNodes[0].nodeType === 3 ? (last.querySelector(".bubble").childNodes[0].textContent = m.content) : null) : null; scrollChat(); } else renderChat(); }

async function callImg(prompt, size = 640) {
  const fd = new FormData();
  fd.append("text", prompt);
  fd.append("width", String(size)); fd.append("height", String(size));
  fd.append("image_generator_version", "hd");
  fd.append("use_new_model", "true"); fd.append("use_old_model", "false");
  fd.append("quality", "true"); fd.append("generation_source", "img");
  const key = await tryitKey();
  const resp = await fetch("https://api.deepai.org/api/text2img", { method: "POST", headers: { "api-key": key }, body: fd });
  const j = await resp.json();
  if (j.output_url) return j.output_url;
  throw new Error(j.err || "image generation failed");
}

/* ---------- attachments ---------- */
function renderAtt() {
  const row = $("#attRow"); row.innerHTML = "";
  state.attachments.forEach((a, i) => {
    const chip = document.createElement("div"); chip.className = "att-chip";
    let preview = "";
    if (a.type.startsWith("image")) preview = `<img src="${a.data}">`;
    else if (a.type.startsWith("audio")) preview = `<audio controls src="${a.data}"></audio>`;
    chip.innerHTML = `${preview}<span class="nm">${esc(a.name)}</span><button class="x">×</button>`;
    chip.querySelector(".x").onclick = () => { state.attachments.splice(i, 1); renderAtt(); };
    row.appendChild(chip);
  });
}
function handleFiles(files) {
  [...files].forEach(f => {
    const reader = new FileReader();
    reader.onload = () => { state.attachments.push({ name: f.name, type: f.type || "file", data: reader.result }); renderAtt(); };
    reader.readAsDataURL(f);
  });
}

/* ---------- image page ---------- */
function renderImgMode() {
  const edit = $("#imgMode").value === "image-editor";
  $("#editOnly").style.display = edit ? "block" : "none";
}
async function generateImage() {
  const prompt = $("#imgPrompt").value.trim();
  if (!prompt) { $("#imgStatus").textContent = "Enter a prompt"; return; }
  const size = $("#imgSize").value;
  const [w, h] = size.split("x");
  const edit = $("#imgMode").value === "image-editor";
  $("#imgStatus").textContent = "Generating…"; $("#genBtn").disabled = true;
  try {
    const fd = new FormData();
    fd.append("text", prompt);
    fd.append("width", w); fd.append("height", h);
    fd.append("image_generator_version", "hd");
    fd.append("use_new_model", "true"); fd.append("use_old_model", "false");
    fd.append("quality", "true"); fd.append("generation_source", "img");
    let endpoint = "text2img";
    if (edit) {
      endpoint = "image-editor";
      const f = $("#imgFile").files[0];
      if (f) fd.append("image", f, f.name);
      else if ($("#imgUrl").value.trim()) {
        try {
          const blob = await (await fetch($("#imgUrl").value.trim())).blob();
          fd.append("image", blob, "img.png");
        } catch (e) { throw new Error("Could not load the image URL (CORS)"); }
      } else throw new Error("Provide a source image (upload or URL)");
    }
    const key = await tryitKey();
    const resp = await fetch("https://api.deepai.org/api/" + endpoint, { method: "POST", headers: { "api-key": key }, body: fd });
    const j = await resp.json();
    if (!j.output_url) throw new Error(j.err || "No image returned");
    const url = j.output_url;
    const grid = $("#imgGrid");
    const card = document.createElement("div"); card.className = "img-card";
    card.innerHTML = `<img src="${url}"><a class="dl" href="${url}" download>${ic("dl")}</a>`;
    grid.prepend(card); $("#imgStatus").textContent = "Done";
  } catch (e) { $("#imgStatus").textContent = "⚠ " + e.message; }
  finally { $("#genBtn").disabled = false; }
}
function fileData(f) { return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); }); }

/* ---------- models page ---------- */
function renderModelsPage() {
  const body = $("#modelsBody"); body.innerHTML = "";
  $("#modelCount").textContent = (state.allModels?.length || 0) + " models";
  (state.allModels || []).forEach(m => {
    const tr = document.createElement("tr");
    const typ = m.architecture.output_modalities?.includes("image") ? (m.id === "image-editor" ? "edit" : "image") : (m.architecture.modality === "web" ? "web" : "chat");
    const ctx = m.architecture.context_length || "—";
    const inputs = (m.architecture.input_modalities || []).join(", ");
    tr.innerHTML = `<td><b>${esc(m.name)}</b><br><span class="faint">${m.id}</span></td><td><span class="tag ${typ}">${typ}</span></td><td>${ctx}</td><td class="muted">${esc(m.description || "")}${inputs && typ !== "image" ? "<br><span class='faint'>in: " + inputs + "</span>" : ""}</td><td class="status" data-id="${m.id}">—</td><td><button class="text-btn use" data-id="${m.id}">Use</button></td>`;
    tr.querySelector(".use").onclick = () => { $("#modelSelect").value = m.id; state.model = m.id; if (curConv()) curConv().model = m.id; persist(); openPage("chat"); };
    body.appendChild(tr);
  });
}
async function testAll() {
  renderModelsPage();
  for (const m of (state.allModels || [])) {
    if ((m.architecture.output_modalities || []).includes("image")) continue;
    const cell = $(`[data-id="${m.id}"].status`); if (!cell) continue;
    cell.textContent = "…"; cell.className = "status";
    try {
      const r = await fetch(BASE + "/api/v1/chat/completions", { method: "POST", headers: { ...auth(), "Content-Type": "application/json" }, body: JSON.stringify({ model: m.id, stream: false, messages: [{ role: "user", content: "ping" }] }) });
      const j = await r.json();
      if (j.choices?.[0]?.message?.content) { cell.textContent = "OK"; cell.className = "status ok"; }
      else { cell.textContent = "?"; cell.className = "status fail"; }
    } catch (e) { cell.textContent = "ERR"; cell.className = "status fail"; }
  }
}

/* ---------- keys page ---------- */
function renderKeys() {
  const wrap = $("#keysList"); if (!wrap) return;
  if (!state.key) { wrap.innerHTML = '<p class="muted">Loading key…</p>'; return; }
  const role = state.key.includes("admin") ? "admin" : "public";
  wrap.innerHTML = `<div class="key-row"><span class="key-pill">${state.key}<span class="badge ${role}">${role}</span></span>
    <button class="copy-btn" id="copyKey">Copy</button></div>
    <p class="faint" style="margin-top:6px">Send <span class="kbd">Authorization: Bearer ${state.key}</span> with every request.</p>`;
  $("#copyKey").onclick = () => { navigator.clipboard.writeText(state.key); $("#copyKey").textContent = "Copied"; setTimeout(() => $("#copyKey").textContent = "Copy", 1200); };
}
async function genKey() {
  await ensureKey();
  const name = $("#keyName").value.trim() || "key";
  const r = await fetch(BASE + "/api/v1/keys", { method: "POST", headers: { ...auth(), "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
  const j = await r.json();
  if (j.key) { alert("New key created:\n" + j.key); }
  else alert("Could not create key (admin key required to make admin keys).");
}

/* ---------- helpers ---------- */
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function ic(n) {
  const p = { pen: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>', trash: '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>', dl: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>' };
  return `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${p[n] || ""}</svg>`;
}
function resizeTa() { const t = $("#msgInput"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; }

/* ---------- nav ---------- */
function openPage(p) {
  $$(".page").forEach(x => x.classList.remove("active"));
  $("#page-" + p).classList.add("active");
  $$(".side-item").forEach(x => x.classList.toggle("active", x.dataset.page === p));
  if (p === "models") renderModelsPage();
  if (p === "keys") renderKeys();
  closeSide();
}
function openSide() { $("#sidebar").classList.add("open"); $("#overlay").classList.add("show"); }
function closeSide() { $("#sidebar").classList.remove("open"); $("#overlay").classList.remove("show"); }

/* ---------- init ---------- */
async function init() {
  restore();
  if (!state.convs.length) { state.convs = []; newConv(); } else { renderConvs(); renderChat(); }
  await loadModels();
  $("#baseUrl").textContent = BASE; $("#baseUrl2").textContent = BASE;
  $$(".side-item").forEach(a => a.onclick = () => openPage(a.dataset.page));
  $("#newChat").onclick = newConv;
  $("#modelSelect").onchange = (e) => { state.model = e.target.value; if (curConv()) curConv().model = state.model; persist(); };
  $("#sendBtn").onclick = send;
  $("#msgInput").addEventListener("input", resizeTa);
  $("#msgInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  $("#imgToggle").onclick = () => { state.imgMode = !state.imgMode; $("#imgToggle").classList.toggle("on", state.imgMode); $("#imgHint").textContent = state.imgMode ? "Image mode: prompts generate images" : ""; };
  $("#attachBtn").onclick = () => $("#fileInput").click();
  $("#fileInput").onchange = (e) => handleFiles(e.target.files);
  $("#undoBtn").onclick = () => { const c = curConv(); if (c && c.msgs.length) { c.msgs.pop(); persist(); renderChat(); } };
  $("#retryBtn").onclick = async () => { const c = curConv(); if (!c) return; while (c.msgs.length && c.msgs[c.msgs.length - 1].role === "assistant") c.msgs.pop(); persist(); renderChat(); $("#msgInput").value = c.msgs.filter(m => m.role === "user").pop()?.content?.replace(/^\[[^\]]+\]\s*/, "") || ""; resizeTa(); send(); };
  $("#menuBtn").onclick = openSide; $("#closeSide").onclick = closeSide; $("#overlay").onclick = closeSide;
  $$("[data-menu]").forEach(b => b.onclick = openSide);
  $("#themeToggle").onclick = () => { const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = t; localStorage.setItem("tnxbd-theme", t); };
  document.documentElement.dataset.theme = localStorage.getItem("tnxbd-theme") || "light";
  $("#imgMode").onchange = renderImgMode; renderImgMode();
  $("#genBtn").onclick = generateImage;
  $("#testAllBtn").onclick = testAll;
  $("#genKeyBtn").onclick = genKey;
  renderKeys();
  ensureKey();
}
init();
