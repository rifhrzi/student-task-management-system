"use strict";

const $ = (selector) => document.querySelector(selector);
const PAGE_SIZE = 20;
const MAX_FILE_SIZE = 10 * 1024 * 1024;
const sessionKey = "student-tasks-token";
let token = "";
try { token = sessionStorage.getItem(sessionKey) || ""; } catch { /* Private storage may be disabled. */ }
let user = null;
let registering = false;
let tasks = [];
let taskOffset = 0;
let selected = null;
let submissions = [];
let selectionVersion = 0;

const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);
const date = (value) => new Date(value).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" });
const deadline = (task) => task.due_at ? `Tenggat ${date(task.due_at)}` : "Tanpa tenggat";
const isOwner = (task) => user?.role === "lecturer" && task.lecturer_id === user.id;
const isLate = (task) => task.due_at && new Date(task.due_at) < new Date();

function saveToken(value) {
  token = value;
  try {
    if (value) sessionStorage.setItem(sessionKey, value);
    else sessionStorage.removeItem(sessionKey);
  } catch { /* The current page can still keep the session in memory. */ }
}

function notify(message, error = false) {
  const notice = $("#notice");
  notice.textContent = message;
  notice.classList.toggle("error", error);
  notice.hidden = !message;
  if (message) notice.scrollIntoView({ block: "nearest" });
}

function errorMessage(response, body) {
  const messages = {
    "Incorrect email or password": "Email atau password belum cocok. Silakan coba lagi.",
    "Email already registered": "Email ini sudah terdaftar. Silakan masuk dengan akun tersebut.",
    "The submission deadline has passed": "Tenggat tugas sudah lewat. Jawaban tidak dapat dikirim.",
    "Task not found": "Tugas sudah dihapus atau tidak tersedia. Muat ulang daftar tugas.",
    "Submission file is unavailable": "File jawaban tidak ditemukan di penyimpanan.",
  };
  if (messages[body?.detail]) return messages[body.detail];
  if (response.status === 401) return "Sesi telah berakhir. Silakan masuk kembali.";
  if (response.status === 403) return "Akunmu tidak memiliki akses untuk tindakan ini.";
  if (response.status === 404) return "Data yang diminta tidak tersedia atau tidak dapat diakses.";
  if (response.status === 413) return "File terlalu besar. Pilih file maksimal 10 MiB.";
  if (response.status === 415) return "File ditolak. Gunakan PDF, DOCX, atau ZIP yang valid, tidak rusak, dan tidak terenkripsi.";
  if (response.status === 422) return "Periksa kembali isian formulir. Password minimal 8 karakter (maksimal 72 byte), dan nilai harus 0–100 dengan paling banyak 2 desimal.";
  return "Permintaan belum berhasil. Coba lagi dan pastikan server serta database berjalan.";
}

async function api(path, options = {}) {
  const requestToken = token;
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (typeof options.body === "string") headers.set("Content-Type", "application/json");
  let response;
  try { response = await fetch(path, { ...options, headers }); }
  catch { throw new Error("Tidak dapat terhubung ke server. Periksa koneksi lalu coba lagi."); }
  if (requestToken !== token) throw new DOMException("Session changed", "AbortError");
  if (!response.ok) {
    let body;
    try { body = await response.json(); } catch { body = null; }
    if (response.status === 401 && path !== "/auth/login") signOut();
    throw new Error(errorMessage(response, body));
  }
  return response;
}

async function json(path, options) {
  return (await api(path, options)).json();
}

async function act(button, operation) {
  if (button?.disabled) return;
  if (button) { button.disabled = true; button.setAttribute("aria-busy", "true"); }
  try { await operation(); }
  catch (error) { if (error.name !== "AbortError") notify(error.message, true); }
  finally {
    if (button) { button.disabled = false; button.removeAttribute("aria-busy"); }
  }
}

function setAuthMode(register) {
  registering = register;
  $("#show-login").setAttribute("aria-pressed", String(!register));
  $("#show-register").setAttribute("aria-pressed", String(register));
  $("#name-field").hidden = !register;
  $("#full-name").required = register;
  $("#password").autocomplete = register ? "new-password" : "current-password";
  $("#password").minLength = register ? 8 : 1;
  $("#auth-title").textContent = register ? "Daftar mahasiswa" : "Masuk";
  $("#auth-description").textContent = register ? "Isi data untuk membuat akun mahasiswa." : "Masukkan email dan password.";
  $("#password-help").textContent = register ? "Minimal 8 karakter. Maksimal 72 byte UTF-8." : "Gunakan password akunmu.";
  $("#auth-submit").textContent = register ? "Daftar" : "Masuk";
  notify("");
}

