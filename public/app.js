const euro = new Intl.NumberFormat("nl-NL", { style: "currency", currency: "EUR" });

const els = {
  authShell: document.getElementById("authShell"),
  appShell: document.getElementById("appShell"),
  authTitle: document.getElementById("authTitle"),
  authText: document.getElementById("authText"),
  authForm: document.getElementById("authForm"),
  authUsername: document.getElementById("authUsername"),
  authPassword: document.getElementById("authPassword"),
  authSubmit: document.getElementById("authSubmit"),
  authError: document.getElementById("authError"),
  currentUser: document.getElementById("currentUser"),
  usersButton: document.getElementById("usersButton"),
  logoutButton: document.getElementById("logoutButton"),
  userModal: document.getElementById("userModal"),
  closeUsersButton: document.getElementById("closeUsersButton"),
  addUserForm: document.getElementById("addUserForm"),
  newUsername: document.getElementById("newUsername"),
  newPassword: document.getElementById("newPassword"),
  newAdmin: document.getElementById("newAdmin"),
  userError: document.getElementById("userError"),
  userList: document.getElementById("userList"),
  loadingScreen: document.getElementById("loadingScreen"),
  loadingText: document.getElementById("loadingText"),
  loadingBar: document.getElementById("loadingBar"),
  reportInput: document.getElementById("reportInput"),
  planningInput: document.getElementById("planningInput"),
  reportDropZone: document.getElementById("reportDropZone"),
  planningDropZone: document.getElementById("planningDropZone"),
  clearSessionBtn: document.getElementById("clearSessionBtn"),
  uploadStatus: document.getElementById("uploadStatus"),
  reportName: document.getElementById("reportName"),
  planningFiles: document.getElementById("planningFiles"),
  weekSelect: document.getElementById("weekSelect"),
  zipInvoiceBtn: document.getElementById("zipInvoiceBtn"),
  searchInput: document.getElementById("searchInput"),
  summary: document.getElementById("summary"),
  weekCheck: document.getElementById("weekCheck"),
  sourceStatus: document.getElementById("sourceStatus"),
  resultsSection: document.getElementById("resultsSection"),
  companyRows: document.getElementById("companyRows"),
};

let reports = [];
let weeks = [];
let companies = [];
let weekMeta = {};
let pendingReportFiles = [];
let authState = { setupRequired: false, authenticated: false, username: "", admin: false };
let loadingHideTimer = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

async function readJson(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    if (response.status === 413) {
      throw new Error(
        "Upload is te groot voor de website. Upload eerst het rittenboek; van een groot CSV sturen we alleen die week."
      );
    }
    if (response.status === 503 || response.status === 500) {
      throw new Error(
        `Serverfout (${response.status}). Controleer Redis/Upstash op Vercel en of de laatste versie is gedeployed.`
      );
    }
    throw new Error(
      response.ok
        ? "De server gaf geen JSON terug. Controleer of de API op Vercel draait."
        : `API-fout (${response.status}). Vernieuw de pagina of controleer de Vercel-logs.`
    );
  }
  return response.json();
}

