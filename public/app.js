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
  loadingTitle: document.getElementById("loadingTitle"),
  loadingText: document.getElementById("loadingText"),
  loadingBar: document.getElementById("loadingBar"),
  loadingPercent: document.getElementById("loadingPercent"),
  reportInput: document.getElementById("reportInput"),
  planningInput: document.getElementById("planningInput"),
  invoiceInput: document.getElementById("invoiceInput"),
  reportDropZone: document.getElementById("reportDropZone"),
  planningDropZone: document.getElementById("planningDropZone"),
  invoiceDropZone: document.getElementById("invoiceDropZone"),
  clearSessionBtn: document.getElementById("clearSessionBtn"),
  uploadStatus: document.getElementById("uploadStatus"),
  reportName: document.getElementById("reportName"),
  planningFiles: document.getElementById("planningFiles"),
  invoiceFile: document.getElementById("invoiceFile"),
  weekSelect: document.getElementById("weekSelect"),
  zipInvoiceBtn: document.getElementById("zipInvoiceBtn"),
  searchInput: document.getElementById("searchInput"),
  summary: document.getElementById("summary"),
  weekCheck: document.getElementById("weekCheck"),
  invoiceCheck: document.getElementById("invoiceCheck"),
  sourceStatus: document.getElementById("sourceStatus"),
  resultsSection: document.getElementById("resultsSection"),
  companyRows: document.getElementById("companyRows"),
};

let reports = [];
let weeks = [];
let companies = [];
let weekMeta = {};
let coverage = { reports: [], totals: null };
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

function formatIso(value) {
  if (!value) return "—";
  const [year, month, day] = String(value).split("-");
  if (!day) return value;
  return `${day}-${month}-${year}`;
}

function monthTitle(yearMonth) {
  const [year, month] = yearMonth.split("-");
  const names = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december"];
  return `${names[Number(month) - 1] || month} ${year}`;
}

function renderCalendar(days) {
  if (!days.length) return "";
  const byMonth = new Map();
  days.forEach((day) => {
    const key = day.date.slice(0, 7);
    if (!byMonth.has(key)) byMonth.set(key, []);
    byMonth.get(key).push(day);
  });
  const weekday = ["ma", "di", "wo", "do", "vr", "za", "zo"];
  const months = [...byMonth.entries()].map(([key, monthDays]) => {
    const first = new Date(`${monthDays[0].date}T12:00:00`);
    const mondayIndex = (first.getDay() + 6) % 7;
    const pads = Array.from({ length: mondayIndex }, () => "<span class=\"cal-day empty\"></span>");
    const cells = monthDays.map((day) => {
      const number = Number(day.date.slice(8, 10));
      const state = day.inPlanning ? "planned" : day.inReport ? "missing" : "idle";
      const title = day.inPlanning
        ? `${formatIso(day.date)} · ${day.week || "rittenboek"}`
        : day.inReport
          ? `${formatIso(day.date)} · ${day.rows} tolregels, nog geen weekplanning`
          : formatIso(day.date);
      return `<span class="cal-day ${state}" title="${escapeAttr(title)}">${number}</span>`;
    });
    return `<div class="cal-month">
      <h3>${monthTitle(key)}</h3>
      <div class="cal-weekdays">${weekday.map((name) => `<span>${name}</span>`).join("")}</div>
      <div class="cal-grid">${pads.join("")}${cells.join("")}</div>
    </div>`;
  }).join("");
  return `<div class="cal-wrap">${months}</div>
    <div class="cal-legend">
      <span><i class="planned"></i> Weekplanning geüpload</span>
      <span><i class="missing"></i> Tolrapport, nog geen weekplanning</span>
      <span><i class="idle"></i> Geen tol die dag</span>
    </div>`;
}