function signOut() {
  saveToken("");
  user = null;
  tasks = [];
  taskOffset = 0;
  submissions = [];
  selected = null;
  selectionVersion++;
  $("#workspace").hidden = true;
  $("#auth-view").hidden = false;
  $("#account").hidden = true;
  $("#logout").hidden = true;
  $("#auth-form").reset();
  $("#detail").replaceChildren();
  $("#task-list").replaceChildren();
  setAuthMode(false);
}

async function enterWorkspace() {
  user = await json("/auth/me");
  $("#auth-form").reset();
  $("#auth-view").hidden = true;
  $("#workspace").hidden = false;
  $("#account").hidden = false;
  $("#logout").hidden = false;
  $("#user-name").textContent = user.full_name;
  $("#user-role").textContent = user.role === "lecturer" ? "Dosen" : "Mahasiswa";
  $("#new-task").hidden = user.role !== "lecturer";
  notify("");
  await loadTasks();
}

function renderTaskList() {
  $("#task-count").textContent = `${tasks.length} tugas dimuat`;
  $("#task-list").innerHTML = tasks.length ? tasks.map((task) => `
    <button class="task-item" data-task="${task.id}" aria-current="${selected?.id === task.id}">
      <strong>${escapeHTML(task.title)}</strong><small>${escapeHTML(deadline(task))}${isLate(task) ? " · Sudah lewat" : ""}</small>
    </button>`).join("") : `<p class="empty">Belum ada tugas. ${user.role === "lecturer" ? "Klik Buat tugas untuk menambahkan tugas pertama." : "Tugas dari dosen akan muncul di sini."}</p>`;
}

async function loadTasks(append = false, selectId = selected?.id) {
  const batch = await json(`/tasks?offset=${append ? taskOffset : 0}&limit=${PAGE_SIZE}`);
  taskOffset = (append ? taskOffset : 0) + batch.length;
  tasks = append ? [...tasks, ...batch.filter((item) => !tasks.some((task) => task.id === item.id))] : batch;
  $("#more-tasks").hidden = batch.length < PAGE_SIZE;
  renderTaskList();
  if (!append) {
    const next = tasks.find((task) => task.id === selectId) || tasks[0];
    if (next) await selectTask(next);
    else {
      selected = null;
      selectionVersion++;
      $("#detail").innerHTML = '<h2>Pilih tugas untuk mulai</h2><p class="empty">Instruksi, jawaban, dan nilai akan tampil di sini.</p>';
    }
  }
}

async function selectTask(task) {
  selected = task;
  const version = ++selectionVersion;
  submissions = [];
  renderTaskList();
  const canView = user.role === "student" || isOwner(task);
  $("#detail").innerHTML = `
    <h2 class="detail-title">${escapeHTML(task.title)}</h2>
    <p class="muted detail-meta">${escapeHTML(deadline(task))}${isLate(task) ? ' <span class="danger-text">· Tenggat sudah lewat</span>' : ""}</p>
    <p class="description">${escapeHTML(task.description)}</p>
    ${isOwner(task) ? '<div class="actions section-actions"><button id="edit-task" class="button secondary">Ubah tugas</button><button id="delete-task" class="button danger">Hapus tugas</button></div>' : ""}
    ${user.role === "student" ? `<section class="section-block"><h2>Kirim jawaban</h2><p class="muted">Unggah PDF, DOCX, atau ZIP. Maksimal 10 MiB.</p>
      ${isLate(task) ? '<p class="empty">Tenggat sudah lewat. Riwayat jawaban dan nilai tetap bisa dilihat di bawah.</p>' : `<form id="submission-form">
        <div class="field"><label for="answer-file">File jawaban</label><input id="answer-file" name="file" type="file" accept=".pdf,.docx,.zip" required aria-describedby="file-help"><small id="file-help" hidden></small></div>
        <div class="field"><label for="answer-text">Catatan (opsional)</label><textarea id="answer-text" name="answer_text" maxlength="20000" placeholder="Tambahkan penjelasan untuk dosen..."></textarea></div>
        <button class="button primary" type="submit">Kirim jawaban</button></form>`}</section>` : ""}
    <section class="section-block"><h2>${user.role === "student" ? "Riwayat pengiriman" : "Jawaban mahasiswa"}</h2>
      <div id="submissions">${canView ? '<p class="empty">Memuat jawaban…</p>' : '<p class="empty">Tugas ini milik dosen lain. Jawaban mahasiswa hanya dapat dibuka oleh dosen pemilik tugas.</p>'}</div>
      <button id="more-submissions" class="button secondary" hidden>Muat jawaban lainnya</button></section>`;
  if (canView) await loadSubmissions(false, task.id, version);
}

