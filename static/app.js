/* gh-review-tool frontend */

const $ = (sel) => document.querySelector(sel);
const $main = () => $("#main-content");

let state = { repos: [], currentRepo: null, currentPR: null };

// --- API helpers ---

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`/api${path}`, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Request failed");
  }
  return res.json();
}

function toast(msg, type = "success") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// --- Repo sidebar ---

async function loadRepos() {
  state.repos = await api("GET", "/repos");
  renderRepoList();
}

function renderRepoList() {
  const el = $("#repo-list");
  if (!state.repos.length) {
    el.innerHTML = `<div style="padding:16px;color:var(--text-muted);font-size:12px;">No repositories added yet.</div>`;
    return;
  }
  el.innerHTML = state.repos.map((r) => `
    <div class="repo-item ${state.currentRepo?.full_name === r.full_name ? 'active' : ''}" data-repo="${r.full_name}">
      <div>
        <div class="name">${r.name}</div>
        <div class="owner">${r.owner}</div>
      </div>
      <button class="remove-btn" data-remove="${r.full_name}" title="Remove">&times;</button>
    </div>
  `).join("");

  el.querySelectorAll(".repo-item").forEach((item) => {
    item.addEventListener("click", (e) => {
      if (e.target.closest(".remove-btn")) return;
      selectRepo(item.dataset.repo);
    });
  });

  el.querySelectorAll(".remove-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await api("DELETE", "/repos", { full_name: btn.dataset.remove });
      if (state.currentRepo?.full_name === btn.dataset.remove) {
        state.currentRepo = null;
        showEmpty();
      }
      await loadRepos();
    });
  });
}

// --- Repo search / add ---

$("#repo-search-btn").addEventListener("click", searchOrAddRepo);
$("#repo-search").addEventListener("keydown", (e) => { if (e.key === "Enter") searchOrAddRepo(); });

async function searchOrAddRepo() {
  let val = $("#repo-search").value.trim();
  if (!val) return;

  // Handle full GitHub URLs: https://github.com/Owner/Repo/...
  const urlMatch = val.match(/github\.com\/([^/]+)\/([^/]+)/);
  if (urlMatch) {
    val = `${urlMatch[1]}/${urlMatch[2]}`;
  }

  // If it looks like owner/repo, add directly
  if (val.includes("/")) {
    const [owner, name] = val.split("/", 2);
    if (owner && name) {
      await api("POST", "/repos", { owner, name });
      $("#repo-search").value = "";
      $("#search-results").style.display = "none";
      await loadRepos();
      selectRepo(`${owner}/${name}`);
      return;
    }
  }

  // Otherwise search in TotalEnergiesCode org
  const resultsEl = $("#search-results");
  resultsEl.style.display = "block";
  resultsEl.innerHTML = `<div class="loading"><div class="spinner"></div>Searching...</div>`;

  try {
    const repos = await api("GET", `/repos/search?org=TotalEnergiesCode&q=${encodeURIComponent(val)}`);
    if (!repos.length) {
      resultsEl.innerHTML = `<div style="padding:10px;color:var(--text-muted);font-size:12px;">No repos found.</div>`;
      return;
    }
    resultsEl.innerHTML = repos.slice(0, 10).map((r) => `
      <div class="search-result-item" data-owner="${r.owner?.login || 'TotalEnergiesCode'}" data-name="${r.name}">
        <span>${r.name}</span>
        <span class="desc">${r.description || ""}</span>
      </div>
    `).join("");
    resultsEl.querySelectorAll(".search-result-item").forEach((item) => {
      item.addEventListener("click", async () => {
        await api("POST", "/repos", { owner: item.dataset.owner, name: item.dataset.name });
        $("#repo-search").value = "";
        resultsEl.style.display = "none";
        await loadRepos();
        selectRepo(`${item.dataset.owner}/${item.dataset.name}`);
      });
    });
  } catch (e) {
    resultsEl.innerHTML = `<div style="padding:10px;color:var(--p0);font-size:12px;">${e.message}</div>`;
  }
}

// --- Repo selected -> show PRs ---

async function selectRepo(fullName) {
  const [owner, name] = fullName.split("/", 2);
  state.currentRepo = { owner, name, full_name: fullName };
  state.currentPR = null;
  renderRepoList();
  updateBreadcrumb();
  await showPRs();
}