async function readJson(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    if (response.status === 413) {
      throw new Error(
        "Bestand is te groot voor de serverlimiet. De app comprimeert uploads automatisch — vernieuw de pagina en probeer opnieuw, of gebruik start.bat lokaal."
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
  els.loadingTitle.textContent = title;
  els.loadingText.textContent = text;
  els.loadingScreen.hidden = false;
  updateLoading(percent);
}

function updateLoading(percent, text = "") {
  const value = Math.max(0, Math.min(100, Math.round(percent)));
  els.loadingBar.style.width = `${value}%`;
  els.loadingPercent.textContent = `${value}%`;
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
    ? "Maak het eerste beheeraccount aan. Alleen de gebruikersnaam en een veilige wachtwoordhash worden opgeslagen."
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
  coverage = { reports: [], totals: null };
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
  const [fileData, planningData, invoiceData, weekData, coverageData] = await Promise.all([
    getJson("/api/files"),
    getJson("/api/planning"),
    getJson("/api/invoice-files"),
    getJson("/api/weeks").catch(() => ({ weeks: [] })),
    getJson("/api/week-check").catch(() => ({ reports: [], totals: null })),
  ]);
  coverage = coverageData || { reports: [], totals: null };
  reports = fileData.files || [];
  els.reportName.textContent = reports.length
    ? `${reports.length} rapport${reports.length === 1 ? "" : "en"}: ${reports.map((item) => item.name).join(", ")}`
    : "Geen tolrapporten";

  const weekFiles = planningData.files || [];
  els.planningFiles.textContent = weekFiles.length
    ? `Rittenboek: ${weekFiles.join(", ")}`
    : "Nog geen rittenboek";
  const invoiceFiles = invoiceData.files || [];
  els.invoiceFile.textContent = invoiceFiles.length
    ? `Factuur-PDF: ${invoiceFiles.map((file) => file.name).join(", ")}`
    : "Geen factuur-PDF";
  els.sourceStatus.hidden = !reports.length && !weekFiles.length && !invoiceFiles.length;

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

  const totals = coverage.totals;
  const weekShares = coverage.weeksDetail || [];
  const timeline = coverage.timeline || {};
  if (totals?.rows || weekShares.length) {
    const reportPct = Number(totals?.percent || 0);
    const reportFrom = timeline.reportFrom || "";
    const reportTo = timeline.reportTo || "";
    const calendar = renderCalendar(timeline.days || []);
    const weekList = weekShares.map((item) => (
      `<span class="coverage-chip"><b>${escapeHtml(item.id)}</b> ${formatIso(item.from)}–${formatIso(item.to)} · ${Number(item.percent || 0)}%</span>`
    )).join("");
    els.weekCheck.hidden = false;
    els.weekCheck.innerHTML = `<div class="coverage-card">
      <div class="coverage-block">
        <div class="coverage-head">
          <strong>Kalender</strong>
          <span class="coverage-legend">Tolrapport <b>${formatIso(reportFrom)}</b> t/m <b>${formatIso(reportTo)}</b></span>
        </div>
        ${calendar}
        ${weekList ? `<div class="cal-legend">${weekList}</div>` : ""}
      </div>
      <div class="coverage-block">
        <div class="coverage-head">
          <strong>Gekoppeld aan het rittenboek</strong>
          <span class="coverage-legend"><b>${reportPct}%</b> van het tolrapport</span>
        </div>
        <div class="coverage-track" role="img" aria-label="${reportPct} procent van het tolrapport valt in het rittenboek">
          <span class="coverage-filled" style="width:${reportPct}%"></span>
        </div>
      </div>
    </div>`;
  } else {
    els.weekCheck.hidden = true;
    els.weekCheck.innerHTML = "";
  }

  els.invoiceCheck.hidden = true;

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

async function uploadFiles(fileList, expectedKind) {
  let files = [...fileList];
  if (expectedKind === "report") files = files.filter((file) => /\.(csv|xls|xlsx)$/i.test(file.name));
  if (expectedKind === "planning") files = files.filter((file) => /\.(xls|xlsx)$/i.test(file.name));
  if (expectedKind === "invoice") files = files.filter((file) => /\.pdf$/i.test(file.name)).slice(0, 1);
  if (!files.length) {
    const messages = {
      report: "Kies één of meer tolrapporten als CSV, XLS of XLSX.",
      planning: "Kies één of meer weekplanningen als XLS of XLSX.",
      invoice: "Kies één MSTS-factuur als PDF.",
    };
    setStatus(messages[expectedKind] || "Kies een geldig bestand.", "error");
    return;
  }

  const loadingLabels = {
    report: "Tolrapport verwerken",
    planning: "Weekplanningen verwerken",
    invoice: "Factuur-PDF controleren",
  };
  showLoading(loadingLabels[expectedKind] || "Bestanden verwerken", "Upload voorbereiden…", 5);
  els.reportInput.disabled = true;
  els.planningInput.disabled = true;
  els.invoiceInput.disabled = true;
  let completed = false;
  try {
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      setStatus(`Uploaden ${index + 1} van ${files.length}: ${file.name}`);
      updateLoading(10 + (index / files.length) * 55, `${file.name} comprimeren/uploaden…`);
      const prepared = await gzipFileIfHelpful(file);
      const body = new FormData();
      body.append("file", prepared.blob, file.name);
      if (prepared.compressed) body.append("compressed", "gzip");
      const response = await fetch("/api/upload?expected=" + encodeURIComponent(expectedKind), { method: "POST", body });
      const data = await readJson(response);
      if (!response.ok) throw new Error(data.error || `${file.name} kon niet worden geüpload.`);
      updateLoading(10 + ((index + 1) / files.length) * 55, `${file.name} ingelezen`);
    }
    const success = expectedKind === "report"
      ? `${files.length} tolrapport${files.length === 1 ? "" : "en"} verwerkt.`
      : expectedKind === "invoice"
        ? "Factuur-PDF verwerkt."
        : `${files.length} weekplanning${files.length === 1 ? "" : "en"} verwerkt.`;
    setStatus(success, "success");
    updateLoading(75, "Bedrijven en bedragen koppelen…");
    await loadSources();
    updateLoading(95, "Overzichten voorbereiden…");
    completed = true;
  } catch (error) {
    setStatus(error.message || "Uploaden is mislukt.", "error");
  } finally {
    els.reportInput.disabled = false;
    els.planningInput.disabled = false;
    els.invoiceInput.disabled = false;
    els.reportInput.value = "";
    els.planningInput.value = "";
    els.invoiceInput.value = "";
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
    coverage = { reports: [], totals: null };
    setStatus("Alle tijdelijke gegevens zijn gewist.", "success");
    await loadSources();
  } catch (error) {
    setStatus(error.message || "Wissen is mislukt.", "error");
  } finally {
    setBusy(els.clearSessionBtn, false);
  }
}

async function downloadFile(url, button, fallbackName, withLoading = false) {
  setBusy(button, true);
  if (withLoading) showLoading("ZIP-bestand maken", "PDF-overzichten samenstellen…", 10);
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

    if (withLoading) updateLoading(85, "ZIP-bestand downloaden…");
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
els.invoiceInput.addEventListener("change", () => uploadFiles(els.invoiceInput.files, "invoice"));
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
enableDropZone(els.invoiceDropZone, "invoice");
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
