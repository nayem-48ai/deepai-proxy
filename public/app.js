"use strict";
const $ = (s) => document.querySelector(s);
let API_KEY = localStorage.getItem("apiKey") || "";
const base = ""; // same origin

// ---------- key bootstrap ----------
async function ensureKey() {
  if (API_KEY) return;
  try {
    const r = await fetch("/api/keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "site" }) });
    const d = await r.json();
    API_KEY = d.key;
    localStorage.setItem("apiKey", API_KEY);
  } catch (e) { console.warn("key gen failed", e); }
}
function auth() { return { "Content-Type": "application/json", Authorization: "Bearer " + API_KEY }; }

// ---------- nav ----------
const titles = { chat: "Chat", images: "Images", models: "Models", keys: "API Keys", docs: "Docs" };
$("#nav").addEventListener("click", (e) => {
  const a = e.target.closest(".nav-item"); if (!a) return;
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
  document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
  a.classList.add("active");
  const p = a.dataset.page;
  $("#page-" + p).classList.add("active");
  $("#pageTitle").textContent = titles[p];
});

// ---------- theme ----------
$("#themeToggle").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("theme", next);
});
if (localStorage.getItem("theme") === "dark") document.documentElement.setAttribute("data-theme", "dark");

// ---------- models ----------
let MODELS = null;
async function loadModels() {
  const r = await fetch("/api/models");
  MODELS = await r.json();
  const chatSel = $("#chatModel");
  let opts = "";
  for (const [cat, c] of Object.entries(MODELS.categories)) {
    if (c.models[0]?.type) continue; // skip image category in chat select
    opts += `<optgroup label="${c.label}">` + c.models.map((m) => `<option value="${m.id}">${m.label}</option>`).join("") + `</optgroup>`;
  }
  chatSel.innerHTML = opts;
  renderModelsTable();
  $("#modelCount").textContent = allModelList().length + " models";
  $("#baseUrl").textContent = location.origin + "/api";
}
function allModelList() {
  const out = [];
  for (const c of Object.values(MODELS.categories)) for (const m of c.models) out.push({ ...m, cat: c.label });
  return out;
}
function renderModelsTable() {
  const body = $("#modelsBody");
  body.innerHTML = allModelList().map((m) => {
    let tag = `<span class="tag">chat</span>`;
    if (m.type === "image") tag = `<span class="tag img">image</span>`;
    if (m.cat === "Web Search") tag = `<span class="tag web">web</span>`;
    return `<tr data-id="${m.id}">
      <td><b>${m.label}</b></td>
      <td>${tag}</td>
      <td class="muted">${m.note || ""}</td>
      <td class="status" id="st-${m.id}">—</td>
      <td><button class="btn sm ghost" data-test="${m.id}">Test</button></td>
    </tr>`;
  }).join("");
  body.querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", () => testModel(b.dataset.test)));
}

async function testModel(id) {
  const el = $("#st-" + id);
  el.textContent = "testing…"; el.className = "status";
  const t0 = performance.now();
  try {
    if (id === "text2img" || id === "image-editor") {
      const r = await fetch("/api/images/generations", { method: "POST", headers: auth(), body: JSON.stringify({ prompt: "a cat", size: "512x512" }) });
      const d = await r.json();
      if (d.error) throw new Error(d.error);
      el.textContent = "ok " + Math.round(performance.now() - t0) + "ms"; el.className = "status ok";
    } else {
      const r = await fetch("/api/chat", { method: "POST", headers: auth(), body: JSON.stringify({ model: id, messages: [{ role: "user", content: "reply with one word: hi" }], stream: false }) });
      const d = await r.json();
      if (d.error) throw new Error(d.error);
      el.textContent = "ok " + Math.round(performance.now() - t0) + "ms"; el.className = "status ok";
    }
  } catch (e) {
    el.textContent = "fail: " + e.message.slice(0, 40); el.className = "status fail";
  }
}
$("#testAllBtn").addEventListener("click", async () => {
  for (const m of allModelList()) {
    const el = $("#st-" + m.id);
    if (el && el.textContent !== "ok" || !el) await testModel(m.id);
    else await testModel(m.id);
    await new Promise((r) => setTimeout(r, 300));
  }
});