async function showPRs(forceRefresh = false) {
  const { owner, name, full_name } = state.currentRepo;
  $main().innerHTML = `
    <div class="pr-list-header">
      <h2>Pull Requests &mdash; ${full_name}</h2>
      <button class="btn btn-accent" id="refresh-prs">Refresh</button>
    </div>
    <div id="pr-list-content"><div class="loading"><div class="spinner"></div>Loading PRs...</div></div>
  `;

  $("#refresh-prs").addEventListener("click", () => showPRs(true));

  try {
    let prs;
    if (forceRefresh) {
      prs = await api("POST", `/prs/${owner}/${name}/refresh`);
      toast("PRs refreshed");
    } else {
      prs = await api("GET", `/prs/${owner}/${name}`);
      if (!prs.length) {
        // Auto-refresh if cache empty
        prs = await api("POST", `/prs/${owner}/${name}/refresh`);
      }
    }
    renderPRList(prs);
  } catch (e) {
    $("#pr-list-content").innerHTML = `<div style="color:var(--p0);">${e.message}</div>`;
  }
}

function renderPRList(prs) {
  const el = $("#pr-list-content");
  if (!prs.length) {
    el.innerHTML = `<div class="empty-state" style="height:auto;padding:40px;"><p>No open pull requests.</p></div>`;
    return;
  }
  el.innerHTML = prs.map((pr) => `
    <div class="pr-item" data-pr="${pr.number}">
      <div class="pr-title"><span class="pr-number">#${pr.number}</span> ${pr.title}</div>
      <div class="pr-meta">
        <span>${pr.author?.login || "unknown"}</span>
        <span>${pr.headRefName} -> ${pr.baseRefName}</span>
        <span>+${pr.additions || 0} -${pr.deletions || 0}</span>
        <span>${pr.updatedAt ? new Date(pr.updatedAt).toLocaleDateString() : ""}</span>
      </div>
    </div>
  `).join("");

  el.querySelectorAll(".pr-item").forEach((item) => {
    item.addEventListener("click", () => openPR(parseInt(item.dataset.pr), prs.find((p) => p.number === parseInt(item.dataset.pr))));
  });
}

// --- PR detail view ---

function openPR(prNumber, prData) {
  state.currentPR = { number: prNumber, ...prData };
  updateBreadcrumb();
  renderPRDetail("review");
}

function renderPRDetail(activeTab) {
  const pr = state.currentPR;
  const { owner, name, full_name } = state.currentRepo;

  $main().innerHTML = `
    <div class="pr-detail-header">
      <h2><span class="pr-number">#${pr.number}</span> ${pr.title || ""}</h2>
      <div class="meta">${pr.author?.login || ""} &middot; ${pr.headRefName || ""} -> ${pr.baseRefName || ""}</div>
    </div>
    <div class="tabs">
      <button class="tab ${activeTab === 'review' ? 'active' : ''}" data-tab="review">Review</button>
      <button class="tab ${activeTab === 'comments' ? 'active' : ''}" data-tab="comments">Comments</button>
    </div>
    <div id="tab-content"></div>
  `;

  $main().querySelectorAll(".tab").forEach((t) => {
    t.addEventListener("click", () => renderPRDetail(t.dataset.tab));
  });

  if (activeTab === "review") renderReviewTab();
  else renderCommentsTab();
}

// --- Review tab ---

async function renderReviewTab() {
  const { owner, name, full_name } = state.currentRepo;
  const pr = state.currentPR;
  const el = $("#tab-content");

  el.innerHTML = `
    <div class="review-actions">
      <button class="btn btn-accent" id="run-review">Review PR</button>
      <button class="btn" id="rerun-review">Re-run Review</button>
      <button class="btn btn-p0 btn-small" id="clear-cache">Clear Cache</button>
    </div>
    <div id="review-results"></div>
  `;

  $("#run-review").addEventListener("click", () => runReview(false));
  $("#rerun-review").addEventListener("click", () => runReview(true));
  $("#clear-cache").addEventListener("click", async () => {
    await api("DELETE", `/review/${owner}/${name}/${pr.number}/cache`);
    $("#review-results").innerHTML = "";
    toast("Review cache cleared");
  });

  // Try loading cached review
  try {
    const cached = await api("POST", `/review/${owner}/${name}/${pr.number}`);
    if (cached && (cached.findings?.length || cached.summary)) {
      renderReviewResults(cached);
    }
  } catch (e) { /* no cached review */ }
}