function csvDateToIso(value) {
  const raw = String(value || "").replace(/^['"]+|['"]+$/g, "").trim();
  const dmy = raw.match(/^(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})/);
  if (dmy) {
    const year = dmy[3].length === 2 ? `20${dmy[3]}` : dmy[3];
    return `${year}-${dmy[2].padStart(2, "0")}-${dmy[1].padStart(2, "0")}`;
  }
  const iso = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return iso ? `${iso[1]}-${iso[2]}-${iso[3]}` : "";
}

function dateInRanges(iso, ranges) {
  return Boolean(iso && ranges.some((range) => iso >= range.from && iso <= range.to));
}

async function weekRangesFromServer() {
  const data = await getJson("/api/weeks").catch(() => ({ weeks: [] }));
  return (data.weeks || [])
    .filter((week) => week.from && week.to)
    .map((week) => ({ from: week.from, to: week.to }));
}

function csvHeaderMeta(headerLine) {
  const semi = headerLine.split(";");
  const comma = headerLine.split(",");
  const delim = semi.length >= comma.length ? ";" : ",";
  const cols = delim === ";" ? semi : comma;
  const idx = cols.findIndex((col) => /trx\s*date|^datum$/i.test(String(col).replace(/"/g, "").trim()));
  return { delim, idx };
}

async function slimCsvToPlanning(file, ranges, onProgress) {
  const decoder = new TextDecoder("utf-8");
  const reader = file.stream().getReader();
  let leftover = "";
  let header = "";
  let meta = null;
  let total = 0;
  let kept = 0;
  const lines = [];
  const consume = (chunk, last = false) => {
    leftover += chunk;
    const parts = leftover.split(/\r?\n/);
    leftover = last ? "" : parts.pop() || "";
    for (const line of parts) {
      if (!meta) {
        if (!line.trim()) continue;
        header = line;
        meta = csvHeaderMeta(line);
        lines.push(line);
        continue;
      }
      if (!line.trim()) continue;
      total += 1;
      if (meta.idx < 0) {
        kept += 1;
        lines.push(line);
        continue;
      }
      const cols = line.split(meta.delim);
      const iso = csvDateToIso(cols[meta.idx] || "");
      if (dateInRanges(iso, ranges)) {
        kept += 1;
        lines.push(line);
      }
    }
  };
  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      consume("", true);
      break;
    }
    consume(decoder.decode(value, { stream: true }));
    if (onProgress) onProgress(total, kept);
  }
  if (!header) throw new Error("Dit CSV-bestand heeft geen kopregel.");
  if (!kept) {
    throw new Error("In dit rapport staan geen tolregels in de datums van het rittenboek.");
  }
  const blob = new Blob([`${lines.join("\n")}\n`], { type: "text/csv" });
  return { file: new File([blob], file.name, { type: "text/csv" }), total, kept };
}

async function gzipFileIfHelpful(file) {
  if (typeof CompressionStream === "undefined" || file.size < 200 * 1024) {
    return { blob: file, compressed: false };
  }
  try {
    const stream = file.stream().pipeThrough(new CompressionStream("gzip"));
    const blob = await new Response(stream).blob();
    if (blob.size >= file.size * 0.9) {
      return { blob: file, compressed: false };
    }
    return { blob, compressed: true };
  } catch (error) {
    return { blob: file, compressed: false };
  }
}

async function getJson(url) {
  const response = await fetch(url);
  const data = await readJson(response);
  if (!response.ok) throw new Error(data.error || "Gegevens laden is mislukt.");
  return data;
}

function setStatus(message, type = "") {
  els.uploadStatus.hidden = !message;
  els.uploadStatus.className = `message ${type}`.trim();
  els.uploadStatus.textContent = message;
}

function setBusy(button, busy, text = "Maken…") {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = text;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
  }
}

function showLoading(title, text, percent = 5) {
  if (loadingHideTimer) clearTimeout(loadingHideTimer);
  els.loadingText.textContent = text || title;
  els.loadingScreen.hidden = false;
  updateLoading(percent);
}

function updateLoading(percent, text = "") {
  if (percent != null && Number.isFinite(Number(percent))) {
    const value = Math.max(0, Math.min(100, Math.round(percent)));
    els.loadingBar.style.width = `${value}%`;
  }
  if (text) els.loadingText.textContent = text;
}

function hideLoading(completed = true) {
  if (!completed) {
    els.loadingScreen.hidden = true;
    return;
  }
  updateLoading(100, "Klaar");
  loadingHideTimer = setTimeout(() => {
    els.loadingScreen.hidden = true;
  }, 300);
}

function showAuthentication() {
  els.appShell.hidden = true;
  els.userModal.hidden = true;
  els.authShell.hidden = false;
  els.authError.hidden = true;
  els.authTitle.textContent = authState.setupRequired ? "Beheerder instellen" : "Inloggen";
  els.authText.textContent = authState.setupRequired
    ? "Maak het eerste beheeraccount aan."
    : "Log in om de tolverdeling te gebruiken.";
  els.authSubmit.textContent = authState.setupRequired ? "Beheerder aanmaken" : "Inloggen";
  els.authPassword.autocomplete = authState.setupRequired ? "new-password" : "current-password";
}

async function showApplication() {
  els.authShell.hidden = true;
  els.appShell.hidden = false;
  els.currentUser.textContent = authState.username;
  els.usersButton.hidden = !authState.admin;
  await loadSources();
}

async function bootstrapAuthentication() {
  authState = await getJson("/api/auth/status");
  if (authState.authenticated) await showApplication();
  else showAuthentication();
}

async function submitAuthentication(event) {
  event.preventDefault();
  setBusy(els.authSubmit, true, authState.setupRequired ? "Aanmaken…" : "Inloggen…");
  els.authError.hidden = true;
  try {
    const endpoint = authState.setupRequired ? "/api/auth/setup" : "/api/auth/login";
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: els.authUsername.value.trim(),
        password: els.authPassword.value,
      }),
    });
    const data = await readJson(response);
    if (!response.ok) throw new Error(data.error || "Inloggen is mislukt.");
    authState = { setupRequired: false, authenticated: true, username: data.username, admin: Boolean(data.admin) };
    els.authPassword.value = "";
    await showApplication();
  } catch (error) {
    els.authError.textContent = error instanceof TypeError
      ? "Geen verbinding met de server. Sluit het zwarte venster en start opnieuw via start.bat."
      : (error.message || "Inloggen is mislukt.");
    els.authError.hidden = false;
  } finally {
    setBusy(els.authSubmit, false);
  }
}

