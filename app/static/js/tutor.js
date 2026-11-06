/* AI doubt solver (lesson page + tutor page): streamed answers, optional "ask about this video moment"
   (screenshot of the current frame + timestamp). */
(function () {
  "use strict";
  const root = document.querySelector("[data-tutor]");
  if (!root) return;
  const CN = window.CN;
  const log = root.querySelector("[data-log]");
  const form = root.querySelector("form");
  const input = form.querySelector("textarea");
  const send = form.querySelector("[type=submit]");
  const chip = root.querySelector("[data-frame-chip]");
  const frameBtn = root.querySelector("[data-frame]");
  const batchId = Number(root.dataset.batch);
  const lessonId = root.dataset.lesson ? Number(root.dataset.lesson) : null;
  let frame = null;
  log.scrollTop = log.scrollHeight;

  function showChip() {
    if (!chip) return;
    chip.hidden = !frame;
    if (!frame) return;
    const img = chip.querySelector("img");
    img.hidden = !frame.image;
    if (frame.image) img.src = frame.image;
    chip.querySelector("[data-frame-label]").textContent = "Asking about the moment at " + CN.fmtTime(frame.time) +
      (frame.image ? " (screenshot attached)" : "");
  }
  if (frameBtn) {
    frameBtn.addEventListener("click", () => {
      if (!CN.captureFrame) { CN.toast("Open a video lesson to ask about a moment.", "info"); return; }
      if (CN.pauseVideo) CN.pauseVideo();
      frame = CN.captureFrame();
      showChip();
      if (!input.value.trim()) input.value = "Can you explain what's happening at this point in the video?";
      input.focus();
    });
  }
  if (chip) chip.querySelector("[data-frame-clear]").addEventListener("click", () => { frame = null; showChip(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const question = input.value.trim();
    if (question.length < 2 || send.disabled) return;
    const mine = CN.bubble(log, "mine", "", frame ? "📸 " + CN.fmtTime(frame.time) : "You");
    const myBody = mine.querySelector(".cn-bubble");
    if (frame && frame.image) { const img = document.createElement("img"); img.className = "cn-frame"; img.src = frame.image; img.alt = "Video frame"; myBody.appendChild(img); }
    myBody.appendChild(document.createTextNode(question));
    const payload = { question, batch_id: batchId, lesson_id: lessonId };
    if (frame) { payload.video_time = frame.time; if (frame.image) payload.image = frame.image; }
    frame = null; showChip(); input.value = "";
    CN.busy(send, true, "Thinking…");
    const ai = CN.bubble(log, "ai", "", "AI tutor");
    const body = ai.querySelector(".cn-bubble");
    const meta = ai.querySelector(".cn-meta");
    CN.typing(body);
    let text = "";
    try {
      await CN.sse("/api/ai/ask", payload, (ev) => {
        if (ev.type === "token") { if (!text) body.textContent = ""; text += ev.text; body.textContent = text.replace(/<\/?\s*context\s*>/gi, ""); log.scrollTop = log.scrollHeight; }
        else if (ev.type === "final") {
          body.innerHTML = ev.html;                       // sanitised (nh3) on the server
          if (ev.answered_by === "fallback") meta.textContent = "Offline answer from your notes · shared with your teacher";
        } else if (ev.type === "notice") { CN.bubble(log, "system", ev.text); }
        else if (ev.type === "sources" && ev.items.length) { meta.textContent = "AI tutor · " + ev.items.map((s) => s.title).join(" · "); }
      });
      if (!text && !body.textContent.trim()) body.textContent = "…";
    } catch (err) {
      body.textContent = "⚠️ " + err.message;
    } finally {
      CN.busy(send, false);
      input.focus();
      log.scrollTop = log.scrollHeight;
    }
  });
})();
