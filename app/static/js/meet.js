/* ClassNest live classes - Google-Meet-style WebRTC mesh.
   Signalling: /ws/meet/<code>. Newcomers send offers; fixed audio+video transceivers mean camera <-> screen
   switching uses replaceTrack (no renegotiation). Host records a composite (canvas + mixed audio) and uploads
   5-second chunks. Captions (Web Speech API) feed the transcript for AI notes. */
(function () {
  "use strict";
  const CN = window.CN;
  const cfg = CN.readJSON("meet-config");
  if (!cfg) return;
  const $ = (s) => document.querySelector(s);
  const csrf = () => document.body.dataset.csrf || "";
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

  const S = { ws: null, me: null, ice: cfg.ice || [], peers: new Map(), local: new MediaStream(), audio: null, cam: null,
    screen: null, micOn: true, camOn: true, hand: false, captions: false, rec: null, recorder: null, mix: null,
    ended: false, retries: 0, startedAt: null, panel: null, audioCtx: null, analysers: new Map(), unread: 0 };

  /* ---------------------------------------------------------------- media */
  async function getMedia() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      CN.toast("Camera and microphone need HTTPS (or localhost). You can still watch and chat.", "warning", 12000);
      S.micOn = S.camOn = false; return;
    }
    try {
      S.local = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true },
        video: { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 24 } } });
    } catch (e) {
      try { S.local = await navigator.mediaDevices.getUserMedia({ audio: true }); S.camOn = false;
        CN.toast("Camera unavailable — joining with audio only.", "warning"); }
      catch (e2) { S.local = new MediaStream(); S.micOn = S.camOn = false;
        CN.toast("No camera/microphone permission. You can still watch, listen and chat.", "warning", 9000); }
    }
    S.audio = S.local.getAudioTracks()[0] || null;
    S.cam = S.local.getVideoTracks()[0] || null;
    if (S.audio) S.audio.enabled = S.micOn;
    if (S.cam) S.cam.enabled = S.camOn;
  }
  const outgoingVideo = () => S.screen || S.cam;

  /* ---------------------------------------------------------------- lobby */
  const lobbyVideo = $("#lobby-video");
  function syncLobby() {
    const mic = $("[data-lobby-mic]"), cam = $("[data-lobby-cam]");
    if (mic) { mic.classList.toggle("off", !S.micOn); mic.innerHTML = '<i class="fa-solid ' + (S.micOn ? "fa-microphone" : "fa-microphone-slash") + '"></i>'; }
    if (cam) { cam.classList.toggle("off", !S.camOn); cam.innerHTML = '<i class="fa-solid ' + (S.camOn ? "fa-video" : "fa-video-slash") + '"></i>'; }
  }
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-lobby-mic]") && S.audio) { S.micOn = !S.micOn; S.audio.enabled = S.micOn; syncLobby(); }
    if (e.target.closest("[data-lobby-cam]") && S.cam) { S.camOn = !S.camOn; S.cam.enabled = S.camOn; syncLobby(); }
  });
  getMedia().then(() => { if (lobbyVideo) lobbyVideo.srcObject = S.local; syncLobby(); });

  const startBtn = $("[data-start]"), joinBtn = $("[data-join]"), waitText = $("[data-wait]");
  if (startBtn) startBtn.addEventListener("click", async () => {
    CN.busy(startBtn, true, "Starting…");
    try { await CN.api(cfg.startUrl, { method: "POST" }); join(); }
    catch (err) { CN.busy(startBtn, false); CN.toast(err.message, "danger"); }
  });
  if (joinBtn) {
    joinBtn.addEventListener("click", join);
    const poll = () => CN.api(cfg.statusUrl).then((d) => {
      if (d.status === "live") { joinBtn.disabled = false; if (waitText) waitText.textContent = d.participants + " in the class — you can join now."; }
      else if (d.status === "ended") { if (waitText) waitText.textContent = "This class has ended. Notes and the recording will appear in your classes."; }
      else { joinBtn.disabled = true; if (waitText) waitText.textContent = "Waiting for the host to start the class…"; setTimeout(poll, 4000); }
    }).catch(() => setTimeout(poll, 6000));
    poll();
  }

  /* ---------------------------------------------------------------- room UI */
  const tiles = $("#tiles");
  function layout() {
    const n = tiles.children.length || 1;
    const cols = n <= 1 ? 1 : n <= 4 ? 2 : n <= 9 ? 3 : 4;
    tiles.style.setProperty("--cols", String(window.innerWidth < 700 ? Math.min(cols, 2) : cols));
    const count = $("[data-count]"); if (count) count.textContent = String(n);
  }
  function initials(name) { const p = (name || "?").trim().split(/\s+/); return ((p[0] || "?")[0] + (p.length > 1 ? p[p.length - 1][0] : "")).toUpperCase(); }
  function addTile(id, name, stream, isLocal) {
    let tile = document.getElementById("tile-" + id);
    if (!tile) {
      tile = document.createElement("div");
      tile.className = "cn-tile";
      tile.id = "tile-" + id;
      const video = document.createElement("video");
      video.autoplay = true; video.playsInline = true;
      if (isLocal) { video.muted = true; video.style.transform = "scaleX(-1)"; }
      const avatar = document.createElement("div"); avatar.className = "cn-tile-avatar";
      const circle = document.createElement("span"); circle.textContent = initials(name); avatar.appendChild(circle);
      const label = document.createElement("div"); label.className = "cn-tile-name";
      const mic = document.createElement("i"); mic.className = "fa-solid fa-microphone-slash"; mic.hidden = true;
      const text = document.createElement("span"); text.textContent = name;
      label.append(mic, text);
      const hand = document.createElement("div"); hand.className = "cn-tile-hand"; hand.textContent = "✋";
      tile.append(video, avatar, label, hand);
      tiles.appendChild(tile);
    }
    const v = tile.querySelector("video");
    if (v.srcObject !== stream) v.srcObject = stream;
    v.play().catch(() => {});
    layout();
    return tile;
  }
  function setTileState(id, st) {
    const tile = document.getElementById("tile-" + id);
    if (!tile || !st) return;
    tile.classList.toggle("cam-off", st.video === false && !st.screen);
    tile.classList.toggle("hand", !!st.hand);
    tile.classList.toggle("screen", !!st.screen);
    const mic = tile.querySelector(".cn-tile-name i"); if (mic) mic.hidden = st.audio !== false;
  }
  function localState() { return { audio: !!(S.audio && S.micOn), video: !!(S.cam && S.camOn), screen: !!S.screen, hand: S.hand }; }
  function refreshLocal() {
    setTileState("local", localState());
    const v = document.querySelector("#tile-local video");
    if (v) { v.srcObject = S.screen ? new MediaStream([S.screen]) : S.local; v.style.transform = S.screen ? "none" : "scaleX(-1)"; }
    syncControls();
    send(Object.assign({ type: "state" }, localState()));
  }

  /* ---------------------------------------------------------------- signalling */
  function send(obj) { if (S.ws && S.ws.readyState === 1) { S.ws.send(JSON.stringify(obj)); return true; } return false; }
  function connect() {
    const ws = (S.ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + cfg.ws));
    ws.addEventListener("open", () => { S.retries = 0; setConn("Connected", "success"); });
    ws.addEventListener("message", (e) => { let m; try { m = JSON.parse(e.data); } catch (x) { return; } onMessage(m); });
    ws.addEventListener("close", (e) => {
      Array.from(S.peers.keys()).forEach(removePeer);
      if (S.ended) return;
      if (e.code === 4403) return leave("You don't have access to this meeting.");
      if (e.code === 4409) return leave("The class hasn't started yet.");
      if (e.code === 4001 || e.code === 4000) return;
      setConn("Reconnecting…", "warning");
      setTimeout(connect, Math.min(10000, 1000 * Math.pow(2, S.retries++)));
    });
  }
  setInterval(() => send({ type: "ping" }), 25000);

  async function onMessage(m) {
    switch (m.type) {
      case "welcome":
        S.me = m.peer_id; if (m.ice && m.ice.length) S.ice = m.ice;
        setRecording(m.recording);
        for (const p of m.peers) { addPeer(p); await makeOffer(p.peer_id); }
        refreshLocal(); renderPeople();
        break;
      case "peer-joined": addPeer(m.peer); systemLine(m.peer.name + " joined"); break;
      case "peer-left": { const p = S.peers.get(m.peer_id); if (p) systemLine(p.name + " left"); removePeer(m.peer_id); break; }
      case "signal": await onSignal(m.from, m.data); break;
      case "peer-state": { const p = S.peers.get(m.peer_id); if (p) { p.state = m.state; setTileState(p.id, m.state); renderPeople(); } break; }
      case "chat": addChat(m); break;
      case "caption": showCaption(m.name, m.text, m.final); break;
      case "reaction": showReaction(m.emoji); break;
      case "notice": CN.toast(m.text, m.level || "info", 9000); systemLine(m.text); break;
      case "recording": setRecording(m.on); break;
      case "force-mute": if (S.micOn) toggleMic(); CN.toast("You were muted by " + m.by, "info"); break;
      case "kicked": S.ended = true; leave(m.reason || "You were removed from the meeting."); break;
      case "replaced": S.ended = true; leave("You joined this meeting from another tab or device."); break;
      case "ended": S.ended = true; await stopRecording(); showEnded(); break;
      default: break;
    }
  }

  /* ---------------------------------------------------------------- peers */
  function addPeer(info) {
    if (S.peers.has(info.peer_id)) return S.peers.get(info.peer_id);
    const pc = new RTCPeerConnection({ iceServers: S.ice });
    const peer = { id: info.peer_id, name: info.name || "Participant", role: info.role, isHost: !!info.is_host, pc,
      stream: new MediaStream(), pending: [], state: info.state || {} };
    S.peers.set(peer.id, peer);
    pc.addEventListener("icecandidate", (e) => { if (e.candidate) send({ type: "signal", to: peer.id, data: { candidate: e.candidate } }); });
    pc.addEventListener("track", (e) => {
      peer.stream.addTrack(e.track);
      addTile(peer.id, peer.name, peer.stream, false);
      if (e.track.kind === "audio") { mixAudio(peer.id, peer.stream); watchSpeaking(peer.id, peer.stream); }
    });
    pc.addEventListener("connectionstatechange", () => {
      if (pc.connectionState === "failed" && pc.restartIce) pc.restartIce();
    });
    addTile(peer.id, peer.name, peer.stream, false);
    setTileState(peer.id, peer.state);
    renderPeople();
    return peer;
  }
  function attachLocal(pc, offerer) {
    if (offerer) {
      const a = pc.addTransceiver("audio", { direction: "sendrecv" });
      const v = pc.addTransceiver("video", { direction: "sendrecv" });
      if (S.audio) a.sender.replaceTrack(S.audio);
      if (outgoingVideo()) v.sender.replaceTrack(outgoingVideo());
      return;
    }
    pc.getTransceivers().forEach((t) => {
      const kind = t.receiver && t.receiver.track ? t.receiver.track.kind : null;
      t.direction = "sendrecv";
      if (kind === "audio" && S.audio) t.sender.replaceTrack(S.audio);
      if (kind === "video" && outgoingVideo()) t.sender.replaceTrack(outgoingVideo());
    });
  }
  async function makeOffer(id) {
    const peer = S.peers.get(id);
    if (!peer) return;
    attachLocal(peer.pc, true);
    await peer.pc.setLocalDescription(await peer.pc.createOffer());
    send({ type: "signal", to: id, data: { sdp: peer.pc.localDescription } });
  }
  async function onSignal(from, data) {
    if (!data) return;
    const peer = S.peers.get(from) || addPeer({ peer_id: from, name: "Participant" });
    const pc = peer.pc;
    try {
      if (data.sdp) {
        await pc.setRemoteDescription(data.sdp);
        if (data.sdp.type === "offer") {
          attachLocal(pc, false);
          await pc.setLocalDescription(await pc.createAnswer());
          send({ type: "signal", to: from, data: { sdp: pc.localDescription } });
        }
        while (peer.pending.length) await pc.addIceCandidate(peer.pending.shift());
      } else if (data.candidate) {
        if (pc.remoteDescription) await pc.addIceCandidate(data.candidate); else peer.pending.push(data.candidate);
      }
    } catch (err) { console.warn("WebRTC signalling error", err); }
  }
  function removePeer(id) {
    const p = S.peers.get(id);
    if (!p) return;
    try { p.pc.close(); } catch (e) { /* already closed */ }
    S.peers.delete(id);
    S.analysers.delete(id);
    const tile = document.getElementById("tile-" + id);
    if (tile) tile.remove();
    layout(); renderPeople();
  }
  function replaceVideoEverywhere(track) {
    S.peers.forEach((p) => p.pc.getTransceivers().forEach((t) => {
      if (t.receiver && t.receiver.track && t.receiver.track.kind === "video") t.sender.replaceTrack(track || null);
    }));
  }

  /* ---------------------------------------------------------------- controls */
  function toggleMic() {
    if (!S.audio) { CN.toast("No microphone available.", "warning"); return; }
    S.micOn = !S.micOn; S.audio.enabled = S.micOn;
    if (!S.micOn && S.rec) { try { S.rec.stop(); } catch (e) { /* ignore */ } }
    if (S.micOn && S.captions && S.rec) { try { S.rec.start(); } catch (e) { /* already running */ } }
    refreshLocal();
  }
  function toggleCam() {
    if (!S.cam) { CN.toast("No camera available.", "warning"); return; }
    S.camOn = !S.camOn; S.cam.enabled = S.camOn; refreshLocal();
  }
  async function toggleScreen() {
    if (S.screen) { stopScreen(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) { CN.toast("Screen sharing isn't supported here.", "warning"); return; }
    try {
      const s = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 15 }, audio: false });
      S.screen = s.getVideoTracks()[0];
      S.screen.addEventListener("ended", stopScreen);
      replaceVideoEverywhere(S.screen);
      refreshLocal();
    } catch (e) { /* user cancelled */ }
  }
  function stopScreen() {
    if (!S.screen) return;
    S.screen.stop(); S.screen = null;
    replaceVideoEverywhere(S.cam); refreshLocal();
  }
  function toggleHand() { S.hand = !S.hand; refreshLocal(); }
  function syncControls() {
    const set = (sel, on, iconOn, iconOff) => { const b = $(sel); if (!b) return; b.classList.toggle("off", !on); b.innerHTML = '<i class="fa-solid ' + (on ? iconOn : iconOff) + '"></i>'; };
    set("[data-ctl=mic]", S.micOn && !!S.audio, "fa-microphone", "fa-microphone-slash");
    set("[data-ctl=cam]", S.camOn && !!S.cam, "fa-video", "fa-video-slash");
    const scr = $("[data-ctl=screen]"); if (scr) scr.classList.toggle("on", !!S.screen);
    const hand = $("[data-ctl=hand]"); if (hand) hand.classList.toggle("on", S.hand);
    const cc = $("[data-ctl=cc]"); if (cc) cc.classList.toggle("on", S.captions);
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-ctl]");
    if (!b) return;
    const what = b.dataset.ctl;
    if (what === "mic") toggleMic();
    else if (what === "cam") toggleCam();
    else if (what === "screen") toggleScreen();
    else if (what === "hand") toggleHand();
    else if (what === "cc") (S.captions ? stopCaptions() : startCaptions());
    else if (what === "chat" || what === "people") togglePanel(what);
    else if (what === "react") { const emoji = b.dataset.emoji; send({ type: "reaction", emoji }); }
    else if (what === "leave") leaveClicked();
  });
  document.addEventListener("keydown", (e) => {
    if (!e.ctrlKey || $("#cn-room").hidden) return;
    if (e.key === "d") { e.preventDefault(); toggleMic(); }
    if (e.key === "e") { e.preventDefault(); toggleCam(); }
  });

  /* ---------------------------------------------------------------- side panel: chat + people */
  const room = $("#cn-room");
  function togglePanel(name) {
    if (S.panel === name) { S.panel = null; room.classList.remove("panel-open"); return; }
    S.panel = name; room.classList.add("panel-open");
    document.querySelectorAll("[data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== name; });
    document.querySelectorAll("[data-panel-tab]").forEach((t) => t.classList.toggle("active", t.dataset.panelTab === name));
    if (name === "chat") { S.unread = 0; const badge = $("[data-chat-badge]"); if (badge) badge.hidden = true; const i = $("[data-chat-input]"); if (i) i.focus(); }
  }
  document.querySelectorAll("[data-panel-tab]").forEach((t) => t.addEventListener("click", () => togglePanel(t.dataset.panelTab)));
  const chatLog = $("[data-chat-log]");
  function addChat(m) {
    const mine = m.from === S.me;
    const el = CN.bubble(chatLog, (mine ? "mine" : "") + (m.flagged ? " flagged" : ""), m.text,
      (mine ? "You" : m.name) + " · " + new Date(m.ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
    if (S.panel !== "chat" && !mine) {
      S.unread += 1;
      const badge = $("[data-chat-badge]"); if (badge) { badge.hidden = false; badge.textContent = String(S.unread); }
      CN.toast(m.name + ": " + m.text.slice(0, 80), "info", 3500);
    }
    return el;
  }
  function systemLine(text) { if (chatLog) CN.bubble(chatLog, "system", text); }
  const chatForm = $("[data-chat-form]");
  if (chatForm) {
    const input = $("[data-chat-input]");
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); chatForm.requestSubmit(); } });
    chatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const text = input.value.trim();
      if (text && send({ type: "chat", text })) input.value = "";
    });
  }
  const people = $("[data-people]");
  function renderPeople() {
    if (!people) return;
    people.replaceChildren();
    const rows = [{ id: "local", name: cfg.me.name + " (You)", state: localState(), isHost: cfg.isHost }]
      .concat(Array.from(S.peers.values()));
    rows.forEach((p) => {
      const row = document.createElement("div"); row.className = "cn-person-row";
      const av = document.createElement("span"); av.className = "cn-avatar"; av.style.setProperty("--size", "34px"); av.textContent = initials(p.name);
      const name = document.createElement("div"); name.className = "flex-grow-1 small fw-bold"; name.textContent = p.name + (p.isHost ? " · Host" : "");
      const icons = document.createElement("span"); icons.className = "small";
      icons.textContent = (p.state && p.state.hand ? "✋ " : "") + (p.state && p.state.audio === false ? "🔇" : "🎙️");
      row.append(av, name, icons);
      if (cfg.isHost && p.id !== "local" && !p.isHost) {
        const mute = document.createElement("button"); mute.className = "btn btn-sm btn-ghost"; mute.title = "Mute"; mute.innerHTML = '<i class="fa-solid fa-microphone-slash"></i>';
        mute.addEventListener("click", () => send({ type: "mute", peer_id: p.id }));
        const kick = document.createElement("button"); kick.className = "btn btn-sm btn-soft-danger"; kick.title = "Remove"; kick.innerHTML = '<i class="fa-solid fa-user-xmark"></i>';
        kick.addEventListener("click", async () => { if (await CN.confirm("Remove " + p.name + " from the meeting?", { okText: "Remove" })) send({ type: "remove", peer_id: p.id }); });
        row.append(mute, kick);
      }
      people.appendChild(row);
    });
    const count = $("[data-count]"); if (count) count.textContent = String(rows.length);
  }

  /* ---------------------------------------------------------------- captions (Web Speech API) */
  const captionBox = $("#captions");
  const captionLines = new Map();
  function showCaption(name, text, final) {
    if (!captionBox) return;
    let line = captionLines.get(name);
    if (!line) { line = document.createElement("div"); captionLines.set(name, line); captionBox.appendChild(line); }
    line.textContent = name + ": " + text;
    clearTimeout(line.timer);
    line.timer = setTimeout(() => { line.remove(); captionLines.delete(name); }, final ? 4500 : 7000);
  }
  let lastInterim = 0;
  function startCaptions() {
    if (!SR) { CN.toast("Live captions need Chrome or Edge. Notes will still use the chat.", "info"); return; }
    if (!S.rec) {
      const rec = (S.rec = new SR());
      rec.continuous = true; rec.interimResults = true; rec.lang = cfg.lang || "en-IN";
      rec.addEventListener("result", (e) => {
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const r = e.results[i];
          const text = r[0].transcript.trim();
          if (!text) continue;
          if (r.isFinal) { send({ type: "caption", text, final: true }); showCaption("You", text, true); }
          else if (Date.now() - lastInterim > 800) { lastInterim = Date.now(); send({ type: "caption", text, final: false }); }
        }
      });
      rec.addEventListener("end", () => { if (S.captions && S.micOn && !S.ended) { try { rec.start(); } catch (e) { /* restarting */ } } });
      rec.addEventListener("error", (e) => { if (e.error === "not-allowed" || e.error === "service-not-allowed") { S.captions = false; syncControls(); } });
    }
    S.captions = true;
    if (S.micOn) { try { S.rec.start(); } catch (e) { /* already started */ } }
    syncControls();
  }
  function stopCaptions() { S.captions = false; if (S.rec) { try { S.rec.stop(); } catch (e) { /* ignore */ } } syncControls(); }

  /* ---------------------------------------------------------------- reactions + speaking */
  function showReaction(emoji) {
    const el = document.createElement("div");
    el.className = "cn-reaction"; el.textContent = emoji;
    el.style.left = (10 + Math.random() * 70) + "%";
    $(".cn-stage").appendChild(el);
    setTimeout(() => el.remove(), 2700);
  }
  function audioCtx() {
    if (!S.audioCtx) { const AC = window.AudioContext || window.webkitAudioContext; if (AC) S.audioCtx = new AC(); }
    return S.audioCtx;
  }
  function watchSpeaking(id, stream) {
    const ctx = audioCtx();
    if (!ctx || !stream.getAudioTracks().length || S.analysers.has(id)) return;
    try {
      const an = ctx.createAnalyser(); an.fftSize = 256;
      ctx.createMediaStreamSource(stream).connect(an);
      S.analysers.set(id, { an, buf: new Uint8Array(an.frequencyBinCount) });
    } catch (e) { /* stream without audio */ }
  }
  setInterval(() => {
    S.analysers.forEach((a, id) => {
      a.an.getByteFrequencyData(a.buf);
      let sum = 0; for (let i = 0; i < a.buf.length; i++) sum += a.buf[i];
      const tile = document.getElementById("tile-" + id);
      if (tile) tile.classList.toggle("speaking", sum / a.buf.length > 18);
    });
  }, 250);

  /* ---------------------------------------------------------------- host recording */
  function setRecording(on) { room.classList.toggle("recording", !!on); }
  function mixAudio(key, stream) {
    if (!S.mix || S.mix.sources.has(key) || !stream.getAudioTracks().length) return;
    try { const src = S.mix.ctx.createMediaStreamSource(stream); src.connect(S.mix.dest); S.mix.sources.set(key, src); } catch (e) { /* ignore */ }
  }
  function drawComposite() {
    const { g, canvas } = S.mix;
    const W = canvas.width, H = canvas.height;
    g.fillStyle = "#0b1020"; g.fillRect(0, 0, W, H);
    const all = Array.from(tiles.children);
    const shared = all.find((t) => t.classList.contains("screen"));
    const others = all.filter((t) => t !== shared);
    const boxes = [];
    if (shared) {
      boxes.push([shared, 0, 0, W * 0.78, H]);
      const h = H / Math.max(1, Math.min(others.length, 4));
      others.slice(0, 4).forEach((t, i) => boxes.push([t, W * 0.78, i * h, W * 0.22, h]));
    } else {
      const n = all.length || 1, cols = Math.ceil(Math.sqrt(n)), rows = Math.ceil(n / cols);
      const w = W / cols, h = H / rows;
      all.forEach((t, i) => boxes.push([t, (i % cols) * w, Math.floor(i / cols) * h, w, h]));
    }
    boxes.forEach(([tile, x, y, w, h]) => {
      const v = tile.querySelector("video");
      const off = tile.classList.contains("cam-off");
      g.save();
      g.beginPath(); g.rect(x + 3, y + 3, w - 6, h - 6); g.clip();
      if (v && v.readyState >= 2 && v.videoWidth && !off) {
        const contain = tile.classList.contains("screen");
        const r = contain ? Math.min(w / v.videoWidth, h / v.videoHeight) : Math.max(w / v.videoWidth, h / v.videoHeight);
        const dw = v.videoWidth * r, dh = v.videoHeight * r;
        g.drawImage(v, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh);
      } else {
        g.fillStyle = "#1f2a4a"; g.fillRect(x, y, w, h);
        g.fillStyle = "#ffffff"; g.font = "bold " + Math.round(Math.min(w, h) / 5) + "px sans-serif";
        g.textAlign = "center"; g.textBaseline = "middle";
        g.fillText(initials(tile.querySelector(".cn-tile-name span").textContent), x + w / 2, y + h / 2);
      }
      g.restore();
      g.fillStyle = "rgba(0,0,0,.55)"; g.fillRect(x + 10, y + h - 40, Math.min(w - 20, 260), 28);
      g.fillStyle = "#fff"; g.font = "600 15px sans-serif"; g.textAlign = "left"; g.textBaseline = "middle";
      g.fillText(tile.querySelector(".cn-tile-name span").textContent, x + 18, y + h - 26);
    });
  }
  function startRecording() {
    if (!window.MediaRecorder || !HTMLCanvasElement.prototype.captureStream) { CN.toast("This browser can't record the class.", "warning"); return; }
    const ctx = audioCtx();
    if (!ctx) return;
    const canvas = document.createElement("canvas"); canvas.width = 1280; canvas.height = 720;
    S.mix = { canvas, g: canvas.getContext("2d"), ctx, dest: ctx.createMediaStreamDestination(), sources: new Map(), seq: 0,
      queue: Promise.resolve(), timer: null };
    mixAudio("local", S.local);
    S.peers.forEach((p) => mixAudio(p.id, p.stream));
    S.mix.timer = setInterval(drawComposite, 1000 / 15);
    const stream = new MediaStream(canvas.captureStream(15).getVideoTracks().concat(S.mix.dest.stream.getAudioTracks()));
    const types = ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"];
    const mime = types.find((t) => MediaRecorder.isTypeSupported(t));
    S.recorder = new MediaRecorder(stream, mime ? { mimeType: mime, videoBitsPerSecond: 1500000 } : undefined);
    S.recorder.addEventListener("dataavailable", (e) => {
      if (!e.data || !e.data.size) return;
      const seq = S.mix.seq++;
      const blob = e.data;
      S.mix.queue = S.mix.queue.then(() => fetch(cfg.uploadUrl + "/" + seq, { method: "POST", credentials: "same-origin",
        headers: { "X-CSRF-Token": csrf(), "Content-Type": "application/octet-stream" }, body: blob })).catch(() => {});
    });
    S.recorder.start(5000);
    send({ type: "recording", on: true });
    setRecording(true);
  }
  async function stopRecording() {
    if (!S.recorder || S.recorder.state === "inactive") return;
    await new Promise((res) => { S.recorder.addEventListener("stop", res, { once: true }); S.recorder.stop(); });
    clearInterval(S.mix.timer);
    await Promise.race([S.mix.queue, new Promise((r) => setTimeout(r, 15000))]);
  }

  /* ---------------------------------------------------------------- join / leave / end */
  function setConn(text, tone) { const el = $("[data-conn]"); if (el) { el.textContent = text; el.className = "cn-badge " + tone; } }
  function join() {
    $("#cn-lobby").hidden = true;
    room.hidden = false;
    S.startedAt = Date.now();
    addTile("local", cfg.me.name + " (You)", S.local, true);
    watchSpeaking("local", S.local);
    refreshLocal();
    connect();
    if (cfg.isHost) { startRecording(); if (SR) startCaptions(); }
    setInterval(() => { const t = $("[data-clock]"); if (t) t.textContent = CN.fmtTime((Date.now() - S.startedAt) / 1000); }, 1000);
    window.addEventListener("beforeunload", (e) => { if (!S.ended && cfg.isHost) { e.preventDefault(); e.returnValue = ""; } });
  }
  async function leaveClicked() {
    if (cfg.isHost) {
      const end = await CN.confirm("End the class for everyone? The recording, attendance and AI notes will be prepared automatically.",
        { okText: "End class", title: "End class" });
      if (!end) return;
      S.ended = true;
      setConn("Saving recording…", "warning");
      await stopRecording();
      try { await CN.api(cfg.endUrl, { method: "POST" }); } catch (e) { send({ type: "end" }); }
      showEnded();
    } else {
      leave(null);
    }
  }
  function stopAll() {
    S.ended = true;
    try { if (S.ws) S.ws.close(); } catch (e) { /* ignore */ }
    S.local.getTracks().forEach((t) => t.stop());
    if (S.screen) S.screen.stop();
    stopCaptions();
  }
  function leave(reason) {
    stopAll();
    if (reason) { const m = $("#ended-modal"); m.querySelector("[data-ended-text]").textContent = reason; m.classList.add("open"); }
    else location.href = cfg.leaveUrl;
  }
  function showEnded() {
    stopAll();
    const m = $("#ended-modal");
    m.querySelector("[data-ended-text]").textContent = "This class has ended. Attendance is being calculated, and the recording and AI notes will be ready shortly.";
    m.classList.add("open");
  }
  window.addEventListener("resize", layout);
})();
