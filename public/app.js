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
  invoiceSelect: document.getElementById("invoiceSelect"),
  zipInvoiceBtn: document.getElementById("zipInvoiceBtn"),
  searchInput: document.getElementById("searchInput"),
  summary: document.getElementById("summary"),
  invoiceCheck: document.getElementById("invoiceCheck"),
  sourceStatus: document.getElementById("sourceStatus"),
  resultsSection: document.getElementById("resultsSection"),
  companyRows: document.getElementById("companyRows"),
};

let reports = [];
let invoices = [];
let companies = [];
let activeReport = "";
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

async function getJson(url) {
  const response = await fetch(url);
  const data = await response.json();
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
    const data = await response.json();
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
  invoices = [];
  companies = [];
  activeReport = "";
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
    const data = await response.json();
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
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Verwijderen is mislukt.");
    await loadUsers();
  } catch (error) {
    els.userError.textContent = error.message || "Verwijderen is mislukt.";
    els.userError.hidden = false;
  }
}

function selectedReport() {
  return activeReport;
}

function selectedInvoice() {
  return els.invoiceSelect.value || "";
}

async function loadSources(preferredReport = "") {
  const [fileData, planningData, invoiceData] = await Promise.all([
    getJson("/api/files"),
    getJson("/api/planning"),
    getJson("/api/invoice-files"),
  ]);
  reports = fileData.files || [];
  activeReport = reports.some((item) => item.name === preferredReport)
    ? preferredReport
    : (reports[0]?.name || "");
  els.reportName.textContent = activeReport || "Nog niet geüpload";

  const weekFiles = planningData.files || [];
  els.planningFiles.textContent = weekFiles.length
    ? `${weekFiles.length} bestanden: ${weekFiles.join(", ")}`
    : "Geen weekplanningen gevonden";
  const invoiceFiles = invoiceData.files || [];
  els.invoiceFile.textContent = invoiceFiles.length
    ? `Factuur-PDF: ${invoiceFiles.map((file) => file.name).join(", ")}`
    : "Geen factuur-PDF";
  els.sourceStatus.hidden = !activeReport && !weekFiles.length && !invoiceFiles.length;

  await loadInvoices();
}

async function loadInvoices() {
  const report = selectedReport();
  if (!report) {
    invoices = [];
    companies = [];
    els.invoiceSelect.innerHTML = "";
    els.invoiceSelect.disabled = true;
    renderCompanies();
    return;
  }

  els.invoiceSelect.disabled = false;
  const data = await getJson("/api/invoices?file=" + encodeURIComponent(report));
  invoices = data.invoices || [];
  els.invoiceSelect.innerHTML = invoices.map((invoice) => {
    const title = invoice.id === "ongefactureerd"
      ? "Nog niet gefactureerd"
      : `Factuur ${invoice.id}${invoice.date ? ` (${invoice.date})` : ""}`;
    const period = invoice.from && invoice.to ? ` · ${invoice.from} t/m ${invoice.to}` : "";
    return `<option value="${escapeAttr(invoice.id)}">${escapeHtml(title + period)}</option>`;
  }).join("");
  await loadCompanies();
}

async function loadCompanies() {
  const report = selectedReport();
  const invoice = selectedInvoice();
  if (!report || !invoice) {
    companies = [];
    renderCompanies();
    return;
  }

  els.companyRows.innerHTML = `<tr><td colspan="4" class="loading">Bedrijven laden…</td></tr>`;
  const url = "/api/pdf-customers?file=" + encodeURIComponent(report)
    + "&invoice=" + encodeURIComponent(invoice);
  const data = await getJson(url);
  companies = data.customers || [];
  renderCompanies();
  await loadInvoiceCheck();
}

async function loadInvoiceCheck() {
  const report = selectedReport();
  const invoice = selectedInvoice();
  if (!report || !invoice) {
    els.invoiceCheck.hidden = true;
    return;
  }
  const url = "/api/invoice-check?file=" + encodeURIComponent(report)
    + "&invoice=" + encodeURIComponent(invoice);
  const data = await getJson(url);
  if (!data.uploaded || !data.message) {
    els.invoiceCheck.hidden = true;
    return;
  }
  const prefix = data.match ? `${data.match.name}: ` : "";
  els.invoiceCheck.textContent = prefix + data.message;
  els.invoiceCheck.className = `invoice-check ${data.status || ""}`.trim();
  els.invoiceCheck.hidden = false;
}

