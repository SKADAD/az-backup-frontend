/* AzDO Backup Console — dashboard logic. Plain JS, no build step. */
"use strict";

const $ = (id) => document.getElementById(id);

let latestRuns = [];
let logPoll = null;

/* ---------- formatting ---------- */

function fmtBytes(n) {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
  const v = n / Math.pow(1024, i);
  return (v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)) + " " + units[i];
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function fmtRelative(iso) {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (isNaN(ms)) return iso;
  const min = Math.round(ms / 60000);
  if (min < 1) return "just now";
  if (min < 60) return min + " min ago";
  const h = Math.round(min / 60);
  if (h < 48) return h + " h ago";
  return Math.round(h / 24) + " days ago";
}

function fmtDuration(sec) {
  if (sec == null || !Number.isFinite(sec)) return "—";
  if (sec < 60) return Math.round(sec) + "s";
  const m = Math.floor(sec / 60);
  if (m < 60) return m + "m " + Math.round(sec % 60) + "s";
  return Math.floor(m / 60) + "h " + (m % 60) + "m";
}

const STATUS_LABEL = {
  ok: "OK",
  completed_with_errors: "With errors",
  incomplete: "Incomplete",
  failed: "Failed",
  running: "Running",
  succeeded: "Succeeded",
  unknown: "Unknown",
};

function badge(status) {
  const span = document.createElement("span");
  span.className = "badge " + status;
  const dot = document.createElement("span");
  dot.className = "dot";
  span.appendChild(dot);
  span.appendChild(document.createTextNode(STATUS_LABEL[status] || status));
  return span;
}

/* ---------- api ---------- */

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* keep statusText */ }
    throw new Error(detail);
  }
  return res.json();
}

/* ---------- status pills ---------- */

async function loadStatus() {
  const s = await api("/api/status");
  $("root-path").textContent = s.backup_root;
  const tool = $("pill-tool");
  tool.textContent = "tool: " + s.backup_cmd;
  tool.className = "pill " + (s.tool_available ? "on" : "off");
  tool.title = s.tool_available
    ? "azdo-backup CLI found on PATH"
    : "'" + s.backup_cmd + "' not found on PATH — install azdo-az-backup or set AZDO_BACKUP_CMD";
  const pat = $("pill-pat");
  pat.textContent = "PAT";
  pat.className = "pill " + (s.pat_present ? "on" : "off");
  pat.title = s.pat_present
    ? "PAT detected in environment"
    : "No PAT — set " + s.pat_env_vars.join(" or ") + " before starting the service";
  if (s.default_org && !$("f-org").value) $("f-org").value = s.default_org;
}

/* ---------- dashboard ---------- */

async function loadBackups(refresh) {
  const data = await api("/api/backups" + (refresh ? "?refresh=1" : ""));
  latestRuns = data.runs;
  renderTiles(data);
  renderChart(data.runs);
  renderTable(data.runs);
}

function renderTiles(data) {
  const t = data.totals;
  const last = t.last_run;
  $("tile-last").textContent = last ? fmtRelative(last.created_at) : "never";
  $("tile-last-sub").textContent = last ? fmtDate(last.created_at) + " · " + (STATUS_LABEL[last.status] || last.status) : "";
  $("tile-size").textContent = fmtBytes(t.size_bytes);
  $("tile-size-sub").textContent = "under " + data.backup_root;
  $("tile-count").textContent = t.count;
  const running = data.runs.filter((r) => r.status === "running").length;
  $("tile-count-sub").textContent = running ? running + " running now" : "";
  $("tile-projects").textContent = t.projects.length;
  $("tile-projects-sub").textContent = t.projects.slice(0, 4).join(", ") + (t.projects.length > 4 ? ", …" : "");
}

/* Single-series bar chart (size per run over time): series-1 blue, thin bars
   with rounded data-ends, hairline gridlines, hover tooltip. */