// ---------- chat ----------
const chatBox = $("#chatBox");
function addMsg(role, text) {
  const d = document.createElement("div");
  d.className = "msg " + role;
  d.textContent = text || "";
  chatBox.appendChild(d);
  chatBox.scrollTop = chatBox.scrollHeight;
  return d;
}
let chatHistory = [];
async function send() {
  const text = $("#chatText").value.trim();
  if (!text) return;
  $("#chatText").value = "";
  addMsg("user", text);
  chatHistory.push({ role: "user", content: text });
  const bubble = addMsg("assistant", "");
  const model = $("#chatModel").value;
  try {
    const r = await fetch("/api/chat", { method: "POST", headers: auth(), body: JSON.stringify({ model, messages: chatHistory, stream: true }) });
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "", full = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (payload === "[DONE]") continue;
        try {
          const j = JSON.parse(payload);
          const delta = j.choices?.[0]?.delta?.content || "";
          if (delta) { full += delta; bubble.textContent = full; chatBox.scrollTop = chatBox.scrollHeight; }
        } catch (e) {}
      }
    }
    chatHistory.push({ role: "assistant", content: full });
  } catch (e) {
    bubble.textContent = "Error: " + e.message;
  }
}
$("#sendBtn").addEventListener("click", send);
$("#chatText").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });

// ---------- images ----------
$("#imgMode").addEventListener("change", () => {
  $("#editOnly").style.display = $("#imgMode").value === "image-editor" ? "block" : "none";
});
async function generate() {
  const btn = $("#genBtn"); btn.disabled = true; $("#imgStatus").textContent = "working…";
  const mode = $("#imgMode").value;
  const prompt = $("#imgPrompt").value.trim();
  if (!prompt) { $("#imgStatus").textContent = "prompt required"; btn.disabled = false; return; }
  let payload = { prompt, size: $("#imgSize").value };
  if (mode === "image-editor") {
    const file = $("#imgFile").files[0];
    if (file) {
      payload.image = await fileToDataURL(file);
    } else if ($("#imgUrl").value.trim()) {
      payload.image = $("#imgUrl").value.trim();
    } else { $("#imgStatus").textContent = "provide a source image"; btn.disabled = false; return; }
  }
  try {
    const endpoint = mode === "image-editor" ? "/api/images/edits" : "/api/images/generations";
    const r = await fetch(endpoint, { method: "POST", headers: auth(), body: JSON.stringify(payload) });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    const url = d.data?.[0]?.url;
    if (!url) throw new Error("no image returned");
    const grid = $("#imgGrid");
    const card = document.createElement("div");
    card.className = "img-card";
    card.innerHTML = `<img src="${url}" alt="result" /><a class="dl" href="${url}" target="_blank" download><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14"/></svg></a>`;
    grid.prepend(card);
    $("#imgStatus").textContent = "done";
  } catch (e) {
    $("#imgStatus").textContent = "error: " + e.message.slice(0, 60);
  }
  btn.disabled = false;
}
function fileToDataURL(f) { return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); }); }
$("#genBtn").addEventListener("click", generate);

// ---------- keys ----------
async function renderKeys() {
  const r = await fetch("/api/keys");
  const d = await r.json();
  $("#keysList").innerHTML = (d.keys || []).map((k) => `<div class="code" style="margin-bottom:8px">${k.name || "key"} — ${k.key}</div>`).join("");
}
$("#genKeyBtn").addEventListener("click", async () => {
  const name = $("#keyName").value.trim() || "key";
  const r = await fetch("/api/keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
  const d = await r.json();
  if (d.key) { API_KEY = d.key; localStorage.setItem("apiKey", API_KEY); }
  renderKeys();
});

// ---------- init ----------
(async function init() {
  await ensureKey();
  await loadModels();
  renderKeys();
})();