async function loadSubmissions(append = false, taskId = selected?.id, version = selectionVersion) {
  try {
    const batch = await json(`/tasks/${taskId}/submissions?offset=${append ? submissions.length : 0}&limit=${PAGE_SIZE}`);
    if (version !== selectionVersion) return;
    submissions = append ? [...submissions, ...batch] : batch;
    renderSubmissions();
    $("#more-submissions").hidden = batch.length < PAGE_SIZE;
  } catch (error) {
    if (version === selectionVersion && $("#submissions")) $("#submissions").innerHTML = '<p class="empty">Jawaban belum dapat dimuat. Klik Muat ulang untuk mencoba lagi.</p>';
    throw error;
  }
}

function renderSubmissions() {
  $("#submissions").innerHTML = submissions.length ? submissions.map((item, index) => `
    <article class="submission">
      <div class="submission-top"><div><h3>${user.role === "lecturer" ? escapeHTML(item.student.full_name) : `Pengiriman ${index + 1}`}</h3>
        ${user.role === "lecturer" ? `<small>${escapeHTML(item.student.email)}</small>` : ""}<small>Dikirim ${escapeHTML(date(item.submitted_at))}</small></div>
        <small>${item.grade ? "Sudah dinilai" : "Belum dinilai"}</small></div>
      <button class="file-button" data-download="${item.id}">Unduh ${escapeHTML(item.original_filename)}</button>
      ${item.answer_text ? `<p class="answer">${escapeHTML(item.answer_text)}</p>` : ""}
      ${item.grade ? `<div class="grade-result"><strong>Nilai ${escapeHTML(Number(item.grade.score))} / 100</strong><p>${escapeHTML(item.grade.feedback || "Belum ada feedback tambahan.")}</p></div>` : '<p class="muted answer">Menunggu penilaian dosen.</p>'}
      ${user.role === "lecturer" ? `<details class="grade-details"><summary>${item.grade ? "Ubah nilai dan feedback" : "Beri nilai dan feedback"}</summary><form class="grade-form" data-submission="${item.id}">
        <div class="field"><label for="score-${item.id}">Nilai (0–100)</label><input id="score-${item.id}" name="score" type="number" min="0" max="100" step="0.01" required value="${escapeHTML(item.grade?.score ?? "")}"></div>
        <div class="field"><label for="feedback-${item.id}">Feedback (opsional)</label><textarea id="feedback-${item.id}" name="feedback" maxlength="10000">${escapeHTML(item.grade?.feedback || "")}</textarea></div>
        <button class="button primary" type="submit">Simpan penilaian</button></form></details>` : ""}
    </article>`).join("") : '<p class="empty">Belum ada jawaban yang dikirim.</p>';
}

