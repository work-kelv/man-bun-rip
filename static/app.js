(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const state = { format: "mp3", speed: 1, searching: false };

  const FORMAT_HINTS = {
    wav: "Uncompressed — best for DAWs & transcription",
    mp3: "320 kbps — small files, great for phones",
    m4a: "AAC audio — plays everywhere on Apple devices",
    mp4: "MP4 video up to 1080p — watch the fingerings",
  };
  const FORMAT_LABELS = { wav: "WAV", mp3: "MP3", m4a: "M4A", mp4: "VIDEO" };
  const IS_MAC = /Mac/i.test(navigator.platform || navigator.userAgent);
  const REVEAL_LABEL = IS_MAC ? "Show in Finder" : "Show in folder";

  const els = {
    form: $("#search-form"),
    input: $("#search-input"),
    searchBtn: $("#search-btn"),
    results: $("#results"),
    empty: $("#empty"),
    notice: $("#notice"),
    formatGroup: $("#format-group"),
    formatHint: $("#format-hint"),
    speed: $("#speed-select"),
    updateBtn: $("#update-btn"),
    updateDot: $("#update-dot"),
    version: $("#ytdlp-version"),
    template: $("#card-template"),
    folderPath: $("#folder-path"),
    folderChange: $("#folder-change"),
    folderReset: $("#folder-reset"),
    themeBtn: $("#theme-btn"),
  };

  // ------------------------------------------------------------------ api helper
  async function api(path, options = {}) {
    const opts = { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } };
    let res;
    try {
      res = await fetch(path, opts);
    } catch {
      throw new Error("Can't reach the app's local server. Try restarting Man Bun Rip.");
    }
    let body = null;
    try { body = await res.json(); } catch { /* not JSON */ }
    if (!res.ok) {
      let detail = body && body.detail;
      if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join("; ");
      throw new Error(detail || `Request failed (${res.status})`);
    }
    return body;
  }

  function showNotice(message, kind = "error") {
    if (!message) { els.notice.hidden = true; return; }
    els.notice.textContent = message;
    els.notice.className = `notice ${kind === "info" ? "info" : ""}`;
    els.notice.hidden = false;
  }

  // ------------------------------------------------------------------ settings
  els.formatGroup.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-format]");
    if (!btn) return;
    state.format = btn.dataset.format;
    for (const b of els.formatGroup.querySelectorAll("button")) {
      b.setAttribute("aria-checked", String(b === btn));
    }
    els.formatHint.textContent = FORMAT_HINTS[state.format];
  });
  els.speed.addEventListener("change", () => { state.speed = parseFloat(els.speed.value); });

  // ------------------------------------------------------------------ search
  els.form.addEventListener("submit", (e) => {
    e.preventDefault();
    runSearch(els.input.value);
  });
  document.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip[data-q]");
    if (chip) { els.input.value = chip.dataset.q; runSearch(chip.dataset.q); }
  });

  async function runSearch(query) {
    query = (query || "").trim();
    if (!query || state.searching) return;
    state.searching = true;
    showNotice("");
    els.searchBtn.disabled = true;
    els.searchBtn.innerHTML = '<span class="spinner"></span>Searching';
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
      renderResults(data.results, query);
    } catch (err) {
      showNotice(err.message);
    } finally {
      state.searching = false;
      els.searchBtn.disabled = false;
      els.searchBtn.textContent = "Search";
    }
  }

  function renderResults(results, query) {
    // Keep cards that have downloads in flight, so progress isn't lost.
    const busy = [...els.results.querySelectorAll(".card")].filter((c) => c.querySelector(".job.running"));
    els.results.replaceChildren();
    if (!results.length) {
      const empty = els.empty.cloneNode(true);
      empty.querySelector("h2").textContent = "No results";
      empty.querySelector("p").textContent = `Nothing found for “${query}”. Try different words or paste a link.`;
      empty.querySelector(".chips").remove();
      els.results.append(empty);
      return;
    }
    const head = document.createElement("div");
    head.className = "results-head";
    const left = document.createElement("span");
    left.append("Results for ");
    const strong = document.createElement("strong");
    strong.textContent = query;
    left.append(strong);
    const right = document.createElement("span");
    right.textContent = `${results.length} track${results.length === 1 ? "" : "s"}`;
    head.append(left, right);
    els.results.append(head);

    const ids = new Set(results.map((r) => r.id));
    for (const card of busy) if (!ids.has(card.dataset.id)) els.results.append(card);
    for (const r of results) {
      const existing = busy.find((c) => c.dataset.id === r.id);
      els.results.append(existing || buildCard(r));
    }
    els.results.scrollTop = 0;
  }

  function buildCard(item) {
    const card = els.template.content.firstElementChild.cloneNode(true);
    card.dataset.id = item.id;
    const img = $(".thumb img", card);
    img.src = item.thumbnail;
    img.onerror = () => { img.style.visibility = "hidden"; };
    $(".badge", card).textContent = item.duration;
    $(".title", card).textContent = item.title;
    $(".title", card).title = item.title;
    $(".channel", card).textContent = item.uploader || "Unknown channel";
    $(".js-download", card).addEventListener("click", () => startDownload(card, item));
    return card;
  }

  // ------------------------------------------------------------------ downloads
  async function startDownload(card, item) {
    const fmt = state.format;
    const speed = state.speed;
    const job = buildJob(fmt, speed);
    $(".jobs", card).append(job.el);
    job.update({ status: "queued", stage: "Starting…", percent: null });

    let taskId;
    try {
      const res = await api("/api/download", {
        method: "POST",
        body: JSON.stringify({ video_id: item.id, format: fmt, speed, title: item.title }),
      });
      taskId = res.task_id;
    } catch (err) {
      job.update({ status: "error", stage: "Failed", error: err.message });
      return;
    }
    job.taskId = taskId;

    const es = new EventSource(`/api/progress/${taskId}`);
    es.onmessage = (ev) => {
      const snap = JSON.parse(ev.data);
      job.update(snap);
      if (["done", "error", "cancelled"].includes(snap.status)) es.close();
    };
    es.onerror = () => {
      if (job.finished) { es.close(); return; }
      // EventSource auto-reconnects; the server resends the latest state.
    };
  }

  function buildJob(fmt, speed) {
    const el = document.createElement("div");
    el.className = "job running";
    el.innerHTML = `
      <div class="job-head">
        <span class="job-tag"></span>
        <span class="job-stage"></span>
        <span class="job-stats"></span>
        <span class="job-actions"></span>
      </div>
      <div class="bar"><span style="width:0%"></span></div>`;
    const tag = speed < 1 ? `${FORMAT_LABELS[fmt]} · ${Math.round(speed * 100)}%` : FORMAT_LABELS[fmt];
    $(".job-tag", el).textContent = tag;

    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn btn-ghost btn-sm";
    cancelBtn.textContent = "Cancel";
    $(".job-actions", el).append(cancelBtn);

    const job = {
      el,
      taskId: null,
      finished: false,
      update(s) {
        el.classList.remove("running", "done", "error", "cancelled");
        const finished = ["done", "error", "cancelled"].includes(s.status);
        job.finished = finished;
        el.classList.add(finished ? s.status : "running");

        $(".job-stage", el).textContent = s.status === "error" ? (s.error || "Download failed") : (s.stage || "");
        const stats = [];
        if (!finished && s.percent != null) stats.push(`${Math.round(s.percent)}%`);
        if (!finished && s.speed_text) stats.push(s.speed_text);
        if (!finished && s.eta_text) stats.push(s.eta_text);
        $(".job-stats", el).textContent = stats.join(" · ");

        const bar = $(".bar", el);
        const fill = $(".bar > span", el);
        const indeterminate = !finished && s.percent == null;
        bar.classList.toggle("indeterminate", indeterminate);
        bar.hidden = s.status === "error" || s.status === "cancelled";
        fill.style.width = `${finished ? 100 : (s.percent || 0)}%`;

        const actions = $(".job-actions", el);
        if (finished) {
          actions.replaceChildren();
          if (s.status === "done" && s.filepath) {
            const reveal = document.createElement("button");
            reveal.type = "button";
            reveal.className = "btn btn-accent btn-sm";
            reveal.textContent = REVEAL_LABEL;
            reveal.addEventListener("click", async () => {
              try {
                await api("/api/reveal", { method: "POST", body: JSON.stringify({ path: s.filepath }) });
              } catch (err) { showNotice(err.message); }
            });
            actions.append(reveal);
            let file = $(".job-file", el);
            if (!file) { file = document.createElement("div"); file.className = "job-file"; el.append(file); }
            file.textContent = s.filename;
          }
        }
      },
    };
    cancelBtn.addEventListener("click", async () => {
      if (!job.taskId) return;
      cancelBtn.disabled = true;
      try { await api(`/api/cancel/${job.taskId}`, { method: "POST" }); }
      catch (err) { showNotice(err.message); cancelBtn.disabled = false; }
    });
    return job;
  }

  // ------------------------------------------------------------------ save folder
  function showFolder(info) {
    els.folderPath.textContent = info.download_dir_display;
    els.folderPath.title = info.download_dir;
    els.folderReset.hidden = info.is_default;
  }

  els.folderChange.addEventListener("click", async () => {
    els.folderChange.disabled = true;
    try {
      let info;
      try {
        info = await api("/api/download-dir/choose", { method: "POST" });
      } catch (err) {
        // No native window (running in a browser via --server): ask for a path instead.
        if (!/native window/i.test(err.message)) throw err;
        const typed = window.prompt("Folder to save downloads to:", els.folderPath.title);
        if (!typed) return;
        info = await api("/api/download-dir", { method: "POST", body: JSON.stringify({ path: typed }) });
      }
      showFolder(info);
      if (!info.cancelled) showNotice(`New downloads will be saved to ${info.download_dir_display}`, "info");
    } catch (err) {
      showNotice(err.message);
    } finally {
      els.folderChange.disabled = false;
    }
  });

  els.folderReset.addEventListener("click", async () => {
    try {
      showFolder(await api("/api/download-dir", { method: "POST", body: JSON.stringify({ path: null }) }));
      showNotice("New downloads will be saved to your Downloads folder.", "info");
    } catch (err) { showNotice(err.message); }
  });

  // ------------------------------------------------------------------ theme
  function applyTheme(theme) {
    const dark = theme === "dark";
    if (dark) document.documentElement.dataset.theme = "dark";
    else delete document.documentElement.dataset.theme;
    const label = dark ? "Switch to light mode" : "Switch to dark mode";
    els.themeBtn.setAttribute("aria-label", label);
    els.themeBtn.title = label;
  }
  els.themeBtn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("mbr-theme", next); } catch { /* storage unavailable */ }
    applyTheme(next);
  });
  applyTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");

  // ------------------------------------------------------------------ yt-dlp updater
  function setUpdater(dotClass, text) {
    els.updateDot.className = `status-dot ${dotClass}`;
    els.version.textContent = text;
  }

  async function loadHealth() {
    try {
      const h = await api("/api/health");
      setUpdater("ok", `yt-dlp ${h.ytdlp_version}`);
      showFolder(h);
      els.updateDot.title = h.ffmpeg ? "Ready" : "ffmpeg not found";
      if (!h.ffmpeg) {
        setUpdater("err", `yt-dlp ${h.ytdlp_version}`);
        showNotice("ffmpeg wasn't found, so downloads can't be converted. Run setup_bins.sh and restart the app.");
      }
    } catch (err) {
      setUpdater("err", "offline");
    }
  }

  els.updateBtn.addEventListener("click", async () => {
    els.updateBtn.disabled = true;
    els.updateBtn.textContent = "Updating…";
    setUpdater("busy", els.version.textContent);
    try {
      const r = await api("/api/update", { method: "POST" });
      if (r.updated) {
        setUpdater("ok", `yt-dlp ${r.version}`);
        showNotice(`yt-dlp updated ${r.previous_version} → ${r.version}. Restart Man Bun Rip to use the new version.`, "info");
      } else {
        setUpdater("ok", `yt-dlp ${r.version}`);
        showNotice(`yt-dlp is already up to date (${r.version}).`, "info");
      }
    } catch (err) {
      setUpdater("err", els.version.textContent);
      showNotice(`Update failed: ${err.message}`);
    } finally {
      els.updateBtn.disabled = false;
      els.updateBtn.textContent = "Update yt-dlp";
    }
  });

  loadHealth();
  els.input.focus();
  const initialQuery = new URLSearchParams(location.search).get("q");
  if (initialQuery) { els.input.value = initialQuery; runSearch(initialQuery); }
})();