function renderChart(runs) {
  const host = $("chart");
  const done = runs
    .filter((r) => r.size_bytes > 0 && r.created_at)
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
    .slice(-20);
  $("chart-note").textContent = done.length ? "last " + done.length + " runs" : "";
  host.innerHTML = "";
  if (!done.length) {
    host.innerHTML = '<div class="empty">Nothing to plot yet.</div>';
    return;
  }

  const css = getComputedStyle(document.documentElement);
  const color = (v) => css.getPropertyValue(v).trim();
  const W = Math.max(host.clientWidth || 560, 320);
  const H = 220;
  const pad = { top: 14, right: 10, bottom: 26, left: 54 };
  const iw = W - pad.left - pad.right;
  const ih = H - pad.top - pad.bottom;
  const maxV = Math.max(...done.map((r) => r.size_bytes));

  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Bar chart of backup size per run");

  // y gridlines + ticks at 0 / half / max (clean, recessive)
  const ticks = [0, maxV / 2, maxV];
  for (const v of ticks) {
    const y = pad.top + ih - (maxV ? (v / maxV) * ih : 0);
    const line = document.createElementNS(ns, "line");
    line.setAttribute("x1", pad.left); line.setAttribute("x2", W - pad.right);
    line.setAttribute("y1", y); line.setAttribute("y2", y);
    line.setAttribute("stroke", v === 0 ? color("--baseline") : color("--grid"));
    line.setAttribute("stroke-width", "1");
    svg.appendChild(line);
    const label = document.createElementNS(ns, "text");
    label.setAttribute("x", pad.left - 8); label.setAttribute("y", y + 4);
    label.setAttribute("text-anchor", "end");
    label.setAttribute("fill", color("--text-muted"));
    label.setAttribute("font-size", "11");
    label.textContent = fmtBytes(v);
    svg.appendChild(label);
  }

  const band = iw / done.length;
  const barW = Math.min(24, Math.max(6, band - 2)); // ≤24px thick, ≥2px surface gap
  const tooltip = $("chart-tooltip");
  const card = host.closest(".card");

  done.forEach((r, i) => {
    const h = maxV ? (r.size_bytes / maxV) * ih : 0;
    const x = pad.left + i * band + (band - barW) / 2;
    const y = pad.top + ih - h;
    const rTop = Math.min(4, barW / 2, h); // rounded data-end, square baseline
    const bar = document.createElementNS(ns, "path");
    bar.setAttribute("d",
      `M${x},${pad.top + ih} V${y + rTop} Q${x},${y} ${x + rTop},${y} H${x + barW - rTop} ` +
      `Q${x + barW},${y} ${x + barW},${y + rTop} V${pad.top + ih} Z`);
    bar.setAttribute("fill", color("--series-1"));
    svg.appendChild(bar);

    // full-column transparent hit target (bigger than the mark)
    const hit = document.createElementNS(ns, "rect");
    hit.setAttribute("x", pad.left + i * band); hit.setAttribute("y", pad.top);
    hit.setAttribute("width", band); hit.setAttribute("height", ih);
    hit.setAttribute("fill", "transparent");
    hit.addEventListener("mousemove", (ev) => {
      tooltip.hidden = false;
      tooltip.innerHTML =
        `<div class="tt-title">${escapeHtml(r.id)}</div>` +
        `<div class="tt-row">${fmtDate(r.created_at)}</div>` +
        `<div class="tt-row">${fmtBytes(r.size_bytes)} · ${r.projects.length} project${r.projects.length === 1 ? "" : "s"}</div>`;
      const box = card.getBoundingClientRect();
      const tx = Math.min(ev.clientX - box.left + 12, box.width - tooltip.offsetWidth - 8);
      tooltip.style.left = Math.max(tx, 4) + "px";
      tooltip.style.top = (ev.clientY - box.top + 14) + "px";
      bar.setAttribute("fill", color("--series-1-soft"));
    });
    hit.addEventListener("mouseleave", () => {
      tooltip.hidden = true;
      bar.setAttribute("fill", color("--series-1"));
    });
    svg.appendChild(hit);

    // sparse x labels: first, last, and roughly every 5th
    if (i === 0 || i === done.length - 1 || i % 5 === 0) {
      const label = document.createElementNS(ns, "text");
      label.setAttribute("x", pad.left + i * band + band / 2);
      label.setAttribute("y", H - 8);
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("fill", color("--text-muted"));
      label.setAttribute("font-size", "10.5");
      const d = new Date(r.created_at);
      label.textContent = isNaN(d) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
      svg.appendChild(label);
    }
  });

  host.appendChild(svg);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderTable(runs) {
  const body = $("runs-body");
  body.innerHTML = "";
  $("runs-empty").hidden = runs.length > 0;
  for (const r of runs) {
    const tr = document.createElement("tr");

    const name = document.createElement("td");
    name.textContent = r.id + (r.kind === "zip" ? " 🗜" : "");
    name.title = r.path;
    tr.appendChild(name);

    const date = document.createElement("td");
    date.textContent = fmtDate(r.created_at);
    date.title = fmtRelative(r.created_at);
    tr.appendChild(date);

    const org = document.createElement("td");
    org.textContent = r.org || "—";
    tr.appendChild(org);

    const projects = document.createElement("td");
    const shown = r.projects.slice(0, 3);
    for (const p of shown) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = p;
      projects.appendChild(chip);
    }
    if (r.projects.length > 3) {
      const more = document.createElement("span");
      more.className = "chip";
      more.textContent = "+" + (r.projects.length - 3);
      more.title = r.projects.join(", ");
      projects.appendChild(more);
    }
    if (!r.projects.length) projects.textContent = "—";
    tr.appendChild(projects);

    const size = document.createElement("td");
    size.className = "num";
    size.textContent = fmtBytes(r.size_bytes);
    tr.appendChild(size);

    const dur = document.createElement("td");
    dur.className = "num";
    dur.textContent = fmtDuration(r.duration_seconds);
    tr.appendChild(dur);

    const status = document.createElement("td");
    status.appendChild(badge(r.status));
    tr.appendChild(status);

    const actions = document.createElement("td");
    if (r.status === "running" && r.job_id) {
      const watch = document.createElement("button");
      watch.className = "btn ghost small";
      watch.textContent = "Watch log";
      watch.onclick = () => openLog(r.job_id, "Backup · " + r.id);
      actions.appendChild(watch);
    } else {
      const detail = document.createElement("button");
      detail.className = "btn ghost small";
      detail.textContent = "Details";
      detail.onclick = () => openDetail(r.id);
      actions.appendChild(detail);
      if (r.kind === "dir") {
        const logBtn = document.createElement("button");
        logBtn.className = "btn ghost small";
        logBtn.textContent = "Log";
        logBtn.onclick = () => openSavedLog(r);
        actions.appendChild(logBtn);
      }
      const verify = document.createElement("button");
      verify.className = "btn ghost small";
      verify.textContent = "Verify";
      verify.onclick = async () => {
        verify.disabled = true;
        try {
          const res = await api(`/api/backups/${encodeURIComponent(r.id)}/verify`, { method: "POST" });
          openLog(res.job_id, "Verify · " + r.id);
        } catch (e) {
          alert("Verify failed to start: " + e.message);
        } finally {
          verify.disabled = false;
        }
      };
      actions.appendChild(verify);
    }
    tr.appendChild(actions);
    body.appendChild(tr);
  }
}

