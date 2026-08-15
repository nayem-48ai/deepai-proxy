"use strict";
const $ = (s) => document.querySelector(s);
const LS_KEY = "tnxbd_key";
const LS_CONV = "tnxbd_conversations";
const AUTH = () => ({ "Content-Type": "application/json", Authorization: "Bearer " + API_KEY });

let API_KEY = localStorage.getItem(LS_KEY) || "";
let conversations = loadConv();
let activeId = conversations[0] ? conversations[0].id : null;
let imgMode = false;
let modelList = [];

// ---------- key bootstrap ----------
async function ensureKey() {
  if (API_KEY) return;
  try {
    const r = await fetch("/api/v1/keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "site" }) });
    const d = await r.json();
    API_KEY = d.key;
    localStorage.setItem(LS_KEY, API_KEY);
  } catch (e) { console.warn("key gen failed", e); }
}

// ---------- conversations ----------
function loadConv() {
  try { return JSON.parse(localStorage.getItem(LS_CONV)) || []; } catch (e) { return []; }
}
function saveConv() { localStorage.setItem(LS_CONV, JSON.stringify(conversations)); }
function activeConv() { return conversations.find((c) => c.id === activeId) || null; }
function newChat() {
  const c = { id: "c_" + Date.now() + Math.random().toString(36).slice(2, 7), title: "New chat", model: "standard", messages: [], createdAt: Date.now(), updatedAt: Date.now() };
  conversations.unshift(c); activeId = c.id; saveConv();
  showPage("chat"); renderSidebar(); renderChat(); $("#msgInput").focus();
}
function selectChat(id) { activeId = id; saveConv(); showPage("chat"); renderSidebar(); renderChat(); }
function deleteChat(id) {
  conversations = conversations.filter((c) => c.id !== id);
  if (activeId === id) activeId = conversations[0] ? conversations[0].id : null;
  if (!conversations.length) newChat(); else { saveConv(); renderSidebar(); renderChat(); }
}
function renameChat(id) {
  const c = conversations.find((x) => x.id === id); if (!c) return;
  const t = prompt("Rename conversation", c.title); if (t && t.trim()) { c.title = t.trim(); saveConv(); renderSidebar(); if (id === activeId) $("#convTitle").textContent = c.title; }
}

// ---------- sidebar ----------
function renderSidebar() {
  const list = $("#convList");
  if (!conversations.length) { list.innerHTML = `<div class="empty" style="padding:20px">No chats yet</div>`; return; }
  list.innerHTML = conversations.map((c) => `
    <div class="conv-item ${c.id === activeId ? "active" : ""}" data-id="${c.id}">
      <span class="ct">${esc(c.title || "Untitled")}</span>
      <span class="acts">
        <button class="icon-mini" data-rename="${c.id}" title="Rename"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg></button>
        <button class="icon-mini" data-del="${c.id}" title="Delete"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg></button>
      </span>
    </div>`).join("");
  list.querySelectorAll(".conv-item").forEach((el) => {
    el.addEventListener("click", (e) => { if (e.target.closest(".acts")) return; selectChat(el.dataset.id); });
    const rn = el.querySelector("[data-rename]"); if (rn) rn.addEventListener("click", () => renameChat(rn.dataset.rename));
    const dl = el.querySelector("[data-del]"); if (dl) dl.addEventListener("click", () => { if (confirm("Delete this conversation?")) deleteChat(dl.dataset.del); });
  });
}

// ---------- chat render ----------
function esc(s) { return (s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
function makeMsgEl(m, idx) {
  const wrap = document.createElement("div");
  wrap.className = "msg " + m.role;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const acts = document.createElement("div");
  acts.className = "acts";
  if (m.role === "assistant" || m.role === "image") {
    const retry = document.createElement("button");
    retry.title = "Retry";
    retry.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>';
    retry.addEventListener("click", () => retryFrom(idx));
    acts.appendChild(retry);
  }
  const del = document.createElement("button");
  del.title = "Delete";
  del.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>';
  del.addEventListener("click", () => { conversations.find((c) => c.id === activeId).messages.splice(idx, 1); saveConv(); renderChat(); });
  acts.appendChild(del);

  if (m.role === "image") {
    if (m.url) {
      bubble.innerHTML = `<img src="${m.url}" alt="result" /><a class="dl" href="${m.url}" target="_blank" download style="position:absolute;top:6px;right:6px;background:rgba(15,23,42,.7);color:#fff;width:28px;height:28px;border-radius:7px;display:grid;place-items:center"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14"/></svg></a>`;
    } else {
      bubble.textContent = m.content || "Generating…";
    }
  } else {
    bubble.textContent = m.content || "";
  }
  wrap.appendChild(acts); wrap.appendChild(bubble);
  return wrap;
}
function renderChat() {
  const conv = activeConv();
  $("#convTitle").textContent = conv ? conv.title : "New chat";
  $("#modelSelect").value = conv ? conv.model : "standard";
  const scroll = $("#chatScroll");
  if (!conv || !conv.messages.length) {
    scroll.innerHTML = `<div class="chat-inner"><div class="empty">Start a conversation. Toggle the image button to generate images inline.</div></div>`;
    return;
  }
  const inner = document.createElement("div"); inner.className = "chat-inner";
  conv.messages.forEach((m, i) => inner.appendChild(makeMsgEl(m, i)));
  scroll.innerHTML = ""; scroll.appendChild(inner);
  scroll.scrollTop = scroll.scrollHeight;
}

// ---------- sending ----------
function getApiMessages(conv) {
  return conv.messages.filter((m) => !(m.role === "assistant" && m.content === ""));
}
async function streamAssistant(conv) {
  const msg = { role: "assistant", content: "" };
  conv.messages.push(msg);
  const inner = $("#chatScroll .chat-inner") || (() => { renderChat(); return $("#chatScroll .chat-inner"); })();
  const el = makeMsgEl(msg, conv.messages.length - 1);
  inner.appendChild(el);
  const bubble = el.querySelector(".bubble");
  $("#chatScroll").scrollTop = $("#chatScroll").scrollHeight;
  const r = await fetch("/api/v1/chat/completions", { method: "POST", headers: AUTH(), body: JSON.stringify({ model: conv.model, messages: getApiMessages(conv).slice(0, -1), stream: true }) });
  const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "", full = "";
  while (true) {
    const { done, value } = await reader.read(); if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split("\n"); buf = lines.pop();
    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      const p = line.slice(5).trim(); if (p === "[DONE]") continue;
      try { const j = JSON.parse(p); const d = j.choices?.[0]?.delta?.content || ""; if (d) { full += d; bubble.textContent = full; $("#chatScroll").scrollTop = $("#chatScroll").scrollHeight; } } catch (e) {}
    }
  }
  msg.content = full; conv.updatedAt = Date.now(); saveConv(); renderSidebar();
}
async function send() {
  const text = $("#msgInput").value.trim(); if (!text) return;
  const conv = activeConv(); if (!conv) return;
  if (imgMode) { $("#msgInput").value = ""; return generateInlineImage(text); }
  conv.messages.push({ role: "user", content: text });
  if (conv.messages.length === 1) { conv.title = text.slice(0, 42); $("#convTitle").textContent = conv.title; }
  $("#msgInput").value = "";
  renderChat(); saveConv(); renderSidebar();
  await streamAssistant(conv);
}
async function generateInlineImage(prompt) {
  const conv = activeConv();
  const m = { role: "image", content: prompt, url: null };
  conv.messages.push(m); renderChat(); saveConv();
  try {
    const r = await fetch("/api/v1/images/generations", { method: "POST", headers: AUTH(), body: JSON.stringify({ prompt, size: "640x640" }) });
    const d = await r.json();
    if (d.error) m.content = "Image error: " + d.error; else m.url = d.data?.[0]?.url;
  } catch (e) { m.content = "Image error: " + e.message; }
  saveConv(); renderChat();
}
function retryFrom(idx) {
  const conv = activeConv();
  conv.messages = conv.messages.slice(0, idx + 1);
  saveConv(); renderChat();
  streamAssistant(conv);
}
function undoLast() {
  const conv = activeConv(); if (!conv || !conv.messages.length) return;
  conv.messages.pop();
  if (conv.messages.length && conv.messages[conv.messages.length - 1].role === "assistant") conv.messages.pop();
  if (conv.messages.length && conv.messages[conv.messages.length - 1].role === "user") conv.messages.pop();
  saveConv(); renderChat(); renderSidebar();
}

// ---------- nav ----------
const titles = { chat: "Chat", images: "Images", models: "Models", keys: "API Keys", docs: "Docs" };
function showPage(p) {
  document.querySelectorAll(".side-item").forEach((n) => n.classList.toggle("active", n.dataset.page === p));
  document.querySelectorAll(".page").forEach((pg) => pg.classList.toggle("active", pg.id === "page-" + p));
  if (p === "models") loadModelsTable();
  if (p === "keys") renderKeys();
}
$("#sideNav").addEventListener("click", (e) => { const a = e.target.closest(".side-item"); if (a) showPage(a.dataset.page); });

// ---------- theme ----------
$("#themeToggle").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next); localStorage.setItem("theme", next);
});
if (localStorage.getItem("theme") === "dark") document.documentElement.setAttribute("data-theme", "dark");

