/* Holiday-aware calendar (FullCalendar). Admins can drag across days to add a holiday / vacation. */
(function () {
  "use strict";
  const el = document.getElementById("cn-calendar");
  if (!el || !window.FullCalendar) return;
  const selectable = el.dataset.selectable === "1";
  const calendar = new window.FullCalendar.Calendar(el, {
    initialView: window.innerWidth < 768 ? "listWeek" : "dayGridMonth",
    headerToolbar: { left: "prev,next today", center: "title", right: "dayGridMonth,timeGridWeek,listMonth" },
    buttonText: { today: "Today", month: "Month", week: "Week", list: "Agenda" },
    firstDay: 1, height: "auto", nowIndicator: true, dayMaxEvents: 3, navLinks: true,
    eventTimeFormat: { hour: "numeric", minute: "2-digit", meridiem: "short" },
    events: { url: "/api/calendar/events", failure: () => window.CN.toast("Couldn't load the calendar.", "danger") },
    loading: (on) => (on ? window.CN.progress.start() : window.CN.progress.done()),
    eventDidMount(info) {
      const p = info.event.extendedProps || {};
      const parts = [info.event.title, p.batch, p.mode, p.due ? "Due " + p.due : ""].filter(Boolean);
      if (parts.length) info.el.setAttribute("title", parts.join(" · "));
    },
    selectable,
    select(info) {
      const modal = document.getElementById("holiday-modal");
      if (!modal) return;
      const end = new Date(info.end.getTime() - 86400000);
      const iso = (d) => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
      modal.querySelector('[name="start_date"]').value = iso(info.start);
      modal.querySelector('[name="end_date"]').value = iso(end);
      modal.classList.add("open");
      setTimeout(() => modal.querySelector('[name="title"]').focus(), 80);
    },
  });
  calendar.render();
  document.addEventListener("cn:theme", () => calendar.render());
})();