/* ---------- run detail modal ---------- */

async function openDetail(runId) {
  let d;
  try {
    d = await api("/api/backups/" + encodeURIComponent(runId));
  } catch (e) {
    alert("Could not load detail: " + e.message);
    return;
  }
  $("detail-title").textContent = d.id;
  const body = $("detail-body");
  body.innerHTML = "";
  const dl = document.createElement("dl");
  dl.className = "kv";
  const add = (k, v) => {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    dl.appendChild(dt); dl.appendChild(dd);
  };
  add("Path", d.path);
  add("Date", fmtDate(d.created_at) + " (" + fmtRelative(d.created_at) + ")");
  add("Size on disk", fmtBytes(d.size_bytes));
  add("Org", d.org || "—");
  add("Projects", d.projects.length ? d.projects.join(", ") : "—");
  add("Status", (STATUS_LABEL[d.status] || d.status) + (d.error_count ? ` (${d.error_count} errors)` : ""));
  if (d.duration_seconds != null) add("Duration", fmtDuration(d.duration_seconds));
  body.appendChild(dl);
  if (d.summary) {
    const h = document.createElement("dt");
    h.className = "subtle";
    h.textContent = "summary.json";
    body.appendChild(h);
    const pre = document.createElement("pre");
    pre.className = "json";
    pre.textContent = JSON.stringify(d.summary, null, 2);
    body.appendChild(pre);
  }
  $("detail-modal").hidden = false;
}

/* ---------- job log ---------- */

