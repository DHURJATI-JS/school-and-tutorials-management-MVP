/* Friends messaging: one live socket for all direct + group conversations. */
(function () {
  "use strict";
  const root = document.querySelector("[data-social]");
  if (!root) return;
  const CN = window.CN;
  const me = root.dataset.me;
  const roomId = Number(root.dataset.room || 0);
  const log = root.querySelector("[data-log]");
  const form = root.querySelector("form[data-send]");
  const input = form ? form.querySelector("textarea") : null;
  const typing = root.querySelector("[data-typing]");
  let oldest = null;
  let typingTimer = null;
  let lastTypingSent = 0;

  function render(m, prepend) {
    if (!log) return;
    const mine = String(m.uid) === me;
    const el = document.createElement("div");
    el.className = "cn-msg" + (mine ? " mine" : "") + (m.flagged ? " flagged" : "");
    const body = document.createElement("div"); body.className = "cn-bubble"; body.textContent = m.text;
    const meta = document.createElement("div"); meta.className = "cn-meta"; meta.textContent = (mine ? "You" : m.name) + " · " + m.ts;
    if (m.badge) { const b = document.createElement("span"); b.className = "cn-badge danger"; b.textContent = m.badge; meta.appendChild(b); }
    el.append(body, meta);
    if (prepend) log.prepend(el); else { log.appendChild(el); log.scrollTop = log.scrollHeight; }
    if (oldest === null || m.id < oldest) oldest = m.id;
  }
  (CN.readJSON("social-initial") || []).forEach((m) => render(m));
  if (log) log.scrollTop = log.scrollHeight;

  function bumpUnread(room) {
    const badge = document.querySelector('[data-room-badge="' + room + '"]');
    if (!badge) return;
    badge.hidden = false;
    badge.textContent = String((Number(badge.textContent) || 0) + 1);
  }
  const sock = CN.socket("/ws/social", (m) => {
    if (m.type === "message") {
      if (m.room === roomId) { render(m); if (String(m.uid) !== me) CN.api("/api/messages/" + roomId + "/read", { method: "POST" }).catch(() => {}); }
      else if (String(m.uid) !== me) { bumpUnread(m.room); CN.toast(m.name + ": " + m.text.slice(0, 80), "info", 4000); }
    } else if (m.type === "typing" && m.room === roomId && typing) {
      typing.textContent = m.name + " is typing…";
      clearTimeout(typingTimer);
      typingTimer = setTimeout(() => { typing.textContent = ""; }, 2500);
    } else if (m.type === "notice") { CN.toast(m.text, "warning", 9000); if (log) CN.bubble(log, "system", m.text); }
    else if (m.type === "kicked") { CN.toast(m.reason, "danger", 12000); sock.stop(); }
    else if (m.type === "room-added") { CN.toast("You were added to a new group — refresh to see it.", "info"); }
  });

  if (form && input) {
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); return; }
      const now = Date.now();
      if (now - lastTypingSent > 2000) { lastTypingSent = now; sock.send({ type: "typing", room: roomId }); }
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      if (sock.send({ type: "message", room: roomId, text })) input.value = "";
      else CN.toast("Connecting… please try again in a second.", "warning");
    });
  }
  if (log) {
    log.addEventListener("scroll", () => {
      if (log.scrollTop > 40 || oldest === null || log.dataset.loading) return;
      log.dataset.loading = "1";
      const before = log.scrollHeight;
      CN.api("/api/messages/" + roomId + "/history?before=" + oldest).then((d) => {
        d.messages.slice().reverse().forEach((m) => render(m, true));
        log.scrollTop = log.scrollHeight - before;
        if (d.messages.length) delete log.dataset.loading;
      }).catch(() => { delete log.dataset.loading; });
    });
  }
})();
