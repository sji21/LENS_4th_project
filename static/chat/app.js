"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("chat-form");
  const input = $("question");
  let conversationId = null;
  let busy = false;
  let externalBusy = false;
  let selectedDelete = null;
  let readinessTimer = null;
  let syncTimer = null;
  let lastRendered = "";
  const csrf = form.querySelector('[name="csrfmiddlewaretoken"]').value;

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function notice(message, reload = false) {
    const box = $("notice");
    box.replaceChildren();
    box.hidden = !message;
    if (message) box.append(document.createTextNode(message));
    if (reload) {
      const link = element("a", "새로고침"); link.href = window.location.pathname; box.append(link);
    }
  }
  function controls() {
    const locked = busy || externalBusy || !conversationId;
    $("send").disabled = locked || !input.value.trim();
    for (const id of ["attach", "new-chat", "upload-suggestion", "document-select"]) $(id).disabled = locked;
    document.querySelectorAll("[data-question], .document-card button").forEach((b) => { b.disabled = locked; });
    form.setAttribute("aria-busy", String(busy));
  }
  async function request(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin", ...options,
      headers: { "X-CSRFToken": csrf, ...options.headers },
    });
    let data;
    try { data = await response.json(); } catch { throw new Error("서버 응답을 확인할 수 없습니다. 잠시 후 새로고침해 주세요."); }
    if (!response.ok) {
      const error = new Error(data.error || "요청을 처리하지 못했습니다.");
      error.status = response.status; throw error;
    }
    return data;
  }
  function post(url, body = {}) {
    return request(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      ...body, conversation_id: conversationId, request_id: crypto.randomUUID(),
    }) });
  }
  function safeLink(url, label) {
    try {
      const parsed = new URL(url);
      if (!["https:", "http:"].includes(parsed.protocol) || parsed.username || parsed.password) return element("span", label);
      const link = element("a", label); link.href = parsed.href;
      link.target = "_blank"; link.rel = "noopener noreferrer"; return link;
    } catch { return element("span", label); }
  }
  function answerText(text) {
    const body = element("div", undefined, "answer-body");
    // A deliberately small text-only Markdown subset; never inject model HTML.
    const parts = String(text).split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g);
    for (const part of parts) {
      if (part.startsWith("**") && part.endsWith("**")) body.append(element("strong", part.slice(2, -2)));
      else if (part.startsWith("`") && part.endsWith("`")) body.append(element("code", part.slice(1, -1)));
      else body.append(document.createTextNode(part));
    }
    return body;
  }
  function renderMessage(message) {
    const row = element("article", undefined, `message ${message.role}`);
    row.setAttribute("aria-label", message.role === "user" ? "내 질문" : "LENS 답변");
    if (message.role !== "user") row.append(element("span", "L", "avatar"));
    const bubble = element("div", undefined, "bubble");
    bubble.append(answerText(message.content));
    if (message.role === "assistant") {
      const meta = element("div", undefined, "message-meta");
      const labels = { answered: "근거 확인 답변", abstained: "답변 보류", refused: "범위 안내" };
      meta.append(element("span", labels[message.status] || "안내", `answer-status ${message.status || ""}`));
      if (typeof message.elapsed_seconds === "number") meta.append(element("span", `${message.elapsed_seconds}초`));
      if (message.used_history) meta.append(element("span", "이전 대화 반영"));
      bubble.append(meta);
      if (message.sources?.length) {
        const sources = element("details", undefined, "sources");
        sources.append(element("summary", `참고 근거 ${message.sources.length}건`));
        const list = element("ul");
        const types = { uploaded_document: "첨부 문서", law: "법령", decree: "시행령", case: "판례", guide: "기관 안내" };
        for (const source of message.sources) {
          const li = element("li"); li.append(document.createTextNode(`${types[source.doc_type] || "근거"} · `));
          li.append(safeLink(source.url, source.label)); list.append(li);
        }
        sources.append(list); bubble.append(sources);
      }
    }
    row.append(bubble); return row;
  }
  function renderDocuments(documents) {
    const box = $("documents"); box.replaceChildren();
    const select = $("document-select"); const previous = select.value;
    select.replaceChildren(new Option("문서 자동 선택", ""));
    $("document-count").textContent = documents.length;
    if (!documents.length) box.append(element("p", "계약서나 등기 문서를 첨부하면 문서 내용을 함께 확인할 수 있어요.", "empty-documents"));
    for (const doc of documents) {
      select.add(new Option(doc.filename, doc.document_id));
      const card = element("details", undefined, "document-card");
      const summary = element("summary");
      summary.append(element("span", doc.filename, "doc-name"), element("span", `${doc.label} · ${doc.page_count}쪽`, "doc-meta"));
      card.append(summary);
      const analysis = doc.analysis || {};
      card.append(element("p", analysis.headline || "분석 완료"), element("p", analysis.summary || ""));
      const checks = [...(analysis.signals || []), ...(analysis.fields || []), ...(analysis.clauses || [])];
      if (checks.length) {
        const list = element("ul");
        for (const check of checks) {
          const li = element("li");
          li.append(element("strong", `${check.title}${check.page_number ? ` (${check.page_number}쪽)` : ""}`));
          const statuses = { confirmed: "문구 탐지", review: "원본 확인 필요", not_found: "문구 미탐지", included: "관련 특약 탐지", recommended: "협의 권장", high: "우선 확인", caution: "주의 확인", info: "참고" };
          const status = statuses[check.status || check.severity];
          if (status) li.append(element("p", status, "doc-meta"));
          for (const text of [check.guidance, check.reason, check.recommendation, ...(check.checks || [])]) {
            if (text) li.append(element("p", text));
          }
          for (const source of check.sources || []) li.append(safeLink(source.url, source.title));
          list.append(li);
        }
        card.append(list);
      }
      for (const warning of analysis.extraction?.warnings || []) card.append(element("p", warning));
      for (const check of analysis.common_checks || []) card.append(element("p", check));
      if (analysis.disclaimer) card.append(element("p", analysis.disclaimer, "doc-meta"));
      const remove = element("button", "문서 삭제"); remove.type = "button";
      remove.addEventListener("click", () => { selectedDelete = doc.document_id; $("delete-dialog").showModal(); });
      card.append(remove); box.append(card);
    }
    if (documents.some((doc) => doc.document_id === previous)) select.value = previous;
  }
  function render(data) {
    conversationId = data.conversation_id;
    externalBusy = Boolean(data.busy);
    const signature = JSON.stringify([data.messages, data.documents]);
    if (signature !== lastRendered) {
      $("messages").replaceChildren(...data.messages.map(renderMessage));
      $("welcome").hidden = data.messages.length > 0;
      renderDocuments(data.documents);
      lastRendered = signature;
      scrollBottom();
    }
    controls();
    if (externalBusy) {
      notice("이 대화의 다른 요청을 처리하고 있어요. 완료되면 화면을 갱신합니다.");
      clearTimeout(syncTimer); syncTimer = setTimeout(syncState, 2500);
    }
  }
  function scrollBottom() {
    const box = $("conversation-scroll"); box.scrollTop = box.scrollHeight;
  }
  async function syncState() {
    try { const wasBusy = externalBusy; render(await request(form.dataset.stateUrl)); if (wasBusy && !externalBusy) notice(""); }
    catch (error) { notice(error.message, true); }
  }
  async function action(label, operation) {
    if (busy || externalBusy || !conversationId) return;
    busy = true; controls(); notice("");
    $("pending").hidden = false; $("pending-text").textContent = label;
    const start = Date.now(); $("elapsed").textContent = "";
    const timer = setInterval(() => { $("elapsed").textContent = `${Math.floor((Date.now() - start) / 1000)}초`; }, 1000);
    try { await operation(); }
    catch (error) {
      notice(error.message, [403, 409, 410].includes(error.status) || !error.status);
      // A lost response may still have committed: refresh authoritative server history.
      await syncState();
    } finally {
      clearInterval(timer); busy = false; $("pending").hidden = true; controls();
    }
  }
  input.addEventListener("input", controls);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault(); if (!$("send").disabled) form.requestSubmit();
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault(); const question = input.value.trim();
    if (!question) return;
    action("근거를 확인하고 답변을 준비하고 있어요", async () => {
      $("welcome").hidden = true;
      $("messages").append(renderMessage({ role: "user", content: question })); scrollBottom();
      lastRendered = "";
      const data = await post(form.dataset.sendUrl, { message: question, document_id: $("document-select").value || null });
      // Preserve text the user typed while waiting.
      if (input.value.trim() === question) input.value = "";
      render(data); input.focus();
    });
  });
  document.querySelectorAll("[data-question]").forEach((button) => button.addEventListener("click", () => {
    input.value = button.dataset.question; controls(); input.focus();
  }));
  for (const id of ["attach", "upload-suggestion"]) $(id).addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", () => {
    const files = [...$("file-input").files]; $("file-input").value = "";
    if (!files.length) return;
    if (files.length > 5) { notice("한 번에 최대 5개까지 선택해 주세요."); return; }
    action("첨부 문서를 읽고 있어요", async () => {
      const progress = $("upload-progress"); progress.replaceChildren();
      for (const file of files) {
        const row = element("p", `${file.name} · 대기 중`); progress.append(row);
        if (file.size > 20 * 1024 * 1024) { row.textContent = `${file.name} · 실패: 20MB 이하만 가능합니다.`; continue; }
        row.textContent = `${file.name} · 분석 중`;
        const body = new FormData(); body.append("file", file); body.append("conversation_id", conversationId); body.append("request_id", crypto.randomUUID());
        try {
          const data = await request(form.dataset.uploadUrl, { method: "POST", body });
          render(data); row.textContent = `${file.name} · ${data.notice}`;
        } catch (error) { row.textContent = `${file.name} · 실패: ${error.message}`; }
      }
      await syncState();
    });
  });
  $("new-chat").addEventListener("click", () => $("reset-dialog").showModal());
  $("cancel-reset").addEventListener("click", () => $("reset-dialog").close());
  $("confirm-reset").addEventListener("click", () => {
    $("reset-dialog").close(); action("새 대화를 준비하고 있어요", async () => {
      render(await post(form.dataset.resetUrl)); input.value = ""; $("upload-progress").replaceChildren(); input.focus();
    });
  });
  $("cancel-delete").addEventListener("click", () => $("delete-dialog").close());
  $("confirm-delete").addEventListener("click", () => {
    $("delete-dialog").close(); action("문서를 삭제하고 있어요", async () => {
      render(await post(`${form.dataset.uploadUrl}${encodeURIComponent(selectedDelete)}/delete/`));
    });
  });
  async function checkReadiness() {
    try {
      const data = await request(form.dataset.readinessUrl);
      const labels = { idle: "검색 준비 대기", loading: "검색 모델 준비 중", ready: "검색 준비 완료", failed: "검색 준비 실패" };
      $("readiness-text").textContent = labels[data.state];
      $("ready-dot").className = `status-dot ${data.state}`;
      $("retry-ready").hidden = data.state !== "failed";
      if (["idle", "loading"].includes(data.state)) readinessTimer = setTimeout(checkReadiness, 2000);
    } catch {
      $("readiness-text").textContent = "연결 상태를 확인해 주세요";
      $("retry-ready").hidden = false;
    }
  }
  $("retry-ready").addEventListener("click", async () => {
    $("retry-ready").disabled = true;
    try { await post(form.dataset.retryUrl); clearTimeout(readinessTimer); await checkReadiness(); }
    catch (error) { notice(error.message, true); }
    finally { $("retry-ready").disabled = false; }
  });
  window.addEventListener("pagehide", () => { clearTimeout(readinessTimer); clearTimeout(syncTimer); });
  syncState().then(() => { if (conversationId) checkReadiness(); });
})();