async function runReview(rerun) {
  const { owner, name } = state.currentRepo;
  const pr = state.currentPR;
  const resultsEl = $("#review-results");

  // If not rerun and not streaming, try cached first via POST
  if (!rerun) {
    try {
      const cached = await api("POST", `/review/${owner}/${name}/${pr.number}`);
      if (cached && (cached.findings?.length || cached.summary)) {
        renderReviewResults(cached);
        return;
      }
    } catch (e) { /* no cache, proceed to stream */ }
  }

  // Clear cache if rerun
  if (rerun) {
    try { await api("DELETE", `/review/${owner}/${name}/${pr.number}/cache`); } catch (e) {}
  }

  // Stream the review via SSE
  resultsEl.innerHTML = `
    <div class="loading"><div class="spinner"></div>Running opencode review...</div>
    <div id="stream-output" style="margin-top:12px;padding:12px;background:var(--bg-secondary);border:1px solid var(--border);border-radius:6px;font-size:11px;color:var(--text-secondary);white-space:pre-wrap;max-height:500px;overflow-y:auto;font-family:var(--font-mono);"></div>
  `;

  const streamEl = $("#stream-output");
  const evtSource = new EventSource(`/api/review/${owner}/${name}/${pr.number}/stream`);

  evtSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);

      if (data.type === "chunk") {
        streamEl.textContent += data.text;
        // Auto-scroll to bottom
        streamEl.scrollTop = streamEl.scrollHeight;
      } else if (data.type === "result") {
        console.log("[gh-review-tool] Review result:", JSON.stringify(data.review, null, 2));
        evtSource.close();
        renderReviewResults(data.review);
      } else if (data.type === "done") {
        evtSource.close();
      }
    } catch (e) {
      console.error("[gh-review-tool] SSE parse error:", e, event.data);
    }
  };

  evtSource.onerror = (e) => {
    console.error("[gh-review-tool] SSE error:", e);
    evtSource.close();
    // If stream-output has content, try to parse it as a review
    const output = streamEl.textContent.trim();
    if (output) {
      resultsEl.innerHTML = "";
      // Show what we got
      renderReviewResults({
        summary: "Stream ended. Raw output shown below.",
        raw_output: output,
        raw_length: output.length,
        findings: [],
      });
    } else {
      resultsEl.innerHTML = `<div style="color:var(--p0);">Review stream failed. Check server logs.</div>`;
    }
  };
}

