/* Video comments + replies: post / reply / delete without reloading, refresh every 20 s while visible,
   "pin to this moment" timestamps that seek the video. The server renders the thread (escaped by Jinja). */
(function () {
  "use strict";
  const root = document.querySelector("[data-comments]");
  if (!root) return;
  const CN = window.CN;
  const vid = root.dataset.video;
  const list = root.querySelector("[data-comment-list]");
  const count = root.querySelector("[data-comment-count]");
  const form = root.querySelector("[data-comment-form]");
  const player = document.querySelector(".cn-player video");
  let version = root.dataset.version || "";
  let replyBox = null;

  if (player) { const wrap = root.querySelector("[data-at-time-wrap]"); if (wrap) wrap.hidden = false; }

  function apply(data) {
    if (!data || typeof data.html !== "string") return;
    closeReply();
    list.innerHTML = data.html;
    count.textContent = data.count;
    version = data.version;
    if (data.notice) CN.toast(data.notice, "warning", 6000);
  }
  function payload(formEl, extra) {
    const fd = new FormData(formEl);
    fd.delete("_once");               // sent with the CSRF header instead; the button is disabled while sending
    Object.keys(extra || {}).forEach((k) => fd.set(k, extra[k]));
    return new URLSearchParams(fd);
  }
  async function submit(formEl, extra) {
    const button = formEl.querySelector("[type=submit]");
    if (button.disabled) return false;
    button.disabled = true;
    try {
      apply(await CN.api("/videos/" + vid + "/comments", { method: "POST", body: payload(formEl, extra) }));
      return true;
    } catch (err) {
      CN.toast(err.message, "danger", 7000);
      return false;
    } finally { button.disabled = false; }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = form.elements.body.value.trim();
    if (!text) return;
    const pin = form.querySelector("[data-at-time]");
    const extra = { video_seconds: pin && pin.checked && player ? String(Math.floor(player.currentTime || 0)) : "" };
    if (await submit(form, extra)) { form.reset(); }
  });
  form.elements.body.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); }
  });

  function closeReply() { if (replyBox) { replyBox.remove(); replyBox = null; } }
  function openReply(parentId, name) {
    closeReply();
    const thread = list.querySelector('[data-thread="' + parentId + '"]');
    if (!thread) return;
    replyBox = document.createElement("form");
    replyBox.className = "cn-reply-form";
    replyBox.setAttribute("data-ajax", "");          // handled here, not by the global form guard
    const area = document.createElement("textarea");
    area.className = "form-control"; area.name = "body"; area.rows = 2; area.maxLength = 1000; area.required = true;
    area.placeholder = "Reply to " + name + "…";
    area.value = "@" + name.split(" ")[0] + " ";
    const actions = document.createElement("div"); actions.className = "d-flex flex-column gap-1";
    const send = document.createElement("button"); send.type = "submit"; send.className = "btn btn-primary btn-sm"; send.textContent = "Reply";
    const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "btn btn-ghost btn-sm"; cancel.textContent = "Cancel";
    cancel.addEventListener("click", closeReply);
    actions.append(send, cancel);
    replyBox.append(area, actions);
    replyBox.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!area.value.trim()) return;
      const fd = new FormData(); fd.set("body", area.value); fd.set("parent_id", String(parentId));
      send.disabled = true;
      try { apply(await CN.api("/videos/" + vid + "/comments", { method: "POST", body: new URLSearchParams(fd) })); }
      catch (err) { CN.toast(err.message, "danger", 7000); send.disabled = false; }
    });
    thread.appendChild(replyBox);
    area.focus();
    area.setSelectionRange(area.value.length, area.value.length);
  }

  list.addEventListener("click", async (e) => {
    const seek = e.target.closest("[data-seek]");
    if (seek && player) {
      player.currentTime = Number(seek.dataset.seek) || 0;
      player.play().catch(() => {});
      player.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    const reply = e.target.closest("[data-reply]");
    if (reply) { openReply(reply.dataset.reply, reply.dataset.replyName || ""); return; }
    const del = e.target.closest("[data-delete]");
    if (del && await CN.confirm("Delete this comment?", { okText: "Delete" })) {
      try { apply(await CN.api("/comments/" + del.dataset.delete + "/delete", { method: "POST" })); }
      catch (err) { CN.toast(err.message, "danger"); }
    }
  });

  async function refresh() {                       // pick up new comments / replies from others
    if (document.hidden || replyBox || root.contains(document.activeElement) && document.activeElement.tagName === "TEXTAREA") return;
    try {
      const data = await CN.api("/videos/" + vid + "/comments");
      if (data && data.version !== version) apply(data);
    } catch (e) { /* offline - try again later */ }
  }
  setInterval(refresh, 20000);

  if (/^#c\d+$/.test(location.hash)) {             // arrived from a notification
    const target = document.getElementById(location.hash.slice(1));
    if (target) { target.classList.add("highlight"); setTimeout(() => target.scrollIntoView({ block: "center" }), 300); }
  }
})();