// ---------- composer ----------
$("#sendBtn").addEventListener("click", send);
$("#msgInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
$("#newChat").addEventListener("click", newChat);
$("#undoBtn").addEventListener("click", undoLast);
$("#retryBtn").addEventListener("click", () => retryFrom((activeConv()?.messages.length || 1) - 1));
$("#imgToggle").addEventListener("click", () => {
  imgMode = !imgMode; $("#imgToggle").classList.toggle("on", imgMode);
  $("#imgHint").textContent = imgMode ? "Image mode — your message generates an image" : "";
  $("#msgInput").placeholder = imgMode ? "Describe an image to generate…" : "Send a message…  (Enter to send, Shift+Enter for newline)";
});
$("#modelSelect").addEventListener("change", () => { const c = activeConv(); if (c) { c.model = $("#modelSelect").value; saveConv(); } });

// ---------- models (registry) ----------
async function loadModels() {
  const r = await fetch("/api/models"); modelList = await r.json();
  const sel = $("#modelSelect");
  let opts = "";
  for (const [cat, c] of Object.entries(modelList.categories)) {
    if (c.models[0] && c.models[0].type) continue;
    opts += `<optgroup label="${c.label}">` + c.models.map((m) => `<option value="${m.id}">${m.label}</option>`).join("") + `</optgroup>`;
  }
  sel.innerHTML = opts;
}
function allModelList() { const out = []; for (const c of Object.values(modelList.categories)) for (const m of c.models) out.push({ ...m, cat: c.label }); return out; }
async function loadModelsTable() {
  const body = $("#modelsBody");
  body.innerHTML = allModelList().map((m) => {
    let tag = `<span class="tag">chat</span>`;
    if (m.type === "image") tag = `<span class="tag img">image</span>`;
    if (m.cat === "Web Search") tag = `<span class="tag web">web</span>`;
    const ctx = m.context_length ? m.context_length.toLocaleString() : "—";
    return `<tr data-id="${m.id}"><td><b>${m.label}</b></td><td>${tag}</td><td class="muted">${ctx}</td><td class="muted">${m.note || ""}</td><td class="status" id="st-${m.id}">—</td><td><button class="btn sm ghost" data-test="${m.id}">Test</button></td></tr>`;
  }).join("");
  $("#modelCount").textContent = allModelList().length + " models";
  body.querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", () => testModel(b.dataset.test)));
}
async function testModel(id) {
  const el = $("#st-" + id); el.textContent = "testing…"; el.className = "status";
  const t0 = performance.now();
  try {
    if (id === "text2img" || id === "image-editor") {
      const r = await fetch("/api/v1/images/generations", { method: "POST", headers: AUTH(), body: JSON.stringify({ prompt: "a cat", size: "512x512" }) });
      const d = await r.json(); if (d.error) throw new Error(d.error);
      el.textContent = "ok " + Math.round(performance.now() - t0) + "ms"; el.className = "status ok";
    } else {
      const r = await fetch("/api/v1/chat/completions", { method: "POST", headers: AUTH(), body: JSON.stringify({ model: id, messages: [{ role: "user", content: "reply with one word: hi" }], stream: false }) });
      const d = await r.json(); if (d.error) throw new Error(d.error);
      el.textContent = "ok " + Math.round(performance.now() - t0) + "ms"; el.className = "status ok";
    }
  } catch (e) { el.textContent = "fail: " + e.message.slice(0, 40); el.className = "status fail"; }
}
$("#testAllBtn").addEventListener("click", async () => { for (const m of allModelList()) { await testModel(m.id); await new Promise((r) => setTimeout(r, 250)); } });

// ---------- keys ----------
async function renderKeys() {
  const r = await fetch("/api/v1/keys"); const d = await r.json();
  $("#keysList").innerHTML = (d.data || []).map((k) => `<div class="code" style="margin-bottom:8px">${k.name || "key"} — ${k.key}</div>`).join("");
}
$("#genKeyBtn").addEventListener("click", async () => {
  const name = $("#keyName").value.trim() || "key";
  const r = await fetch("/api/v1/keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
  const d = await r.json(); if (d.key) { API_KEY = d.key; localStorage.setItem(LS_KEY, API_KEY); }
  renderKeys();
});

// ---------- images page ----------
$("#imgMode").addEventListener("change", () => { $("#editOnly").style.display = $("#imgMode").value === "image-editor" ? "block" : "none"; });
async function genImage() {
  const btn = $("#genBtn"); btn.disabled = true; $("#imgStatus").textContent = "working…";
  const mode = $("#imgMode").value; const prompt = $("#imgPrompt").value.trim();
  if (!prompt) { $("#imgStatus").textContent = "prompt required"; btn.disabled = false; return; }
  const payload = { prompt, size: $("#imgSize").value };
  if (mode === "image-editor") {
    const file = $("#imgFile").files[0];
    if (file) payload.image = await fileToDataURL(file);
    else if ($("#imgUrl").value.trim()) payload.image = $("#imgUrl").value.trim();
    else { $("#imgStatus").textContent = "provide a source image"; btn.disabled = false; return; }
  }
  try {
    const endpoint = mode === "image-editor" ? "/api/v1/images/edits" : "/api/v1/images/generations";
    const r = await fetch(endpoint, { method: "POST", headers: AUTH(), body: JSON.stringify(payload) });
    const d = await r.json(); if (d.error) throw new Error(d.error);
    const url = d.data?.[0]?.url; if (!url) throw new Error("no image returned");
    const grid = $("#imgGrid");
    const card = document.createElement("div"); card.className = "img-card";
    card.innerHTML = `<img src="${url}" alt="result" /><a class="dl" href="${url}" target="_blank" download><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14"/></svg></a>`;
    grid.prepend(card); $("#imgStatus").textContent = "done";
  } catch (e) { $("#imgStatus").textContent = "error: " + e.message.slice(0, 60); }
  btn.disabled = false;
}
function fileToDataURL(f) { return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); }); }
$("#genBtn").addEventListener("click", genImage);

// ---------- init ----------
(async function init() {
  await ensureKey();
  await loadModels();
  $("#baseUrl").textContent = location.origin + "/api/v1";
  if (!conversations.length) newChat(); else { renderSidebar(); renderChat(); }
  renderKeys();
})();
