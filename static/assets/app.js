(() => {
  "use strict";

  const incidentId =
    new URLSearchParams(window.location.search).get("incident_id") || "INC-8492";
  const view = document.body.dataset.appView || "dashboard";
  const routes = {
    dashboard: "/dashboard",
    "dashboard-and-system-health": "/dashboard",
    "active-incidents": "/incidents",
    "incident-details": "/incidents",
    "ai-investigation": "/investigation",
    investigation: "/investigation",
    "evidence-logs": "/investigation",
    "evidence-and-logs": "/investigation",
    evidence: "/investigation",
    "fix-recommendations": "/fix-recommendations",
    "safe-simulation": "/fix-recommendations",
    simulation: "/fix-recommendations",
    "incident-reports": "/reports",
    "incident-report": "/reports",
    "ai-chatbot": "/assistant",
    chatbot: "/assistant",
    settings: "/settings",
  };

  const mascotUrl = "/assets/vitality-dachshund.png";
  const byId = (id) => document.getElementById(id);
  const element = (tag, className, text) => {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  };
  const escapeHtml = (text) =>
    String(text ?? "").replace(/[&<>"']/g, (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[character],
    );

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `Request failed (${response.status}).`);
    }
    return payload;
  }

  function toast(message, isError = false) {
    let item = byId("vitality-runtime-toast");
    if (!item) {
      item = element("div", "vitality-runtime-toast");
      item.id = "vitality-runtime-toast";
      item.setAttribute("role", "status");
      item.setAttribute("aria-live", "polite");
      document.body.append(item);
    }
    item.textContent = message;
    item.classList.toggle("is-error", isError);
    item.classList.add("visible");
    window.clearTimeout(toast.timer);
    toast.timer = window.setTimeout(() => item.classList.remove("visible"), 4200);
  }

  function showDialog(title, content, label = "Close") {
    let dialog = byId("vitality-runtime-dialog");
    if (!dialog) {
      dialog = document.createElement("dialog");
      dialog.id = "vitality-runtime-dialog";
      dialog.className = "vitality-runtime-dialog";
      document.body.append(dialog);
    }
    dialog.replaceChildren();
    const heading = element("h2", "", title);
    const body = element("pre", "vitality-dialog-body", content);
    const footer = element("div", "vitality-dialog-footer");
    const close = element("button", "vitality-dialog-close", label);
    close.type = "button";
    close.addEventListener("click", () => dialog.close());
    footer.append(close);
    dialog.append(heading, body, footer);
    if (!dialog.open) dialog.showModal();
  }

  function addImage(parent, className, alt) {
    const image = document.createElement("img");
    image.src = mascotUrl;
    image.alt = alt;
    image.className = className;
    parent.append(image);
    return image;
  }

  function makeCard(title, subtitle) {
    const card = element("section", "vitality-runtime-card");
    const heading = element("div", "vitality-runtime-card-heading");
    const titleNode = element("h2", "", title);
    heading.append(titleNode);
    if (subtitle) heading.append(element("span", "vitality-runtime-eyebrow", subtitle));
    card.append(heading);
    return card;
  }

  function addModeBanner(health, summary) {
    const main = document.querySelector("main");
    if (!main || byId("vitality-demo-banner")) return;
    const banner = element("div", "vitality-demo-banner");
    banner.id = "vitality-demo-banner";
    const badge = element("span", "vitality-demo-badge", "SAFE DEMO");
    const text = element(
      "span",
      "",
      health.alert_webhook_configured
        ? `${summary.active_incidents} active incident records. Seeded records remain synthetic; authenticated webhook events are stored separately.`
        : `${summary.active_incidents} active sample incident(s). Synthetic data only; this app is not monitoring a live website.`,
    );
    addImage(banner, "vitality-demo-dog", "Vitality dachshund mascot");
    banner.append(badge, text);
    main.prepend(banner);
  }

  function routeForDataPath(path) {
    return routes[path] || "/dashboard";
  }

  function wireNavigation() {
    document.querySelectorAll("a[data-path]").forEach((anchor) => {
      const destination = routeForDataPath(anchor.dataset.path);
      anchor.href = destination;
      if (destination === window.location.pathname) {
        anchor.style.background = "rgba(237, 0, 86, 0.10)";
        anchor.style.color = "#b90041";
        anchor.style.fontWeight = "700";
        anchor.style.borderRadius = "12px";
      } else {
        anchor.style.removeProperty("background");
      }
    });
  }

  function wireMobileNavigation() {
    const sidebar = document.querySelector("aside");
    if (!sidebar || byId("vitality-mobile-menu")) return;

    const backdrop = element("button", "vitality-mobile-backdrop");
    backdrop.type = "button";
    backdrop.id = "vitality-mobile-backdrop";
    backdrop.setAttribute("aria-label", "Close navigation menu");
    const menu = element("button", "vitality-mobile-menu", "menu");
    menu.type = "button";
    menu.id = "vitality-mobile-menu";
    menu.setAttribute("aria-label", "Open navigation menu");
    menu.setAttribute("aria-expanded", "false");
    menu.classList.add("material-symbols-outlined");
    const closeMenu = () => {
      document.body.classList.remove("vitality-nav-open");
      menu.setAttribute("aria-expanded", "false");
    };
    menu.addEventListener("click", () => {
      const open = document.body.classList.toggle("vitality-nav-open");
      menu.setAttribute("aria-expanded", String(open));
    });
    backdrop.addEventListener("click", closeMenu);
    sidebar.querySelectorAll("a[data-path]").forEach((link) => link.addEventListener("click", closeMenu));
    document.body.append(backdrop, menu);
  }

  function mountList(card, rows, emptyText, onSelect) {
    if (!rows.length) {
      card.append(element("p", "vitality-runtime-empty", emptyText));
      return;
    }
    const list = element("div", "vitality-runtime-list");
    rows.forEach((row) => {
      const button = element("button", "vitality-runtime-row");
      button.type = "button";
      const left = element("span", "vitality-runtime-row-title", row.title);
      const meta = element(
        "span",
        "vitality-runtime-row-meta",
        `${row.id} · ${row.severity} · ${row.status}${row.is_synthetic ? " · sample" : " · webhook"}`,
      );
      const text = element("span", "vitality-runtime-row-copy");
      text.append(left, meta);
      const state = element(
        "span",
        `vitality-runtime-state ${row.status === "resolved" ? "is-healthy" : "is-alert"}`,
        row.status.replaceAll("_", " "),
      );
      button.append(text, state);
      button.addEventListener("click", () => onSelect(row));
      list.append(button);
    });
    card.append(list);
  }

  async function showIncident(row) {
    try {
      const details = await api(`/api/incidents/${encodeURIComponent(row.id)}`);
      const evidence = details.evidence
        .map((item) => `• ${item.title}: ${item.description}`)
        .join("\n");
      showDialog(
        `${row.id} — ${row.title}`,
        `${row.severity.toUpperCase()} · ${row.status.toUpperCase()} · ${
          row.is_synthetic ? "SYNTHETIC DEMO" : "MONITORING WEBHOOK"
        }\nService: ${row.service}\nComponent: ${row.component}\n\n${row.summary}\n\nRecorded evidence:\n${
          evidence || "No evidence has been recorded."
        }`,
      );
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function loadRuntimePanel() {
    if (view === "settings") {
      await renderSettings();
      return;
    }

    const main = document.querySelector("main");
    if (!main || byId("vitality-runtime-panel") || view === "assistant" || view === "dashboard") return;
    try {
      const [summary, incidents] = await Promise.all([
        api("/api/dashboard/summary"),
        api("/api/incidents?limit=50"),
      ]);
      const panel = element("div", "vitality-runtime-panel");
      panel.id = "vitality-runtime-panel";

      if (view === "dashboard") {
        const card = makeCard("Connected incident data", "DEMO API");
        const stats = element("div", "vitality-runtime-stats");
        [
          ["Active incidents", summary.active_incidents],
          ["Critical", summary.critical_incidents],
          ["Services in sample", summary.services.length],
        ].forEach(([label, value]) => {
          const stat = element("div", "vitality-runtime-stat");
          stat.append(element("strong", "", String(value)), element("span", "", label));
          stats.append(stat);
        });
        card.append(stats);
        mountList(
          card,
          summary.recent_incidents.slice(0, 4),
          "No incidents are recorded.",
          showIncident,
        );
        panel.append(card);
      } else if (view === "incidents") {
        const card = makeCard("Incident records", "FROM SQLITE");
        mountList(card, incidents.items, "No incidents match this view.", showIncident);
        panel.append(card);
      } else if (view === "investigation") {
        const card = makeCard("Recorded evidence", "INC-8492 · DEMO SAMPLE");
        try {
          const details = await api(`/api/incidents/${encodeURIComponent(incidentId)}`);
          card.append(
            element(
              "p",
              "vitality-runtime-intro",
              `${details.incident.title} · ${details.incident.is_synthetic ? "Synthetic example — not a live diagnosis." : "Alert received from a monitoring webhook."}`,
            ),
          );
          const list = element("div", "vitality-runtime-list");
          details.evidence.forEach((item) => {
            const line = element("article", "vitality-runtime-evidence");
            line.append(
              element("strong", "", item.title),
              element("p", "", item.description),
              element("small", "", `${item.kind} · ${item.source}`),
            );
            list.append(line);
          });
          card.append(list);
        } catch (error) {
          card.append(element("p", "vitality-runtime-empty", error.message));
        }
        const analyze = element("button", "vitality-runtime-primary", "Run evidence-based investigation");
        analyze.type = "button";
        analyze.addEventListener("click", () => runInvestigation(incidentId));
        card.append(analyze);
        panel.append(card);
      } else if (view === "fix-recommendations") {
        const card = makeCard("Approval-gated demo recovery", "SYNTHETIC INCIDENT ONLY");
        try {
          const result = await api(`/api/incidents/${encodeURIComponent(incidentId)}/recommendations`);
          result.items.forEach((item) => {
            const recommendation = element("article", "vitality-runtime-evidence");
            recommendation.append(
              element("strong", "", item.title),
              element("p", "", item.details),
              element("small", "", `Risk: ${item.risk} · State: ${item.status}`),
            );
            card.append(recommendation);
          });
        } catch (error) {
          card.append(element("p", "vitality-runtime-empty", error.message));
        }
        const actions = element("div", "vitality-runtime-actions");
        [
          ["Preview safe simulation", () => previewSimulation(incidentId)],
          ["Approve recommendation", () => approveOnly(incidentId)],
          ["Approve & simulate recovery", () => approveAndSimulate(incidentId)],
        ].forEach(([label, action]) => {
          const button = element("button", "vitality-runtime-primary", label);
          button.type = "button";
          button.addEventListener("click", action);
          actions.append(button);
        });
        card.append(actions);
        panel.append(card);
      } else if (view === "reports") {
        const card = makeCard("Generate a grounded incident report", "RECORDED DATA ONLY");
        card.append(
          element(
            "p",
            "vitality-runtime-intro",
            "The report uses this incident’s saved timeline and evidence. Demo records remain labeled synthetic.",
          ),
        );
        const generate = element("button", "vitality-runtime-primary", "Generate incident report");
        generate.type = "button";
        generate.addEventListener("click", () => generateReport(incidentId, true));
        card.append(generate);
        panel.append(card);
      }
      if (panel.childElementCount) main.insertBefore(panel, main.children[1] || null);
    } catch (error) {
      toast(`Could not load incident data: ${error.message}`, true);
    }
  }

  async function renderSettings() {
    const main = document.querySelector("main");
    if (!main) return;
    const health = await api("/api/health");
    main.replaceChildren();
    const page = element("div", "vitality-settings-page");
    const heading = element("header", "vitality-settings-heading");
    heading.append(
      element("span", "vitality-runtime-eyebrow", "VITALITY AI · SETTINGS"),
      element("h1", "", "Configuration status"),
      element("p", "", "Demo mode is available now. Credentials are never shown in this screen."),
    );
    page.append(heading);

    const aiCard = makeCard("AI provider roles", "OPTIONAL · CONFIGURED SERVER-SIDE");
    const envNames = {
      investigation: "AI_AGENT_1_API_KEY · AI_AGENT_1_PROVIDER · AI_AGENT_1_MODEL · AI_AGENT_1_BASE_URL",
      root_cause: "AI_AGENT_2_API_KEY · AI_AGENT_2_PROVIDER · AI_AGENT_2_MODEL · AI_AGENT_2_BASE_URL",
      chatbot: "CHATBOT_API_KEY · CHATBOT_PROVIDER · CHATBOT_MODEL · CHATBOT_BASE_URL",
    };
    Object.entries(health.ai).forEach(([role, ready]) => {
      const row = element("div", "vitality-settings-row");
      row.append(
        element("strong", "", role.replaceAll("_", " ")),
        element("span", ready ? "vitality-runtime-state is-healthy" : "vitality-runtime-state", ready ? "Configured" : "Not configured"),
        element("code", "", envNames[role]),
      );
      aiCard.append(row);
    });
    page.append(aiCard);

    const monitoring = makeCard("Monitoring connection", "NO LIVE TARGET CONFIGURED");
    [
      ["Alert webhook", health.alert_webhook_configured ? "Secret configured" : "Not configured"],
      ["Health checks", "Not configured — no external target is contacted"],
      ["Prometheus / Alertmanager", "Not connected"],
      ["Database", health.database],
    ].forEach(([name, status]) => {
      const row = element("div", "vitality-settings-row");
      row.append(element("strong", "", name), element("span", "", status));
      monitoring.append(row);
    });
    page.append(monitoring);
    const note = element(
      "p",
      "vitality-settings-note",
      "Add provider settings in Replit Secrets. Never paste API keys into chat or source files. Real incident access and operator authentication must be configured before using this prototype with production data.",
    );
    page.append(note);
    main.append(page);
  }

  async function runInvestigation(id) {
    try {
      toast("Reviewing the recorded evidence…");
      const result = await api(`/api/incidents/${encodeURIComponent(id)}/investigate`, {
        method: "POST",
      });
      showDialog(
        "Evidence-based investigation",
        `${result.mode.replaceAll("_", " ").toUpperCase()}${
          result.source_is_synthetic ? " · SYNTHETIC DEMO" : " · WEBHOOK ALERT"
        }\n\nSUMMARY\n${result.summary}\n\nROOT-CAUSE HYPOTHESIS\n${result.root_cause_hypothesis}\n\nValidation required: ${result.validation_required ? "yes" : "no"}`,
      );
      toast("Investigation result saved with its evidence references.");
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function previewSimulation(id) {
    try {
      const result = await api(`/api/incidents/${encodeURIComponent(id)}/simulate`, {
        method: "POST",
      });
      showDialog(
        "Safe simulation preview",
        `SYNTHETIC SANDBOX · PRODUCTION CHANGED: NO\n\nBefore: ${result.before.p95_latency_ms} ms p95 latency, ${result.before.error_rate_percent}% errors\nAfter (simulated): ${result.after.p95_latency_ms} ms p95 latency, ${result.after.error_rate_percent}% errors\n\n${result.message}`,
      );
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function approveOnly(id) {
    if (!window.confirm("Record your approval for this recommendation? This does not run a production action.")) return;
    try {
      const result = await api(`/api/incidents/${encodeURIComponent(id)}/approve`, {
        method: "POST",
        body: JSON.stringify({ actor: "dashboard_user", note: "Approved in the demo interface." }),
      });
      toast(result.message);
      window.setTimeout(() => window.location.reload(), 800);
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function approveAndSimulate(id) {
    if (!window.confirm("Approve and run this recovery in the synthetic sandbox only? No production service will be changed.")) return;
    try {
      await api(`/api/incidents/${encodeURIComponent(id)}/approve`, {
        method: "POST",
        body: JSON.stringify({ actor: "dashboard_user", note: "Approved for synthetic simulation." }),
      });
      const result = await api(`/api/incidents/${encodeURIComponent(id)}/simulate-fix`, {
        method: "POST",
      });
      showDialog("Simulated recovery complete", `${result.message}\n\np95 latency: ${result.metrics.p95_latency_ms} ms\nError rate: ${result.metrics.error_rate_percent}%\nProduction changed: no`);
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function generateReport(id, download = false) {
    try {
      const result = await api(`/api/incidents/${encodeURIComponent(id)}/postmortem`, {
        method: "POST",
      });
      const text = JSON.stringify(result.report, null, 2);
      if (download) {
        const file = new Blob([text], { type: "application/json" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(file);
        link.download = `${id.toLowerCase()}-incident-report.json`;
        link.click();
        URL.revokeObjectURL(link.href);
        toast("Incident report downloaded as JSON.");
      } else {
        showDialog("Incident report", text);
      }
    } catch (error) {
      toast(error.message, true);
    }
  }

  function appendChatMessage(role, text, mode) {
    const thread = byId("chatThread");
    if (!thread) {
      showDialog(role === "assistant" ? "Vitality Incident Assistant" : "Your question", text);
      return;
    }
    const message = element(
      "article",
      role === "assistant" ? "vitality-chat-message is-assistant" : "vitality-chat-message is-user",
    );
    if (role === "assistant") addImage(message, "vitality-chat-dog", "Vitality dachshund assistant");
    const bubble = element("div", "vitality-chat-bubble");
    bubble.append(
      element("strong", "", role === "assistant" ? "Vitality Dog Detective" : "You"),
      element("p", "", text),
    );
    if (mode) bubble.append(element("small", "", mode.replaceAll("_", " ")));
    message.append(bubble);
    thread.append(message);
    message.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function sendChatMessage(fallbackText) {
    const input = byId("userChatInput") || document.querySelector('input[placeholder*="ask" i]');
    const question = (fallbackText || input?.value || "").trim();
    if (!question) {
      toast("Enter a question about the incident first.", true);
      return;
    }
    if (input && !fallbackText) input.value = "";
    appendChatMessage("user", question);
    const loading = byId("vitality-chat-loading");
    if (!loading && byId("chatThread")) {
      const item = element("p", "vitality-chat-loading", "Vitality is checking the saved incident evidence…");
      item.id = "vitality-chat-loading";
      byId("chatThread").append(item);
    }
    try {
      const result = await api("/api/chat", {
        method: "POST",
        body: JSON.stringify({ message: question, incident_id: incidentId }),
      });
      byId("vitality-chat-loading")?.remove();
      appendChatMessage("assistant", result.answer, result.mode);
    } catch (error) {
      byId("vitality-chat-loading")?.remove();
      appendChatMessage("assistant", `I could not retrieve an answer: ${error.message}`);
    }
  }

  async function shareCurrentIncident() {
    const url = `${window.location.origin}/incidents?incident_id=${encodeURIComponent(incidentId)}`;
    try {
      await navigator.clipboard.writeText(url);
      toast("Incident link copied.");
    } catch {
      showDialog("Incident link", url);
    }
  }

  window.handlePromptClick = (button) => {
    const prompt = button?.innerText?.replace(/^🐾\s*/, "").trim();
    const input = byId("userChatInput");
    if (input && prompt) {
      input.value = prompt.replace(/^["“]|["”]$/g, "");
      input.focus();
    } else if (prompt) {
      window.location.href = `/assistant?incident_id=${encodeURIComponent(incidentId)}&q=${encodeURIComponent(prompt)}`;
    }
  };
  window.sendChatMessage = () => sendChatMessage();
  window.triggerSandboxSimulation = () => previewSimulation(incidentId);
  window.draftSlackUpdate = () => generateReport(incidentId, false);
  window.triggerScan = () => runInvestigation(incidentId);
  window.simulateExecution = () => approveAndSimulate(incidentId);
  window.openTrackerModal = () => byId("incidentModal")?.classList.remove("hidden");
  window.closeTrackerModal = () => byId("incidentModal")?.classList.add("hidden");

  document.addEventListener(
    "click",
    (event) => {
      const button = event.target.closest("button, a");
      if (!button) return;
      const id = button.id || "";
      const text = (button.innerText || button.getAttribute("aria-label") || "").trim().toLowerCase();

      if (button.matches("a[data-path]")) return;
      if (id === "scanButton") {
        event.preventDefault();
        event.stopImmediatePropagation();
        runInvestigation(incidentId);
      } else if (id === "execBtn" || id === "approve-btn" || id === "modal-approve-btn") {
        event.preventDefault();
        event.stopImmediatePropagation();
        approveAndSimulate(incidentId);
      } else if (id === "run-sim-btn") {
        event.preventDefault();
        event.stopImmediatePropagation();
        previewSimulation(incidentId);
      } else if (id === "ask-dog-trigger") {
        event.preventDefault();
        event.stopImmediatePropagation();
        window.location.href = `/assistant?incident_id=${encodeURIComponent(incidentId)}`;
      } else if (id === "view-diff-btn") {
        event.preventDefault();
        event.stopImmediatePropagation();
        api(`/api/incidents/${encodeURIComponent(incidentId)}/recommendations`)
          .then((result) => showDialog("Recommended change", result.items.map((item) => `${item.title}\n${item.details}\nStatus: ${item.status}`).join("\n\n") || "No recommendation is stored."))
          .catch((error) => toast(error.message, true));
      } else if (id === "close-diff-modal" || id === "cancel-diff-modal") {
        byId("diff-modal")?.classList.add("hidden");
      } else if (id === "close-dog-modal") {
        byId("dog-assistant-modal")?.classList.add("hidden");
      } else if (id === "jargonToggle") {
        button.setAttribute("aria-pressed", button.getAttribute("aria-pressed") === "true" ? "false" : "true");
      } else if (text === "ask") {
        const input = button.parentElement?.querySelector("input");
        if (input?.value.trim()) {
          event.preventDefault();
          sendChatMessage(input.value);
        }
      } else if (/download.*(pdf|report)|generate.*(report|postmortem)/.test(text)) {
        event.preventDefault();
        generateReport(incidentId, /download/.test(text));
      } else if (/share with team|share incident/.test(text)) {
        event.preventDefault();
        shareCurrentIncident();
      } else if (/safe simulation|run simulation/.test(text)) {
        event.preventDefault();
        previewSimulation(incidentId);
      }
    },
    true,
  );

  document.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" && event.target?.id === "userChatInput") || event.target?.id === "userChatSend") {
      event.preventDefault();
      sendChatMessage();
    }
  });

  async function boot() {
    wireNavigation();
    wireMobileNavigation();
    try {
      const [health, summary] = await Promise.all([
        api("/api/health"),
        api("/api/dashboard/summary"),
      ]);
      addModeBanner(health, summary);
      const criticalBadge = document.querySelector('a[data-path="active-incidents"] > span');
      if (criticalBadge) {
        criticalBadge.textContent = `${summary.critical_incidents} Critical`;
      }
    } catch (error) {
      toast(`Backend unavailable: ${error.message}`, true);
    }
    loadRuntimePanel().catch((error) => toast(error.message, true));

    const input = byId("userChatInput");
    const question = new URLSearchParams(window.location.search).get("q");
    if (input && question) {
      input.value = question;
      input.focus();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();