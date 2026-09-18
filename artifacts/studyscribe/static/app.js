const state = {
  action: "summary",
  selectedFile: null,
  recording: null,
  recorder: null,
  chunks: [],
  timer: null,
  seconds: 0,
  lastOutput: "",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function getApiKey() {
  return sessionStorage.getItem("studyscribe_gemini_key") || $("#apiKey").value.trim();
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 2700);
}

function showAlert(message) {
  const alert = $("#alert");
  alert.textContent = message;
  alert.classList.remove("d-none");
}

function clearAlert() {
  $("#alert").classList.add("d-none");
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[char]));
}

function renderMarkdown(markdown) {
  let html = escapeHtml(markdown);
  html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/^# (.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/^\s*[-*] (.+)$/gm, "<li>$1</li>");
  html = html.replace(/(<li>.*<\/li>\n?)+/g, (items) => `<ul>${items}</ul>`);
  html = html.split(/\n{2,}/).map((block) => {
    if (block.trim().startsWith("<h") || block.trim().startsWith("<ul")) return block;
    return `<p>${block.replace(/\n/g, "<br>")}</p>`;
  }).join("");
  return html;
}

function setLoading(loading, label = "Generate study material") {
  const button = $("#generateButton");
  button.disabled = loading;
  button.classList.toggle("is-loading", loading);
  button.querySelector(".button-label").textContent = loading ? "Working with Gemini" : label;
  button.querySelector("i").className = loading ? "bi bi-arrow-repeat spin" : "bi bi-arrow-right";
}

function renderResult(result, action, sourceLabel = "StudyScribe") {
  state.lastOutput = result;
  const labels = { summary: "Quick summary", notes: "Study notes", quiz: "Practice quiz", assignment: "Assignment" };
  $("#outputCard").innerHTML = `
    <div class="result-meta"><i class="bi bi-stars"></i><span>${labels[action] || "Study material"} · ${sourceLabel}</span><span class="ms-auto">${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span></div>
    <div class="result-body">${renderMarkdown(result)}</div>
  `;
  $("#outputActions").classList.remove("d-none");
  $("#outputSection").scrollIntoView({ behavior: "smooth", block: "start" });
  addRecentItem(sourceLabel, labels[action] || "Study material", action);
}

function addRecentItem(title, subtitle, action) {
  const icons = { summary: ["file-earmark-text", "purple"], notes: ["journal-text", "green"], quiz: ["patch-question", "orange"], assignment: ["pencil", "purple"] };
  const [icon, color] = icons[action] || icons.summary;
  const list = $("#recentList");
  const item = document.createElement("div");
  item.className = "recent-item";
  item.innerHTML = `<span class="recent-file ${color}"><i class="bi bi-${icon}"></i></span><div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(subtitle)} · just now</small></div><i class="bi bi-arrow-up-right recent-arrow"></i>`;
  list.prepend(item);
}

async function generate() {
  clearAlert();
  const apiKey = getApiKey();
  if (!apiKey) return showAlert("Add your Gemini API key at the top to start generating.");
  const sourceText = $("#sourceText").value.trim();
  if (!sourceText && !state.selectedFile) return showAlert("Paste some material or upload a PDF/image first.");
  setLoading(true);
  try {
    let response;
    if (state.selectedFile) {
      const form = new FormData();
      form.append("api_key", apiKey);
      form.append("action", state.action);
      form.append("file", state.selectedFile);
      response = await fetch("/studyscribe-api/analyze-file", { method: "POST", body: form });
    } else {
      response = await fetch("/studyscribe-api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey, action: state.action, content: sourceText }),
      });
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Something went wrong.");
    renderResult(data.result, state.action, data.filename || "Pasted material");
    $("#sessionState").textContent = "Study material ready";
    showToast("Your study material is ready.");
  } catch (error) {
    showAlert(error.message);
  } finally {
    setLoading(false);
  }
}

function updateTimer() {
  const minutes = String(Math.floor(state.seconds / 60)).padStart(2, "0");
  const seconds = String(state.seconds % 60).padStart(2, "0");
  $("#timer").textContent = `${minutes}:${seconds}`;
}

async function startRecording() {
  clearAlert();
  if (!getApiKey()) return showAlert("Add your Gemini API key before recording so the transcript can be created.");
  if (!navigator.mediaDevices?.getUserMedia) return showAlert("Audio recording is not supported in this browser.");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.chunks = [];
    state.recorder = new MediaRecorder(stream);
    state.recorder.ondataavailable = (event) => event.data.size && state.chunks.push(event.data);
    state.recorder.onstop = () => {
      stream.getTracks().forEach((track) => track.stop());
      state.recording = new Blob(state.chunks, { type: state.recorder.mimeType || "audio/webm" });
      $("#recordStatus").textContent = "Recording ready. Send it to Gemini for a clean transcript.";
      $("#recordButton").classList.add("d-none");
      $("#transcribeButton").classList.remove("d-none");
    };
    state.recorder.start();
    state.seconds = 0;
    updateTimer();
    state.timer = window.setInterval(() => { state.seconds += 1; updateTimer(); }, 1000);
    $(".record-card").classList.add("recording");
    $("#recordButton").classList.add("active");
    $("#recordButton").querySelector("span").textContent = "Stop recording";
    $("#recordButton").querySelector("i").className = "bi bi-stop-fill";
    $("#recordStatus").textContent = "Listening… your lecture is being captured in this browser.";
  } catch (error) {
    showAlert("Microphone access was blocked. Allow microphone access and try again.");
  }
}

