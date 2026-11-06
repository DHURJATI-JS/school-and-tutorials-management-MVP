/* Lesson video player: unique-seconds watch tracking (auto attendance at 50%) + frame capture for the AI. */
(function () {
  "use strict";
  const video = document.querySelector("video[data-track]");
  if (!video) return;
  const url = video.dataset.track;
  const meter = document.querySelector("[data-watch-meter] > span");
  const label = document.querySelector("[data-watch-label]");
  let pending = new Set();
  let last = null;
  let marked = video.dataset.marked === "1";

  video.addEventListener("timeupdate", () => {
    if (video.paused || video.seeking) return;
    const s = Math.floor(video.currentTime);
    if (last !== null && s >= last && s - last <= 2) { for (let i = last; i <= s; i++) pending.add(i); }
    last = s;
  });
  video.addEventListener("seeking", () => { last = null; });

  function render(d) {
    if (!d) return;
    const pct = Math.min(100, Math.round((d.ratio || 0) * 100));
    if (meter) meter.style.width = pct + "%";
    if (label) {
      const need = Math.round((d.threshold || 0.5) * 100);
      label.textContent = d.attendance ? "✅ Attendance marked · watched " + pct + "%" :
        d.auto === false ? "Watched " + pct + "% · your teacher marks attendance for this video" :
        "Watched " + pct + "% · attendance is marked at " + need + "%";
    }
    if (d.just_marked && !marked) { marked = true; window.CN.toast("Attendance marked ✅ — you've watched over half of this class.", "success"); }
  }
  function flush(keepalive) {
    if (!pending.size) return;
    const secs = Array.from(pending).sort((a, b) => a - b);
    pending = new Set();
    const intervals = [];
    secs.forEach((s) => { const prev = intervals[intervals.length - 1]; if (prev && s === prev[1]) prev[1] = s + 1; else intervals.push([s, s + 1]); });
    window.CN.api(url, { json: { intervals, position: video.currentTime, duration: isFinite(video.duration) ? video.duration : null },
      keepalive: !!keepalive }).then(render).catch(() => secs.forEach((s) => pending.add(s)));
  }
  setInterval(() => { if (!video.paused) flush(); }, 15000);
  video.addEventListener("pause", () => flush());
  video.addEventListener("ended", () => flush());
  document.addEventListener("visibilitychange", () => { if (document.hidden) flush(true); });
  window.addEventListener("pagehide", () => flush(true));

  const resume = Number(video.dataset.resume || 0);
  if (resume > 5) {
    video.addEventListener("loadedmetadata", () => { if (!isFinite(video.duration) || resume < video.duration - 5) video.currentTime = resume; }, { once: true });
  }

  /* Capture the frame at the current timestamp for the AI doubt solver. */
  window.CN.captureFrame = function () {
    const time = video.currentTime || 0;
    if (!video.videoWidth) return { image: null, time };
    const scale = Math.min(1, 1280 / video.videoWidth);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    try {
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      return { image: canvas.toDataURL("image/jpeg", 0.82), time };
    } catch (e) {
      return { image: null, time };   // cross-origin video: send the timestamp only
    }
  };
  window.CN.pauseVideo = () => video.pause();
})();
