"use strict";

const POLL_INTERVAL_MS = 15000;

const STATUS_LABELS = {
  normal: "Normal",
  warning: "Warning",
  critical: "Critical",
  unknown: "Unknown",
};

let refreshInFlight = false;

function normalizeStatus(status) {
  return Object.prototype.hasOwnProperty.call(STATUS_LABELS, status) ? status : "unknown";
}

function createStatusBadge(status) {
  const key = normalizeStatus(status);
  const badge = document.createElement("span");
  badge.className = `badge badge-${key}`;

  const dot = document.createElement("span");
  dot.className = "status-dot";
  dot.setAttribute("aria-hidden", "true");

  const label = document.createElement("span");
  label.textContent = STATUS_LABELS[key];

  badge.append(dot, label);
  return badge;
}

function updateOverviewCounts(summaries) {
  const counts = { total: summaries.length, normal: 0, warning: 0, critical: 0, unknown: 0 };
  for (const item of summaries) {
    const key = normalizeStatus(item.status);
    counts[key] += 1;
  }
  document.getElementById("count-total").textContent = String(counts.total);
  document.getElementById("count-normal").textContent = String(counts.normal);
  document.getElementById("count-warning").textContent = String(counts.warning);
  document.getElementById("count-critical").textContent = String(counts.critical);
  document.getElementById("count-unknown").textContent = String(counts.unknown);

  const countLabel = document.getElementById("targets-count-label");
  countLabel.textContent = `${counts.total} ${counts.total === 1 ? "target" : "targets"}`;
}

function buildTargetRow(item) {
  const tr = document.createElement("tr");
  tr.dataset.targetId = String(item.target_id);
  tr.dataset.status = normalizeStatus(item.status);

  const statusTd = document.createElement("td");
  statusTd.appendChild(createStatusBadge(item.status));

  const nameTd = document.createElement("td");
  nameTd.className = "target-name";
  nameTd.textContent = item.name;

  const hostTd = document.createElement("td");
  hostTd.className = "target-host";
  hostTd.textContent = item.host_or_url;

  const typeTd = document.createElement("td");
  const typeBadge = document.createElement("span");
  typeBadge.className = "badge badge-neutral";
  typeBadge.textContent = String(item.check_type || "http").toUpperCase();
  typeTd.appendChild(typeBadge);

  const intervalTd = document.createElement("td");
  intervalTd.textContent = `${item.interval_seconds}s`;

  const thresholdsTd = document.createElement("td");
  thresholdsTd.textContent = `${item.warning_ms} ms / ${item.critical_ms} ms`;

  const scheduleTd = document.createElement("td");
  const scheduleBadge = document.createElement("span");
  scheduleBadge.className = item.enabled ? "badge badge-normal" : "badge badge-neutral";
  scheduleBadge.textContent = item.enabled ? "Enabled" : "Paused";
  scheduleTd.appendChild(scheduleBadge);

  tr.append(statusTd, nameTd, hostTd, typeTd, intervalTd, thresholdsTd, scheduleTd);
  return tr;
}

function renderTargets(summaries) {
  const loadingBox = document.getElementById("targets-loading");
  const emptyBox = document.getElementById("targets-empty");
  const tableWrap = document.getElementById("targets-table-wrap");
  const tbody = document.getElementById("targets-tbody");

  loadingBox.hidden = true;
  tbody.replaceChildren();

  if (summaries.length === 0) {
    tableWrap.hidden = true;
    emptyBox.hidden = false;
    return;
  }

  emptyBox.hidden = true;
  const fragment = document.createDocumentFragment();
  for (const item of summaries) {
    fragment.appendChild(buildTargetRow(item));
  }
  tbody.appendChild(fragment);
  tableWrap.hidden = false;
}

function setConnectionState(online, errorMessage = "") {
  const pill = document.getElementById("api-status");
  const errorBanner = document.getElementById("api-error");

  if (online) {
    pill.className = "connection-pill connection-online";
    pill.textContent = "API Connected";
    errorBanner.hidden = true;
    errorBanner.textContent = "";
  } else {
    pill.className = "connection-pill connection-offline";
    pill.textContent = "API Disconnected";
    errorBanner.textContent = errorMessage || "Could not load targets from the API.";
    errorBanner.hidden = false;
  }
}

async function loadDashboard() {
  if (refreshInFlight) {
    return;
  }
  refreshInFlight = true;
  const refreshBtn = document.getElementById("refresh-btn");
  if (refreshBtn) {
    refreshBtn.disabled = true;
    refreshBtn.textContent = "Refreshing...";
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10000);

  try {
    const response = await fetch("/targets/summaries", {
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`API returned HTTP ${response.status}`);
    }
    const summaries = await response.json();
    if (!Array.isArray(summaries)) {
      throw new Error("Unexpected response format from /targets/summaries");
    }
    updateOverviewCounts(summaries);
    renderTargets(summaries);
    setConnectionState(true);
    const updatedEl = document.getElementById("last-updated");
    if (updatedEl) {
      updatedEl.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    }
  } catch (err) {
    const loadingBox = document.getElementById("targets-loading");
    if (loadingBox) {
      loadingBox.hidden = true;
    }
    const message =
      err.name === "AbortError"
        ? "Request timed out after 10 seconds"
        : err.message || "network error";
    setConnectionState(false, `Failed to load targets from the monitoring API (${message}).`);
  } finally {
    clearTimeout(timeoutId);
    refreshInFlight = false;
    if (refreshBtn) {
      refreshBtn.disabled = false;
      refreshBtn.textContent = "Refresh";
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const refreshBtn = document.getElementById("refresh-btn");
  refreshBtn.addEventListener("click", () => {
    loadDashboard();
  });

  loadDashboard();
  setInterval(() => {
    if (!document.hidden) {
      loadDashboard();
    }
  }, POLL_INTERVAL_MS);

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      loadDashboard();
    }
  });
});