function stopRecording() {
  if (!state.recorder || state.recorder.state === "inactive") return;
  window.clearInterval(state.timer);
  state.recorder.stop();
  $(".record-card").classList.remove("recording");
  $("#recordButton").classList.remove("active");
}

async function transcribe() {
  if (!state.recording) return;
  const button = $("#transcribeButton");
  button.disabled = true;
  button.querySelector("span").textContent = "Transcribing with Gemini";
  try {
    const form = new FormData();
    form.append("api_key", getApiKey());
    form.append("audio", state.recording, "lecture.webm");
    const response = await fetch("/studyscribe-api/transcribe", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Transcription failed.");
    $("#sourceText").value = data.transcript;
    $("#writePanel").classList.add("active");
    $("#uploadPanel").classList.remove("active");
    $$(".source-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === "write"));
    $("#wordCount").textContent = `${data.transcript.trim().split(/\s+/).filter(Boolean).length} words`;
    $("#recordStatus").textContent = "Transcript added to your source material. Choose a study format to continue.";
    $("#recordButton").classList.remove("d-none");
    $("#transcribeButton").classList.add("d-none");
    $("#recordButton").querySelector("span").textContent = "Record again";
    $("#recordButton").querySelector("i").className = "bi bi-mic-fill";
    showToast("Transcript added to your source.");
    document.querySelector(".composer-column").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showAlert(error.message);
  } finally {
    button.disabled = false;
    button.querySelector("span").textContent = "Transcribe recording";
  }
}

function setup() {
  const storedKey = sessionStorage.getItem("studyscribe_gemini_key");
  if (storedKey) { $("#apiKey").value = storedKey; $("#sessionState").textContent = "Gemini key connected"; }
  $("#saveKey").addEventListener("click", () => {
    const key = $("#apiKey").value.trim();
    if (!key) return showAlert("Paste your Gemini API key first.");
    sessionStorage.setItem("studyscribe_gemini_key", key);
    $("#sessionState").textContent = "Gemini key connected";
    showToast("Key saved for this browser session.");
  });
  $("#toggleKey").addEventListener("click", () => {
    const input = $("#apiKey");
    input.type = input.type === "password" ? "text" : "password";
    $("#toggleKey i").className = input.type === "password" ? "bi bi-eye" : "bi bi-eye-slash";
  });
  $("#sourceText").addEventListener("input", () => {
    const count = $("#sourceText").value.trim().split(/\s+/).filter(Boolean).length;
    $("#wordCount").textContent = `${count} word${count === 1 ? "" : "s"}`;
  });
  $$(".source-tab").forEach((tab) => tab.addEventListener("click", () => {
    $$(".source-tab").forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    $("#writePanel").classList.toggle("active", tab.dataset.tab === "write");
    $("#uploadPanel").classList.toggle("active", tab.dataset.tab === "upload");
  }));
  $$(".action-card").forEach((card) => card.addEventListener("click", () => {
    state.action = card.dataset.action;
    $$(".action-card").forEach((item) => {
      item.classList.toggle("selected", item === card);
      item.querySelector(".select-check").className = item === card ? "bi bi-check-circle-fill select-check" : "bi bi-circle select-check";
    });
  }));
  $("#generateButton").addEventListener("click", generate);
  $("#recordButton").addEventListener("click", () => state.recorder?.state === "recording" ? stopRecording() : startRecording());
  $("#transcribeButton").addEventListener("click", transcribe);
  $("#fileInput").addEventListener("change", (event) => {
    state.selectedFile = event.target.files[0] || null;
    if (state.selectedFile) {
      $("#filePreview").innerHTML = `<i class="bi bi-file-earmark-check"></i><strong>${escapeHtml(state.selectedFile.name)}</strong> <span class="ms-1">· ${(state.selectedFile.size / 1024 / 1024).toFixed(2)} MB</span>`;
      $("#filePreview").classList.remove("d-none");
    }
  });
  const dropzone = $("#dropzone");
  ["dragenter", "dragover"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => { event.preventDefault(); dropzone.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => { event.preventDefault(); dropzone.classList.remove("dragging"); }));
  dropzone.addEventListener("drop", (event) => {
    state.selectedFile = event.dataTransfer.files[0] || null;
    if (state.selectedFile) {
      $("#filePreview").innerHTML = `<i class="bi bi-file-earmark-check"></i><strong>${escapeHtml(state.selectedFile.name)}</strong> <span class="ms-1">· ${(state.selectedFile.size / 1024 / 1024).toFixed(2)} MB</span>`;
      $("#filePreview").classList.remove("d-none");
    }
  });
  $("#copyOutput").addEventListener("click", async () => { await navigator.clipboard.writeText(state.lastOutput); showToast("Copied to clipboard."); });
  $("#downloadOutput").addEventListener("click", () => {
    const blob = new Blob([state.lastOutput], { type: "text/markdown" });
    const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = "studyscribe-material.md"; link.click(); URL.revokeObjectURL(link.href);
  });
  $("#clearRecent").addEventListener("click", () => { $("#recentList").innerHTML = ""; showToast("Recent outputs cleared."); });
  $("#mobileMenu").addEventListener("click", () => $(".sidebar").classList.toggle("open"));
  $$(".side-link").forEach((link) => link.addEventListener("click", () => $(".sidebar").classList.remove("open")));
  document.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") generate(); });
}

setup();