async function logout() {
  await fetch("/api/auth/logout", { method: "POST" });
  authState = { setupRequired: false, authenticated: false, username: "", admin: false };
  reports = [];
  weeks = [];
  companies = [];
    weekMeta = {};
  pendingReportFiles = [];
  showAuthentication();
}

function renderUsers(users) {
  els.userList.innerHTML = users.map((user) => {
    const ownAccount = user.username === authState.username;
    return `<div class="user-row">
      <strong>${escapeHtml(user.username)}</strong>
      <span class="user-role">${user.admin ? "Beheerder" : "Gebruiker"}</span>
      ${ownAccount ? "" : `<button type="button" class="delete-user" data-delete-user="${escapeAttr(user.username)}">Verwijderen</button>`}
    </div>`;
  }).join("");
}

async function loadUsers() {
  const data = await getJson("/api/users");
  renderUsers(data.users || []);
}

async function openUsers() {
  els.userError.hidden = true;
  els.userModal.hidden = false;
  await loadUsers();
}

async function addUser(event) {
  event.preventDefault();
  els.userError.hidden = true;
  try {
    const response = await fetch("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: els.newUsername.value.trim(),
        password: els.newPassword.value,
        admin: els.newAdmin.checked,
      }),
    });
    const data = await readJson(response);
    if (!response.ok) throw new Error(data.error || "Gebruiker aanmaken is mislukt.");
    els.addUserForm.reset();
    await loadUsers();
  } catch (error) {
    els.userError.textContent = error.message || "Gebruiker aanmaken is mislukt.";
    els.userError.hidden = false;
  }
}

async function deleteUser(username) {
  if (!confirm(`Gebruiker ${username} verwijderen?`)) return;
  try {
    const response = await fetch("/api/users?username=" + encodeURIComponent(username), { method: "DELETE" });
    const data = await readJson(response);
    if (!response.ok) throw new Error(data.error || "Verwijderen is mislukt.");
    await loadUsers();
  } catch (error) {
    els.userError.textContent = error.message || "Verwijderen is mislukt.";
    els.userError.hidden = false;
  }
}

function selectedWeek() {
  return els.weekSelect.value || "";
}

async function loadSources() {
  const [fileData, planningData, weekData] = await Promise.all([
    getJson("/api/files"),
    getJson("/api/planning"),
    getJson("/api/weeks").catch(() => ({ weeks: [] })),
  ]);
  reports = fileData.files || [];
  els.reportName.textContent = reports.length
    ? `${reports.length} rapport${reports.length === 1 ? "" : "en"}: ${reports.map((item) => item.name).join(", ")}`
    : "Geen tolrapporten";

  const weekFiles = planningData.files || [];
  els.planningFiles.textContent = weekFiles.length
    ? `Rittenboek: ${weekFiles.join(", ")}`
    : "Nog geen rittenboek";
  els.sourceStatus.hidden = !reports.length && !weekFiles.length;

  weeks = weekData.weeks || [];
  const previous = selectedWeek();
  els.weekSelect.disabled = weeks.length === 0;
  els.weekSelect.innerHTML = weeks.map((week) => {
    const period = week.from && week.to ? ` · ${week.from} t/m ${week.to}` : "";
    return `<option value="${escapeAttr(week.id)}">${escapeHtml(`${week.id}${period}`)}</option>`;
  }).join("");
  if (previous && weeks.some((week) => week.id === previous)) {
    els.weekSelect.value = previous;
  }
  await loadCompanies();
}

async function loadCompanies() {
  const week = selectedWeek();
  if (!week) {
    companies = [];
    weekMeta = {};
    renderCompanies();
    return;
  }

  els.companyRows.innerHTML = `<tr><td colspan="4" class="loading">Bedrijven laden…</td></tr>`;
  const data = await getJson("/api/pdf-customers?week=" + encodeURIComponent(week));
  companies = data.customers || [];
  weekMeta = data;
  renderCompanies();
}