function renderReviewResults(review) {
  const el = $("#review-results");
  const { owner, name, full_name } = state.currentRepo;
  const pr = state.currentPR;

  let html = "";

  if (review.summary) {
    html += `<div style="margin-bottom:20px;padding:12px;background:var(--bg-secondary);border:1px solid var(--border);border-radius:6px;font-size:12px;color:var(--text-secondary);">${review.summary}</div>`;
  }

  if (review.raw_output) {
    html += `<details open style="margin-bottom:20px;">
      <summary style="cursor:pointer;color:var(--accent);font-size:12px;margin-bottom:8px;">Debug: Raw opencode output (${review.raw_length || review.raw_output.length} chars)</summary>
      <div style="padding:12px;background:var(--bg-secondary);border:1px solid var(--border);border-radius:6px;font-size:11px;color:var(--text-muted);white-space:pre-wrap;max-height:400px;overflow-y:auto;">${escapeHtml(review.raw_output)}</div>
    </details>`;
  }

  if (review.findings?.length) {
    html += review.findings.map((f, i) => {
      const crit = (f.criticality || "P3").toLowerCase();
      return `
        <div class="finding ${crit}">
          <div class="finding-header">
            <div class="left">
              <span class="badge badge-${crit}">${f.criticality || "P3"}</span>
              <span class="title">${escapeHtml(f.title)}</span>
            </div>
            <button class="btn btn-small btn-${crit}" data-finding="${i}">Publish to PR</button>
          </div>
          <div class="description">${escapeHtml(f.description)}</div>
          ${f.file ? `<div class="file-ref">${escapeHtml(f.file)}${f.line ? `:${f.line}` : ""}</div>` : ""}
          ${f.suggestion ? `<div class="suggestion">${escapeHtml(f.suggestion)}</div>` : ""}
        </div>
      `;
    }).join("");
  } else if (!review.raw_output) {
    html += `<div style="color:var(--success);padding:20px;">No issues found.</div>`;
  }

  el.innerHTML = html;

  // Bind publish buttons
  el.querySelectorAll("[data-finding]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const f = review.findings[parseInt(btn.dataset.finding)];
      const severityEmoji = { P0: "\u{1F534}", P1: "\u{1F7E0}", P2: "\u{1F7E1}", P3: "\u{1F535}" };
      const severityLabel = { P0: "Critical", P1: "Major", P2: "Minor", P3: "Suggestion" };
      const emoji = severityEmoji[f.criticality] || "\u{1F535}";
      const label = severityLabel[f.criticality] || "Info";

      let body = `## ${emoji} ${f.criticality} — ${f.title}\n\n`;
      body += `| | |\n|---|---|\n| **Severity** | ${emoji} ${f.criticality} (${label}) |\n`;
      if (f.file) {
        body += `| **File** | \`${f.file}${f.line ? ':' + f.line : ''}\` |\n`;
      }
      body += `\n---\n\n`;
      body += `### Description\n\n${f.description}\n\n`;
      if (f.suggestion) {
        body += `<details>\n<summary><strong>\u{1F4A1} Suggested Fix</strong></summary>\n\n${f.suggestion}\n\n</details>\n\n`;
      }
      body += `---\n<sub>\u{1F916} Automated review by <strong>OpenCode</strong> — <a href="https://opencode.ai">opencode.ai</a></sub>`;

      btn.disabled = true;
      btn.textContent = "Publishing...";
      try {
        if (f.file && f.line) {
          // Post as inline review comment on the specific file/line
          await api("POST", "/comment/inline", {
            repo: full_name,
            pr_number: pr.number,
            body,
            path: f.file,
            line: f.line,
          });
          btn.textContent = "Published (inline)";
        } else {
          await api("POST", "/comment/publish", { repo: full_name, pr_number: pr.number, body });
          btn.textContent = "Published";
        }
        btn.style.borderColor = "var(--success)";
        btn.style.color = "var(--success)";
        toast("Comment published to PR");
      } catch (e) {
        btn.textContent = "Failed";
        btn.disabled = false;
        toast(e.message, "error");
      }
    });
  });
}

// --- Comments tab ---

async function renderCommentsTab() {
  const { owner, name } = state.currentRepo;
  const pr = state.currentPR;
  const el = $("#tab-content");

  el.innerHTML = `
    <div class="review-actions">
      <button class="btn btn-accent" id="analyze-comments">Analyze Comments</button>
    </div>
    <div id="comments-content"><div class="loading"><div class="spinner"></div>Loading comments...</div></div>
  `;

  $("#analyze-comments").addEventListener("click", analyzeComments);

  // Load comments
  try {
    const data = await api("GET", `/pr/${owner}/${name}/${pr.number}`);
    const comments = [...(data.comments || [])];
    (data.reviews || []).forEach((r) => { if (r.body) comments.push(r); });
    // Include inline review comments (code-level)
    (data.review_comments || []).forEach((c) => {
      c._inline = true;  // flag to show file/line in UI
      comments.push(c);
    });

    if (!comments.length) {
      $("#comments-content").innerHTML = `<div class="empty-state" style="height:auto;padding:40px;"><p>No comments on this PR.</p></div>`;
      return;
    }

    state.currentPR._comments = comments;
    renderCommentsList(comments, null);
  } catch (e) {
    $("#comments-content").innerHTML = `<div style="color:var(--p0);">${e.message}</div>`;
  }
}