function openLog(jobId, title) {
  if (logPoll) clearTimeout(logPoll);
  $("log-card").hidden = false;
  $("log-title").textContent = title;
  const out = $("log-output");
  out.textContent = "";
  let offset = 0;

  const tick = async () => {
    let snap;
    try {
      snap = await api(`/api/jobs/${jobId}?offset=${offset}`);
    } catch (e) {
      out.textContent += "\n[log unavailable: " + e.message + "]";
      return;
    }
    if (snap.lines.length) {
      const stick = out.scrollTop + out.clientHeight >= out.scrollHeight - 8;
      out.textContent += (out.textContent ? "\n" : "") + snap.lines.join("\n");
      if (stick) out.scrollTop = out.scrollHeight;
      offset = snap.next_offset;
    }
    const state = $("log-state");
    state.replaceWith(Object.assign(badge(snap.state), { id: "log-state" }));
    if (snap.state === "running") {
      logPoll = setTimeout(tick, 1500);
    } else {
      loadBackups(true).catch(() => {});
    }
  };
  tick();
  $("log-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* Show the saved frontend.log of a finished run (no polling). */
async function openSavedLog(run) {
  if (logPoll) clearTimeout(logPoll);
  $("log-card").hidden = false;
  $("log-title").textContent = "Saved log · " + run.id;
  const out = $("log-output");
  out.textContent = "Loading…";
  $("log-state").replaceWith(Object.assign(badge(run.status), { id: "log-state" }));
  try {
    const res = await fetch(`/api/backups/${encodeURIComponent(run.id)}/log`);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* keep statusText */ }
      throw new Error(detail);
    }
    out.textContent = (await res.text()) || "(log is empty)";
    out.scrollTop = out.scrollHeight;
  } catch (e) {
    out.textContent = "Could not load saved log: " + e.message;
  }
  $("log-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------- new backup form ---------- */

function formScope() {
  return document.querySelector('input[name="scope"]:checked').value;
}

function wireForm() {
  for (const radio of document.querySelectorAll('input[name="scope"]')) {
    radio.addEventListener("change", () => {
      $("project-row").hidden = formScope() !== "one";
      $("exclude-row").hidden = formScope() !== "all";
    });
  }

  $("btn-fetch-projects").addEventListener("click", async () => {
    const org = $("f-org").value.trim();
    const msg = $("form-msg");
    if (!org) { msg.textContent = "Enter the organization URL first."; msg.className = "form-msg error"; return; }
    msg.textContent = "Fetching projects…"; msg.className = "form-msg";
    try {
      const res = await api("/api/org/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org }),
      });
      const list = $("project-list");
      list.innerHTML = "";
      for (const p of res.projects) {
        const opt = document.createElement("option");
        opt.value = p;
        list.appendChild(opt);
      }
      msg.textContent = res.projects.length + " projects found.";
    } catch (e) {
      msg.textContent = e.message; msg.className = "form-msg error";
    }
  });

  $("backup-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const msg = $("form-msg");
    const payload = {
      org: $("f-org").value.trim(),
      all_projects: formScope() === "all",
      project: formScope() === "one" ? $("f-project").value.trim() : null,
      exclude_projects: formScope() === "all" ? ($("f-exclude").value.trim() || null) : null,
      workers: $("f-workers").value ? Number($("f-workers").value) : null,
      skip_repos: $("f-skip-repos").checked,
      archive: $("f-archive").checked,
    };
    if (!payload.all_projects && !payload.project) {
      msg.textContent = "Enter a project name."; msg.className = "form-msg error"; return;
    }
    if (payload.all_projects) {
      const scope = payload.exclude_projects
        ? `ALL projects (except: ${payload.exclude_projects})`
        : "ALL projects";
      if (!confirm(`This will back up ${scope} in ${payload.org}.\n\nStart the full-organization backup?`)) {
        msg.textContent = "Cancelled.";
        return;
      }
    }
    $("btn-start").disabled = true;
    msg.textContent = "Starting…"; msg.className = "form-msg";
    try {
      const res = await api("/api/backups", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      msg.textContent = "Backup started: " + res.run_id;
      openLog(res.job_id, "Backup · " + res.run_id);
      loadBackups().catch(() => {});
    } catch (e) {
      msg.textContent = e.message; msg.className = "form-msg error";
    } finally {
      $("btn-start").disabled = false;
    }
  });
}

/* ---------- init ---------- */

function init() {
  wireForm();
  $("btn-refresh").addEventListener("click", () => loadBackups(true).catch(console.error));
  $("btn-close-log").addEventListener("click", () => {
    if (logPoll) clearTimeout(logPoll);
    $("log-card").hidden = true;
  });
  $("btn-close-detail").addEventListener("click", () => { $("detail-modal").hidden = true; });
  $("detail-modal").addEventListener("click", (ev) => {
    if (ev.target === $("detail-modal")) $("detail-modal").hidden = true;
  });
  window.addEventListener("resize", () => renderChart(latestRuns));

  loadStatus().catch(console.error);
  loadBackups().catch(console.error);
  setInterval(() => loadBackups().catch(() => {}), 30000);
}

document.addEventListener("DOMContentLoaded", init);