function renderCompanies() {
  const query = els.searchInput.value.trim().toLowerCase();
  const visible = companies.filter((item) => {
    const haystack = `${item.customer || ""} ${item.country || ""}`.toLowerCase();
    return haystack.includes(query);
  });
  const total = companies.reduce((sum, item) => sum + item.eur, 0);

  els.summary.innerHTML = selectedWeek()
    ? `<span><b>${companies.length}</b> klanten</span>
       <span><b>${euro.format(total)}</b> tol</span>`
    : "";

  const notes = (weekMeta.qualityNotes || []).filter((note) => note.text);
  if (notes.length) {
    els.weekCheck.hidden = false;
    els.weekCheck.innerHTML = `<ul>${notes.map((note) => `<li>${escapeHtml(note.text)}</li>`).join("")}</ul>`;
  } else {
    els.weekCheck.hidden = true;
    els.weekCheck.innerHTML = "";
  }

  els.resultsSection.hidden = weeks.length === 0 && reports.length === 0;
  els.zipInvoiceBtn.disabled = !selectedWeek();

  if (!visible.length) {
    els.companyRows.innerHTML = companies.length
      ? `<tr><td colspan="4" class="empty-row">Geen bedrijf gevonden.</td></tr>`
      : "";
    return;
  }

  els.companyRows.innerHTML = visible.map((item) => `<tr>
      <td><strong>${escapeHtml(item.customer)}</strong></td>
      <td>${escapeHtml(item.country || "—")}</td>
      <td class="number">${euro.format(item.eur)}</td>
      <td class="download-cell">
        <button type="button" class="button button-small"
          data-customer="${escapeAttr(item.customer)}">PDF downloaden</button>
      </td>
    </tr>`).join("");
}

async function postUpload(file, expectedKind, replace) {
  const prepared = await gzipFileIfHelpful(file);
  const body = new FormData();
  body.append("file", prepared.blob, file.name);
  if (prepared.compressed) body.append("compressed", "gzip");
  const response = await fetch(
    "/api/upload?expected=" + encodeURIComponent(expectedKind) + (replace ? "&replace=1" : ""),
    { method: "POST", body },
  );
  const data = await readJson(response);
  if (!response.ok) throw new Error(data.error || `${file.name} kon niet worden geüpload.`);
}

async function prepareReportFile(file, ranges, index, total) {
  if (!/\.csv$/i.test(file.name) || !ranges.length) return file;
  updateLoading(8 + (index / total) * 20, `${file.name} filteren op week…`);
  const slim = await slimCsvToPlanning(file, ranges, (seen) => {
    if (seen && seen % 20000 === 0) {
      updateLoading(undefined, `${file.name}: ${seen.toLocaleString("nl-NL")} regels gelezen…`);
    }
  });
  setStatus(
    `${slim.kept.toLocaleString("nl-NL")} van ${slim.total.toLocaleString("nl-NL")} tolregels vallen in het rittenboek.`,
    "success",
  );
  return slim.file;
}

async function uploadFiles(fileList, expectedKind) {
  let files = [...fileList];
  if (expectedKind === "report") files = files.filter((file) => /\.(csv|xls|xlsx)$/i.test(file.name));
  if (expectedKind === "planning") files = files.filter((file) => /\.(xls|xlsx)$/i.test(file.name));
  if (!files.length) {
    setStatus(
      expectedKind === "report"
        ? "Kies een tolrapport (CSV of Excel)."
        : "Kies een weekbestand (Excel).",
      "error",
    );
    return;
  }

  if (expectedKind === "report") {
    const ranges = await weekRangesFromServer();
    if (!ranges.length && files.some((file) => /\.csv$/i.test(file.name) && file.size > 1.5 * 1024 * 1024)) {
      pendingReportFiles = files;
      setStatus("Groot rapport staat klaar. Upload eerst het rittenboek; daarna sturen we alleen die week naar de server.", "success");
      return;
    }
  }

  showLoading("Bestanden verwerken", "Upload…", 5);
  els.reportInput.disabled = true;
  els.planningInput.disabled = true;
  let completed = false;
  try {
    let ranges = [];
    if (expectedKind === "report") ranges = await weekRangesFromServer();
    for (let index = 0; index < files.length; index += 1) {
      let file = files[index];
      setStatus(`Uploaden ${index + 1} van ${files.length}: ${file.name}`);
      updateLoading(10 + (index / files.length) * 55, file.name);
      if (expectedKind === "report") file = await prepareReportFile(file, ranges, index, files.length);
      await postUpload(file, expectedKind, index === 0);
      updateLoading(10 + ((index + 1) / files.length) * 55, file.name);
    }
    setStatus(
      expectedKind === "report"
        ? `${files.length} rapport${files.length === 1 ? "" : "en"} verwerkt.`
        : `${files.length} weekbestand${files.length === 1 ? "" : "en"} verwerkt.`,
      "success",
    );
    updateLoading(80, "Koppelen…");
    await loadSources();
    if (expectedKind === "planning" && pendingReportFiles.length) {
      const leftover = pendingReportFiles;
      pendingReportFiles = [];
      updateLoading(40, "Groot tolrapport filteren…");
      ranges = await weekRangesFromServer();
      for (let index = 0; index < leftover.length; index += 1) {
        let file = leftover[index];
        updateLoading(45 + (index / leftover.length) * 35, file.name);
        file = await prepareReportFile(file, ranges, index, leftover.length);
        await postUpload(file, "report", index === 0);
      }
      await loadSources();
    }
    completed = true;
  } catch (error) {
    setStatus(error.message || "Uploaden is mislukt.", "error");
  } finally {
    els.reportInput.disabled = false;
    els.planningInput.disabled = false;
    els.reportInput.value = "";
    els.planningInput.value = "";
    hideLoading(completed);
  }
}