function renderCommentsList(comments, analysis) {
  const el = $("#comments-content");
  const { full_name } = state.currentRepo;
  const pr = state.currentPR;

  // Store for filtering
  state.currentPR._commentsData = comments;
  state.currentPR._analysisData = analysis;

  // --- Build filter bar ---
  const authors = [...new Set(comments.map((c) => c.author?.login || "unknown"))].sort();
  const hasAnalysis = analysis && analysis.length > 0;
  const crits = hasAnalysis
    ? [...new Set(analysis.map((a) => a?.criticality || "Unrated"))].sort()
    : [];

  const authorFilter = state.currentPR._authorFilter || "";
  const critFilter = state.currentPR._critFilter || "";

  let filterHtml = `<div class="filter-bar">
    <label>Author:
      <select id="filter-author">
        <option value="">All authors</option>
        ${authors.map((a) => `<option value="${a}" ${authorFilter === a ? "selected" : ""}>${a}</option>`).join("")}
      </select>
    </label>
    <label>Severity:
      <select id="filter-crit" ${!hasAnalysis ? 'disabled title="Run Analyze first"' : ""}>
        <option value="">All severities</option>
        ${crits.map((c) => `<option value="${c}" ${critFilter === c ? "selected" : ""}>${c}</option>`).join("")}
      </select>
    </label>
  </div>`;

  // --- Apply filters ---
  let filtered = comments.map((c, i) => ({ comment: c, analysis: analysis?.[i] || null, idx: i }));
  if (authorFilter) {
    filtered = filtered.filter((r) => (r.comment.author?.login || "unknown") === authorFilter);
  }
  if (critFilter) {
    filtered = filtered.filter((r) => (r.analysis?.criticality || "Unrated") === critFilter);
  }

  // --- Render ---
  let cardsHtml = filtered.map((r) => {
    const c = r.comment;
    const a = r.analysis;
    const crit = a ? a.criticality?.toLowerCase() : null;
    return `
      <div class="comment-card" id="comment-card-${r.idx}">
        <div class="comment-header">
          <span class="author">${c.author?.login || "unknown"}</span>
          ${c.path ? `<span class="file-ref" style="font-size:11px;color:var(--text-muted);">${escapeHtml(c.path)}${c.line ? ':' + c.line : ''}</span>` : ""}
          ${a ? `<span class="badge badge-${crit}">${a.criticality}</span>` : ""}
        </div>
        <div class="body">${escapeHtml(c.body || "")}</div>
        ${a ? `
          <div class="comment-analysis">
            <div class="analysis-row">
              <span class="label">Valid:</span> <span class="value">${a.valid ? "Yes" : "No"}</span>
              <span class="label">Interest:</span> <span class="value">${a.interest || "-"}</span>
            </div>
            <div class="summary">${escapeHtml(a.summary || "")}</div>
          </div>
        ` : ""}
        <div class="comment-actions">
          <button class="btn btn-small btn-accent" data-fix="${r.idx}">Fix & Submit PR</button>
        </div>
      </div>
    `;
  }).join("");

  if (!filtered.length) {
    cardsHtml = `<div class="empty-state" style="height:auto;padding:40px;"><p>No comments match the current filters.</p></div>`;
  }

  el.innerHTML = filterHtml + cardsHtml;

  // --- Bind filter events ---
  $("#filter-author").addEventListener("change", (e) => {
    state.currentPR._authorFilter = e.target.value;
    renderCommentsList(state.currentPR._commentsData, state.currentPR._analysisData);
  });
  if (hasAnalysis) {
    $("#filter-crit").addEventListener("change", (e) => {
      state.currentPR._critFilter = e.target.value;
      renderCommentsList(state.currentPR._commentsData, state.currentPR._analysisData);
    });
  }

  // --- Bind suggest fix buttons ---
  el.querySelectorAll("[data-fix]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.fix);
      const c = comments[idx];
      streamFix(btn, idx, c);
    });
  });
}

