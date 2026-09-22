"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("chat-form");
  const input = $("question");
  let conversationId = null;
  let busy = false;
  let externalBusy = false;
  let activeChat = null;
  let selectedDelete = null;
  let readinessTimer = null;
  let syncTimer = null;
  let lastRendered = "";
  let replyingTo = null;
  const submission = new ChatRequestIdentity(() => crypto.randomUUID());
  let estimatedSeconds = 30;
  const csrf = form.querySelector('[name="csrfmiddlewaretoken"]').value;

  document.querySelectorAll("[data-room-row]").forEach((row) => {
    const edit = row.querySelector("[data-room-edit]");
    const renameForm = row.querySelector("[data-room-form]");
    const roomLink = row.querySelector(".case-shortcut");
    const cancel = row.querySelector("[data-room-cancel]");
    if (!edit || !renameForm || !roomLink || !cancel) return;
    edit.addEventListener("click", () => {
      roomLink.hidden = true;
      edit.hidden = true;
      renameForm.hidden = false;
      const title = renameForm.querySelector('input[name="title"]');
      title.focus();
      title.select();
    });
    cancel.addEventListener("click", () => {
      renameForm.hidden = true;
      roomLink.hidden = false;
      edit.hidden = false;
    });
  });

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
  function toast(message, type = "success") {
    const stack = $("toast-stack");
    if (!stack) return;
    const item = element("div", undefined, `app-toast ${type}`);
    item.setAttribute("role", type === "error" ? "alert" : "status");
    item.append(element("span", type === "error" ? "!" : "✓", "toast-icon"));
    const copy = element("span", undefined, "toast-copy");
    copy.append(element("strong", type === "error" ? "리포트 생성 실패" : "리포트 생성 완료"));
    copy.append(element("small", message));
    item.append(copy);
    stack.replaceChildren(item);
    window.setTimeout(() => {
      item.classList.add("leaving");
      window.setTimeout(() => item.remove(), 180);
    }, 4200);
  }
  function controls() {
    const locked = busy || externalBusy || !conversationId;
    $("send").disabled = locked || !input.value.trim();
    for (const id of ["attach", "new-chat", "upload-suggestion", "document-select"]) $(id).disabled = locked;
    document.querySelectorAll("[data-question], .document-card button, .conversation-choices button, .text-action").forEach((b) => { b.disabled = locked; });
    form.setAttribute("aria-busy", String(busy));
    const cancel = $("cancel-answer");
    cancel.hidden = !activeChat;
    cancel.disabled = !activeChat || externalBusy;
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
  function post(url, body = {}, requestId = crypto.randomUUID(), options = {}) {
    return request(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      ...body, conversation_id: conversationId, request_id: requestId,
    }), ...options });
  }
  function safeLink(url, label) {
    try {
      const parsed = new URL(url);
      if (!["https:", "http:"].includes(parsed.protocol) || parsed.username || parsed.password) return element("span", label);
      const link = element("a", label); link.href = parsed.href;
      link.target = "_blank"; link.rel = "noopener noreferrer"; return link;
    } catch { return element("span", label); }
  }
  function formatAnswerSections(text) {
    const value = String(text);
    // Only split the explicit procedure labels, preserving all answer wording.
    if (!["언제", "어떻게", "확인할 사항"].every((label) => value.includes(`${label}:`))) return value;
    return value.replace(/[ \t\r\n]*(\*\*)?(언제|어떻게|확인할 사항):/g,
      (match, bold, label, offset) => `${offset ? "\n\n" : ""}${bold || ""}${label}:`);
  }
  function markup(text, target) {
    // A deliberately small text-only Markdown subset; never inject model HTML.
    const parts = String(text).split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g);
    for (const part of parts) {
      if (part.startsWith("**") && part.endsWith("**")) target.append(element("strong", part.slice(2, -2)));
      else if (part.startsWith("`") && part.endsWith("`")) target.append(element("code", part.slice(1, -1)));
      else target.append(document.createTextNode(part));
    }
    return target;
  }
  function annotations(message) {
    // Offsets refer to message.content exactly as rendered. Citations win ties so a
    // glossary term inside a citation never splits the link.
    const spans = [
      ...(message.citations || []).map((s) => ({ ...s, kind: "cite" })),
      ...(message.glossary || []).map((s) => ({ ...s, kind: "glossary-term" })),
    ].sort((a, b) => a.start - b.start || (a.kind === "cite" ? -1 : 1));
    const kept = [];
    let cursor = 0;
    for (const span of spans) {
      if (!Number.isInteger(span.start) || !Number.isInteger(span.end)) continue;
      if (span.start < cursor || span.end <= span.start) continue;
      kept.push(span); cursor = span.end;
    }
    return kept;
  }
  function detailFor(span, label) {
    const box = element("div", undefined, "annotation-detail");
    if (span.kind === "glossary-term") {
      box.append(element("p", span.definition || ""));
      return box;
    }
    box.append(element("span", span.citation || label, "annotation-source"));
    box.append(element("p", span.excerpt || ""));
    if (span.url) { const link = safeLink(span.url, "원문 보기"); link.className = "annotation-link"; box.append(link); }
    if (span.current_url && span.current_url !== span.url) box.append(safeLink(span.current_url, span.doc_type === "case" ? "종합법률정보 사건번호 검색" : "현행 조문 보기 (검색 근거와 판본이 다를 수 있음)"));
    return box;
  }
  function trigger(span, label) {
    // A span, not a button: buttons bring their own line-height and text-align and
    // would disturb the pre-wrap answer body.
    const node = element("span", label, span.kind);
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    node.setAttribute("aria-expanded", "false");
    node.setAttribute("aria-label", `${label} ${span.kind === "cite" ? "근거" : "뜻"} 보기`);
    const open = () => {
      const bubble = node.closest(".bubble");
      const shown = bubble.querySelector(".annotation-detail");
      const mine = node.getAttribute("aria-expanded") === "true";
      if (shown) shown.remove();
      bubble.querySelectorAll('[aria-expanded="true"]').forEach((n) => n.setAttribute("aria-expanded", "false"));
      if (mine) return;
      node.setAttribute("aria-expanded", "true");
      bubble.querySelector(".answer-body").after(detailFor(span, label));
    };
    node.addEventListener("click", open);
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); }
    });
    return node;
  }
  function answerText(text, message = {}) {
    const body = element("div", undefined, "answer-body");
    const source = Array.from(String(text));
    const spans = annotations(message);
    if (!spans.length) return markup(message.role === "assistant" ? formatAnswerSections(source.join("")) : source.join(""), body);
    let cursor = 0;
    for (const span of spans) {
      if (span.start > cursor) markup(source.slice(cursor, span.start).join(""), body);
      body.append(trigger(span, source.slice(span.start, span.end).join("")));
      if (span.kind === "cite" && span.url) {
        const link = safeLink(span.url, " ↗");
        link.setAttribute("aria-label", `${span.citation} 원문 보기`);
        body.append(link);
      }
      cursor = span.end;
    }
    if (cursor < source.length) markup(source.slice(cursor).join(""), body);
    return body;
  }
  function renderMessage(message) {
    const row = element("article", undefined, `message ${message.role}`);
    if (message.id) row.dataset.messageId = message.id;
    row.setAttribute("aria-label", message.role === "user" ? "내 질문" : "LENS 답변");
    if (message.role !== "user") row.append(element("span", "L", "avatar"));
    const bubble = element("div", undefined, "bubble");
    const body = answerText(message.content, message.role === "assistant" ? message : {});
    bubble.append(body);
    if (message.context_excluded) bubble.append(element("p", "문서 삭제 후 보존된 기록 · 이후 답변의 근거에서 제외됨", "doc-meta"));
    if (!message.context_excluded && message.role === "assistant" && message.followup_question) {
      body.append(element("p", message.followup_question));
    }
    if (message.role === "assistant") {
      const meta = element("div", undefined, "message-meta");
      const labels = { answered: "근거 확인 답변", document_review: "문서 표시 확인", abstained: "답변 보류", refused: "요청 안내", social: "대화", clarify: "확인 질문" };
      const reasons = { no_evidence: "관련 근거 부족", validation_failed: "검증 결과 답변 보류", generation_failed: "답변 생성 실패", needs_information: "추가 정보 확인" };
      meta.append(element("span", reasons[message.reason] || labels[message.status] || "안내", `answer-status ${message.status || ""}`));
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
      if (message.simplified) {
        const box = element("div", undefined, "simplified");
        box.append(element("span", "쉽게 다시 설명", "simplified-label"));
        box.append(markup(message.simplified, element("p")));
        bubble.append(box);
      } else if (!message.context_excluded && message.status === "answered" && message.id) {
        const button = element("button", "쉽게 다시 설명해줘", "text-action");
        button.type = "button"; button.dataset.simplify = message.id;
        button.addEventListener("click", () => action("답변을 쉬운 말로 바꾸고 있어요", async () => {
          render(await post(form.dataset.simplifyUrl, { message_id: message.id }));
        }));
        bubble.append(button);
      }
      if (message.followups?.length) {
        const box = element("div", undefined, "followups");
        box.append(element("span", "이 조항의 다른 부분도 궁금하신가요?", "followups-label"));
        const chips = element("div", undefined, "followup-chips");
        for (const citation of message.followups) {
          const chip = element("button", citation, "followup");
          chip.type = "button"; chip.dataset.question = `${citation}에 대해 설명해줘`;
          chip.addEventListener("click", () => { input.value = chip.dataset.question; controls(); input.focus(); });
          chips.append(chip);
        }
        box.append(chips); bubble.append(box);
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
      const unavailable = (analysis.fields || []).filter(check => check.status === "not_found");
      const checks = [...(analysis.signals || []), ...(analysis.fields || []).filter(check => check.status !== "not_found"), ...(analysis.clauses || []), ...unavailable];
      if (checks.length) {
        const list = element("ul");
        for (const check of checks) {
          if (check === unavailable[0]) {
            const heading = element("li");
            heading.append(element("strong", "현재 첨부 범위에서 확인할 수 없는 항목"), element("p", "다른 페이지에 있거나 OCR이 읽지 못했을 수 있습니다. 계약서 전체의 누락을 뜻하지 않습니다."));
            list.append(heading);
          }
          const li = element("li");
          li.append(element("strong", `${check.title}${check.page_number ? ` (${check.page_number}쪽)` : ""}`));
          const statuses = { confirmed: "문구 탐지", review: "원본 확인 필요", not_found: "첨부 범위에서 미확인", included: "관련 특약 탐지", recommended: "협의 권장", high: "우선 확인", caution: "주의 확인", info: "참고" };
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
      const remove = element("button", undefined, "document-remove"); remove.type = "button";
      remove.title = "문서 삭제";
      const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      icon.setAttribute("viewBox", "0 0 24 24"); icon.setAttribute("aria-hidden", "true");
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5");
      icon.append(path); remove.append(icon);
      remove.setAttribute("aria-label", `${doc.filename} 문서 삭제`);
      remove.addEventListener("click", (event) => {
        event.preventDefault(); event.stopPropagation();
        selectedDelete = doc.document_id;
        $("delete-document-name").textContent = doc.filename;
        $("delete-dialog").showModal();
      });
      summary.append(remove); box.append(card);
    }
    if (documents.some((doc) => doc.document_id === previous)) select.value = previous;
  }
  function renderConversation(data) {
    const context = data.conversation || {};
    const pending = context.enabled ? context.pending : null;
    input.placeholder = "궁금한 내용을 편하게 물어보세요.";
    form.dataset.replyTo = pending?.message_id || "";
    input.setAttribute("aria-description", "질문하거나 직전 답변에 이어서 자유롭게 입력하세요.");
    // Suggested replies and free typing share the same composer and pending turn.
    $("messages").querySelectorAll(".conversation-choices").forEach(node => node.remove());
    const row = Array.from($("messages").children).find(node => node.dataset.messageId === pending?.message_id);
    if (pending && row) {
      const choices = element("div", undefined, "clarification-choices conversation-choices");
      for (const choice of pending.choices || []) {
        const button = element("button", choice.label); button.type = "button";
        button.addEventListener("click", () => {
          input.value = choice.message;
          if (choice.document_id) $("document-select").value = choice.document_id;
          replyingTo = pending.message_id; controls(); form.requestSubmit();
        });
        choices.append(button);
      }
      const typeReply = element("button", "직접 입력"); typeReply.type = "button";
      typeReply.addEventListener("click", () => {
        replyingTo = pending.message_id;
        input.focus(); controls();
      });
      choices.append(typeReply);
      row.querySelector(".bubble").append(choices);
    }
    $("answer-actions").hidden = !(context.enabled && context.can_rephrase);
    const active = data.documents.find((doc) => doc.document_id === context.active_document_id);
    $("active-document").hidden = !active;
    $("active-document").textContent = active ? `최근 확인 문서: ${active.filename}` : "";
  }
  function render(data) {
    if (conversationId !== data.conversation_id) submission.clear();
    const durations = (data.messages || []).filter(m => m.role === "assistant" && m.status === "answered" && Number.isFinite(m.elapsed_seconds) && m.elapsed_seconds > 0).slice(-5).map(m => m.elapsed_seconds).sort((a,b) => a-b);
    estimatedSeconds = durations.length ? Math.max(5, durations[Math.floor(durations.length / 2)]) : 30;
    conversationId = data.conversation_id;
    externalBusy = Boolean(data.busy);
    const signature = JSON.stringify([data.messages, data.documents]);
    const changed = signature !== lastRendered;
    if (changed) {
      $("messages").replaceChildren(...data.messages.map(renderMessage));
      $("welcome").hidden = data.messages.length > 0;
      renderDocuments(data.documents);
      lastRendered = signature;
    }
    renderConversation(data); controls();
    if (changed) requestAnimationFrame(scrollBottom);
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
  async function action(label, operation, showEstimate = false) {
    if (busy || externalBusy || !conversationId) return;
    busy = true; controls(); notice("");
    $("pending").hidden = false; $("pending-text").textContent = label;
    const start = Date.now(); $("elapsed").textContent = "";
    const estimate = estimatedSeconds;
    $("estimate").hidden = !showEstimate;
    $("answer-progress").value = 0;
    $("estimate-text").textContent = `예상 약 ${Math.ceil(estimate)}초 남음`;
    const timer = setInterval(() => {
      const elapsed = (Date.now() - start) / 1000;
      $("elapsed").textContent = `${Math.floor(elapsed)}초`;
      if (showEstimate) {
        if (elapsed < estimate) {
          $("answer-progress").value = Math.min(95, elapsed / estimate * 100);
          $("estimate-text").textContent = `예상 약 ${Math.ceil(estimate - elapsed)}초 남음`;
        } else {
          $("answer-progress").removeAttribute("value");
          $("estimate-text").textContent = "예상보다 오래 걸리고 있어요. 답변을 준비 중입니다.";
        }
      }
    }, 1000);
    try { await operation(); }
    catch (error) {
      notice(error.message, [403, 409, 410].includes(error.status) || !error.status);
      // A lost response may still have committed: refresh authoritative server history.
      await syncState();
    } finally {
      clearInterval(timer); busy = false; $("pending").hidden = true; controls();
    }
  }
  input.addEventListener("input", () => { replyingTo = null; controls(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault(); if (!$("send").disabled) form.requestSubmit();
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault(); const question = input.value.trim();
    if (!question) return;
    action("질문을 이해하고 필요한 내용을 확인하고 있어요", async () => {
      $("welcome").hidden = true;
      $("messages").append(renderMessage({ role: "user", content: question })); scrollBottom();
      lastRendered = "";
      const payload = { message: question, document_id: $("document-select").value || null, ...((replyingTo || form.dataset.replyTo) ? { reply_to: replyingTo || form.dataset.replyTo } : {}) };
      const requestId = submission.get({ conversation_id: conversationId, ...payload });
      const submittedText = input.value;
      const submittedReply = replyingTo;
      const requestController = new AbortController();
      const active = { requestId, controller: requestController, cancelled: false, draft: submittedText, replyTo: submittedReply };
      activeChat = active; controls();
      input.value = ""; replyingTo = null;
      let data;
      try {
        data = await post(form.dataset.sendUrl, payload, requestId, { signal: requestController.signal });
      } catch (error) {
        if (active.cancelled) return;
        // Keep a newer draft; restore the failed submission only into an untouched composer.
        if (!input.value) { input.value = submittedText; replyingTo = submittedReply; }
        throw error;
      } finally {
        if (activeChat === active) { activeChat = null; controls(); }
      }
      submission.clear();
      render(data);
      if (data.room_created && !input.value) { window.location.reload(); return; }
      input.focus();
    }, true);
  });
  $("cancel-answer").addEventListener("click", async () => {
    const active = activeChat;
    if (!active || !conversationId) return;
    const button = $("cancel-answer");
    button.disabled = true;
    try {
      const data = await post(form.dataset.cancelUrl, {}, active.requestId);
      active.cancelled = true;
      submission.clear();
      if (!input.value) { input.value = active.draft; replyingTo = active.replyTo; }
      // Do not wait for the aborted send fetch to settle before re-enabling the
      // composer. Browsers can defer that rejection while the server finishes
      // its upstream model call, which otherwise makes search look disabled.
      if (activeChat === active) activeChat = null;
      busy = false;
      externalBusy = false;
      controls();
      active.controller.abort();
      render({ ...data, busy: false });
      notice("응답을 중지했습니다. 내용을 고쳐 다시 보낼 수 있어요.");
      input.focus();
    } catch (error) {
      // The answer may have completed just before the click; restore the server state.
      await syncState();
      if (error.status !== 409) notice(error.message, [403, 410].includes(error.status) || !error.status);
    } finally {
      controls();
    }
  });
  const reportForm = $("report-form");
  if (reportForm) reportForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = reportForm.querySelector("button");
    const label = reportForm.querySelector(".report-button-label");
    if (button.disabled) return;
    button.disabled = true;
    button.classList.add("loading");
    label.textContent = "리포트 생성 중";
    try {
      const response = await fetch(reportForm.action, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf },
      });
      if (!response.ok) throw new Error("리포트를 만들지 못했습니다.");
      const blob = await response.blob();
      if (blob.type !== "application/pdf") throw new Error("PDF 응답을 확인하지 못했습니다.");
      const disposition = response.headers.get("Content-Disposition") || "";
      const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
      const filename = filenameMatch ? filenameMatch[1] : "LENS_report.pdf";
      const downloadUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = downloadUrl;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
      toast("PDF가 다운로드되고 마이페이지에 저장됐습니다.");
    } catch (error) {
      toast(error.message || "잠시 후 다시 시도해 주세요.", "error");
    } finally {
      button.disabled = false;
      button.classList.remove("loading");
      label.textContent = "리포트 PDF";
    }
  });
  document.querySelectorAll("[data-question]").forEach((button) => button.addEventListener("click", () => {
    input.value = button.dataset.question; replyingTo = null; controls(); input.focus();
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
  if ($("new-chat").dataset.createRoom !== "true") {
    $("new-chat").addEventListener("click", () => $("reset-dialog").showModal());
    $("cancel-reset").addEventListener("click", () => $("reset-dialog").close());
    $("confirm-reset").addEventListener("click", () => {
      $("reset-dialog").close(); action("새 대화를 준비하고 있어요", async () => {
        render(await post(form.dataset.resetUrl)); submission.clear(); replyingTo = null; input.value = ""; $("upload-progress").replaceChildren(); input.focus();
      });
    });
  }
  $("cancel-delete").addEventListener("click", () => $("delete-dialog").close());
  $("confirm-delete").addEventListener("click", () => {
    const documentId = selectedDelete;
    if (!documentId) return;
    $("delete-dialog").close(); action("문서를 삭제하고 있어요", async () => {
      render(await post(`${form.dataset.uploadUrl}${encodeURIComponent(documentId)}/delete/`));
      selectedDelete = null;
      submission.clear(); replyingTo = null;
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