async function clearSession() {
  if (!confirm("Alle tijdelijk geüploade bestanden uit deze sessie wissen?")) return;
  setBusy(els.clearSessionBtn, true, "Wissen…");
  try {
    const response = await fetch("/api/clear", { method: "POST" });
    if (!response.ok) throw new Error("Wissen is mislukt.");
    companies = [];
    weeks = [];
    reports = [];
    weekMeta = {};
    pendingReportFiles = [];
    setStatus("Bestanden gewist.", "success");
    await loadSources();
  } catch (error) {
    setStatus(error.message || "Wissen is mislukt.", "error");
  } finally {
    setBusy(els.clearSessionBtn, false);
  }
}

async function downloadFile(url, button, fallbackName, withLoading = false) {
  setBusy(button, true);
  if (withLoading) showLoading("ZIP maken", "PDF’s maken…", 10);
  let completed = false;
  try {
    const response = await fetch(url);
    if (!response.ok) {
      let message = "Download maken is mislukt.";
      try {
        const data = await readJson(response);
        message = data.error || message;
      } catch (error) {
        // Geen JSON-fout ontvangen.
      }
      throw new Error(message);
    }

    if (withLoading) updateLoading(85, "Downloaden…");
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/i);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = match?.[1] || fallbackName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    completed = true;
  } catch (error) {
    setStatus(error.message || "Download maken is mislukt.", "error");
  } finally {
    setBusy(button, false);
    if (withLoading) hideLoading(completed);
  }
}

function downloadCompany(customer, button) {
  const week = selectedWeek();
  const url = "/api/customer-pdf?week=" + encodeURIComponent(week)
    + "&customer=" + encodeURIComponent(customer);
  const name = `Toloverzicht - ${week} - ${customer}.pdf`;
  return downloadFile(url, button, name);
}

function downloadZip(button) {
  const week = selectedWeek();
  const url = "/api/customer-pdfs.zip?week=" + encodeURIComponent(week);
  const name = `Toloverzichten_${week}.zip`;
  return downloadFile(url, button, name, true);
}

els.authForm.addEventListener("submit", submitAuthentication);
els.logoutButton.addEventListener("click", logout);
els.usersButton.addEventListener("click", openUsers);
els.closeUsersButton.addEventListener("click", () => { els.userModal.hidden = true; });
els.userModal.addEventListener("click", (event) => {
  if (event.target === els.userModal) els.userModal.hidden = true;
});
els.addUserForm.addEventListener("submit", addUser);
els.userList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-delete-user]");
  if (button) deleteUser(button.dataset.deleteUser);
});

els.reportInput.addEventListener("change", () => uploadFiles(els.reportInput.files, "report"));
els.planningInput.addEventListener("change", () => uploadFiles(els.planningInput.files, "planning"));
els.clearSessionBtn.addEventListener("click", clearSession);

function enableDropZone(zone, kind) {
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("dragging");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("dragging");
    uploadFiles(event.dataTransfer.files, kind);
  });
}

enableDropZone(els.reportDropZone, "report");
enableDropZone(els.planningDropZone, "planning");
els.weekSelect.addEventListener("change", loadCompanies);
els.searchInput.addEventListener("input", renderCompanies);
els.zipInvoiceBtn.addEventListener("click", () => downloadZip(els.zipInvoiceBtn));
els.companyRows.addEventListener("click", (event) => {
  const button = event.target.closest("[data-customer]");
  if (button) downloadCompany(button.dataset.customer, button);
});

bootstrapAuthentication().catch((error) => {
  showAuthentication();
  els.authError.textContent = error.message || "De website kon niet worden geladen.";
  els.authError.hidden = false;
});