async function streamFix(btn, idx, comment) {
  const { full_name } = state.currentRepo;
  const pr = state.currentPR;

  btn.disabled = true;
  btn.textContent = "Cloning & fixing...";

  // Create streaming output area below the comment card
  const card = $(`#comment-card-${idx}`);
  // Remove any previous output
  card.querySelectorAll(".fix-stream-output, .fix-result-bar").forEach((el) => el.remove());

  const outputDiv = document.createElement("div");
  outputDiv.className = "fix-stream-output";
  card.appendChild(outputDiv);

  function appendStatus(text) {
    const line = document.createElement("div");
    line.className = "fix-status-line";
    line.textContent = `> ${text}`;
    outputDiv.appendChild(line);
    outputDiv.scrollTop = outputDiv.scrollHeight;
  }

  try {
    const res = await fetch("/api/comment/fix", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo: full_name, pr_number: pr.number, comment_body: comment.body }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Request failed");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let resultData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const data = JSON.parse(line.slice(6));
          if (data.type === "status") {
            appendStatus(data.text);
            btn.textContent = data.text.slice(0, 30) + (data.text.length > 30 ? "..." : "");
          } else if (data.type === "chunk") {
            outputDiv.textContent += data.text;
            outputDiv.scrollTop = outputDiv.scrollHeight;
          } else if (data.type === "error") {
            appendStatus(`ERROR: ${data.text}`);
            btn.textContent = "Failed";
            btn.style.color = "var(--p0)";
          } else if (data.type === "result") {
            resultData = data;
          } else if (data.type === "done") {
            // finished
          }
        } catch (e) {
          // skip malformed events
        }
      }
    }

    if (resultData && resultData.has_changes) {
      btn.textContent = "Changes ready";
      btn.style.color = "var(--success)";
      toast("Fix applied locally — review the changes");

      // Add result bar with clone path and diff
      const resultBar = document.createElement("div");
      resultBar.className = "fix-result-bar";
      resultBar.innerHTML = `
        <span class="fix-label">Worktree:</span>
        <code class="fix-worktree-path">${escapeHtml(resultData.worktree_path)}</code>
        <button class="btn btn-small btn-accent show-diff-btn">Show Diff</button>
      `;
      card.appendChild(resultBar);

      // Show diff on click
      resultBar.querySelector(".show-diff-btn").addEventListener("click", () => {
        let diffDiv = card.querySelector(".fix-diff-output");
        if (diffDiv) {
          diffDiv.remove();
          return;
        }
        diffDiv = document.createElement("div");
        diffDiv.className = "fix-diff-output fix-stream-output";
        diffDiv.textContent = resultData.diff || "(no diff available)";
        card.appendChild(diffDiv);
      });

      // Instructions
      const instructions = document.createElement("div");
      instructions.className = "fix-instructions";
      instructions.innerHTML = `
        <span>To review and push:</span>
        <code>cd ${escapeHtml(resultData.worktree_path)} && git diff --cached</code>
        <code>git commit -m "fix: address review comment" && git push</code>
      `;
      card.appendChild(instructions);

    } else if (resultData && !resultData.has_changes) {
      btn.textContent = "No changes made";
      btn.style.color = "var(--text-muted)";
    } else if (btn.textContent !== "Failed") {
      btn.textContent = "Complete";
      btn.style.color = "var(--success)";
    }
  } catch (e) {
    btn.textContent = "Failed";
    btn.disabled = false;
    appendStatus(`ERROR: ${e.message}`);
    toast(e.message, "error");
  }
}

async function analyzeComments() {
  const { owner, name } = state.currentRepo;
  const pr = state.currentPR;
  const el = $("#comments-content");
  // Reset filters on new analysis
  state.currentPR._authorFilter = "";
  state.currentPR._critFilter = "";
  el.innerHTML = `<div class="loading"><div class="spinner"></div>Analyzing comments with opencode... this may take a minute.</div>`;

  try {
    const data = await api("POST", `/comments/${owner}/${name}/${pr.number}/analyze`);
    renderCommentsList(data.comments || [], data.analysis || []);
    toast("Comments analyzed");
  } catch (e) {
    el.innerHTML = `<div style="color:var(--p0);">Analysis failed: ${e.message}</div>`;
  }
}

// --- Navigation ---

function updateBreadcrumb() {
  const parts = [];
  parts.push(`<span onclick="goHome()">Repos</span>`);
  if (state.currentRepo) {
    parts.push(`<span onclick="selectRepo('${state.currentRepo.full_name}')">${state.currentRepo.full_name}</span>`);
  }
  if (state.currentPR) {
    parts.push(`<span>#${state.currentPR.number}</span>`);
  }
  $("#breadcrumb").innerHTML = parts.join(" / ");
}

function showEmpty() {
  updateBreadcrumb();
  $main().innerHTML = `<div class="empty-state"><div class="icon">&lt;/&gt;</div><p>Select a repository from the sidebar or add one to get started.</p></div>`;
}

window.goHome = () => {
  state.currentRepo = null;
  state.currentPR = null;
  renderRepoList();
  showEmpty();
};

window.selectRepo = selectRepo;

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// --- Init ---
loadRepos();