function renderCompanies() {
  const query = els.searchInput.value.trim().toLowerCase();
  const visible = companies.filter((item) => item.customer.toLowerCase().includes(query));
  const total = companies.reduce((sum, item) => sum + item.eur, 0);
  const missing = companies.reduce((sum, item) => sum + item.missingTripNumbers, 0);

  els.summary.innerHTML = companies.length
    ? `<span><b>${companies.length}</b> bedrijven</span>
       <span><b>${euro.format(total)}</b> tol</span>
       <span class="${missing ? "warning-text" : ""}"><b>${missing}</b> regels zonder ritnummer</span>`
    : "";

  els.resultsSection.hidden = reports.length === 0;
  els.zipInvoiceBtn.disabled = !selectedInvoice();

  if (!visible.length) {
    els.companyRows.innerHTML = companies.length
      ? `<tr><td colspan="4" class="empty-row">Geen bedrijf gevonden.</td></tr>`
      : "";
    return;
  }

  els.companyRows.innerHTML = visible.map((item) => {
    const warningClass = item.customer === "Nog te verdelen" ? "company-warning" : "";
    const missing = item.missingTripNumbers
      ? `<span class="missing">${item.missingTripNumbers}</span>`
      : "0";
    return `<tr class="${warningClass}">
      <td><strong>${escapeHtml(item.customer)}</strong></td>
      <td class="number">${euro.format(item.eur)}</td>
      <td class="number">${missing}</td>
      <td class="download-cell">
        <button type="button" class="button button-small" data-customer="${escapeAttr(item.customer)}">PDF downloaden</button>
      </td>
    </tr>`;
  }).join("");
}

async function uploadFiles(fileList, expectedKind) {
  let files = [...fileList];
  if (expectedKind === "report") files = files.filter((file) => /\.(csv|xls|xlsx)$/i.test(file.name)).slice(0, 1);
  if (expectedKind === "planning") files = files.filter((file) => /\.(xls|xlsx)$/i.test(file.name));
  if (expectedKind === "invoice") files = files.filter((file) => /\.pdf$/i.test(file.name)).slice(0, 1);
  if (!files.length) {
    const messages = {
      report: "Kies één tolrapport als CSV, XLS of XLSX.",
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
  let uploadedReport = "";
  let completed = false;
  try {
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      setStatus(`Uploaden ${index + 1} van ${files.length}: ${file.name}`);
      updateLoading(10 + (index / files.length) * 55, `${file.name} uploaden…`);
      const body = new FormData();
      body.append("file", file, file.name);
      const response = await fetch("/api/upload?expected=" + encodeURIComponent(expectedKind), { method: "POST", body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `${file.name} kon niet worden geüpload.`);
      if (data.kind === "report") uploadedReport = data.name;
      updateLoading(10 + ((index + 1) / files.length) * 55, `${file.name} ingelezen`);
    }
    const success = expectedKind === "report"
      ? "Tolrapport verwerkt."
      : expectedKind === "invoice"
        ? "Factuur-PDF verwerkt."
        : `${files.length} weekplanning${files.length === 1 ? "" : "en"} verwerkt.`;
    setStatus(success, "success");
    updateLoading(75, "Bedrijven en bedragen koppelen…");
    await loadSources(uploadedReport);
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
    invoices = [];
    reports = [];
    activeReport = "";
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
        const data = await response.json();
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
  const url = "/api/customer-pdf?file=" + encodeURIComponent(selectedReport())
    + "&invoice=" + encodeURIComponent(selectedInvoice())
    + "&customer=" + encodeURIComponent(customer);
  return downloadFile(url, button, `Toloverzicht - ${customer}.pdf`);
}

function downloadZip(button) {
  const url = "/api/customer-pdfs.zip?file=" + encodeURIComponent(selectedReport())
    + "&invoice=" + encodeURIComponent(selectedInvoice());
  const name = `Toloverzichten_${selectedInvoice()}.zip`;
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
els.invoiceSelect.addEventListener("change", loadCompanies);
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