function taskForm(task = null) {
  selectionVersion++;
  selected = task;
  renderTaskList();
  let localDue = "";
  if (task?.due_at) {
    const due = new Date(task.due_at);
    localDue = new Date(due.getTime() - due.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  $("#detail").innerHTML = `<h2>${task ? "Ubah tugas" : "Buat tugas baru"}</h2>
    <form id="task-form" data-task="${task?.id || ""}">
      <div class="field"><label for="task-title">Judul tugas</label><input id="task-title" name="title" maxlength="200" required value="${escapeHTML(task?.title || "")}" placeholder="Contoh: Pengantar REST API"></div>
      <div class="field"><label for="task-description">Instruksi tugas</label><textarea id="task-description" name="description" maxlength="20000" rows="5" required placeholder="Jelaskan apa yang perlu dikerjakan dan dikumpulkan.">${escapeHTML(task?.description || "")}</textarea></div>
      <div class="field"><label for="task-due">Tenggat (opsional)</label><input id="task-due" name="due_at" type="datetime-local" value="${localDue}" aria-describedby="due-help"><small id="due-help">Mengikuti waktu perangkatmu. Kosongkan jika tidak ada tenggat.</small></div>
      <div class="actions"><button type="submit" class="button primary">${task ? "Simpan perubahan" : "Simpan tugas"}</button><button id="cancel-task" class="button secondary" type="button">Batal</button></div></form>`;
  $("#task-title").focus();
}

$("#show-login").addEventListener("click", () => setAuthMode(false));
$("#show-register").addEventListener("click", () => setAuthMode(true));
$("#logout").addEventListener("click", () => { signOut(); notify("Kamu sudah keluar dari akun."); $("#email").focus(); });
$("#auth-form").addEventListener("submit", (event) => {
  event.preventDefault();
  act($("#auth-submit"), async () => {
    const email = $("#email").value.trim();
    const password = $("#password").value;
    if (registering) {
      if (new TextEncoder().encode(password).length > 72 || password.includes("\0")) throw new Error("Password maksimal 72 byte UTF-8 dan tidak boleh mengandung karakter null.");
      await json("/auth/register", { method: "POST", body: JSON.stringify({ email, password, full_name: $("#full-name").value.trim() }) });
      setAuthMode(false);
      $("#password").value = "";
      notify("Akun mahasiswa berhasil dibuat. Masuk dengan email dan password tadi.");
      $("#password").focus();
    } else {
      const data = await json("/auth/login", { method: "POST", body: new URLSearchParams({ username: email, password }) });
      saveToken(data.access_token);
      await enterWorkspace();
    }
  });
});
$("#reload").addEventListener("click", (event) => act(event.currentTarget, async () => { await loadTasks(); notify("Daftar tugas dan jawaban sudah diperbarui."); }));
$("#more-tasks").addEventListener("click", (event) => act(event.currentTarget, () => loadTasks(true)));
$("#new-task").addEventListener("click", () => { notify(""); taskForm(); });
$("#task-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-task]");
  if (!button) return;
  act(button, async () => {
    notify("");
    await selectTask(tasks.find((task) => task.id === Number(button.dataset.task)));
    if (matchMedia("(max-width:720px)").matches) $("#detail").focus();
  });
});

$("#detail").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || (button.type === "submit" && button.form)) return;
  if (button.id === "edit-task") { taskForm(selected); return; }
  act(button, async () => {
    if (button.id === "cancel-task") { await loadTasks(); return; }
    if (button.id === "delete-task") {
      if (!confirm(`Hapus tugas “${selected.title}”? Tugas tidak akan terlihat lagi; jawaban dan nilainya tetap disimpan.`)) return;
      await api(`/tasks/${selected.id}`, { method: "DELETE" });
      selected = null;
      await loadTasks();
      notify("Tugas berhasil dihapus dari daftar.");
    }
    if (button.id === "more-submissions") await loadSubmissions(true);
    if (button.dataset.download) {
      const item = submissions.find((value) => value.id === Number(button.dataset.download));
      const response = await api(`/submissions/${item.id}/file`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = item.original_filename;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    }
  });
});

$("#detail").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  act(form.querySelector('[type="submit"]'), async () => {
    const data = new FormData(form);
    if (form.id === "task-form") {
      const due = data.get("due_at");
      const id = form.dataset.task;
      const task = await json(id ? `/tasks/${id}` : "/tasks", {
        method: id ? "PATCH" : "POST",
        body: JSON.stringify({ title: data.get("title").trim(), description: data.get("description").trim(), due_at: due ? new Date(due).toISOString() : null }),
      });
      // Keep a new task visible even when older tasks occupy the first page.
      await loadTasks();
      if (!tasks.some((item) => item.id === task.id)) { tasks.push(task); renderTaskList(); }
      await selectTask(task);
      notify(id ? "Perubahan tugas berhasil disimpan." : "Tugas baru berhasil dibuat.");
    }
    if (form.id === "submission-form") {
      const file = data.get("file");
      if (!file.size) throw new Error("Pilih file jawaban yang tidak kosong.");
      if (file.size > MAX_FILE_SIZE) throw new Error("File terlalu besar. Maksimal 10 MiB (10.485.760 byte).");
      if (!/\.(pdf|docx|zip)$/i.test(file.name)) throw new Error("Format belum didukung. Pilih PDF, DOCX, atau ZIP.");
      const task = selected;
      const version = selectionVersion;
      await json(`/tasks/${task.id}/submissions`, { method: "POST", body: data });
      if (version === selectionVersion) { form.reset(); await loadSubmissions(); }
      notify("Jawaban berhasil dikirim. Nilai akan muncul setelah dosen memeriksanya.");
    }
    if (form.classList.contains("grade-form")) {
      const version = selectionVersion;
      await json(`/submissions/${form.dataset.submission}/grade`, { method: "PUT", body: JSON.stringify({ score: data.get("score"), feedback: data.get("feedback") || null }) });
      if (version === selectionVersion) await loadSubmissions();
      notify("Nilai dan feedback berhasil disimpan.");
    }
  });
});

if (token) act(null, enterWorkspace);
