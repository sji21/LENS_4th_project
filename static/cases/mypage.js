(() => {
  document.querySelectorAll("[data-room-row]").forEach((row) => {
    const edit = row.querySelector("[data-room-edit]");
    const form = row.querySelector("[data-room-form]");
    const roomLink = row.querySelector("a");
    const cancel = row.querySelector("[data-room-cancel]");
    if (!edit || !form || !roomLink || !cancel) return;
    edit.addEventListener("click", () => {
      roomLink.hidden = true;
      edit.hidden = true;
      form.hidden = false;
      const title = form.querySelector('input[name="title"]');
      title.focus();
      title.select();
    });
    cancel.addEventListener("click", () => {
      form.hidden = true;
      roomLink.hidden = false;
      edit.hidden = false;
    });
  });
  const grid = document.getElementById("calendar-grid");
  const label = document.getElementById("calendar-month");
  if (!grid || !label) return;
  const pad = (number) => String(number).padStart(2, "0");
  const keyFor = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const events = new Map();
  document.querySelectorAll("[data-calendar-event]").forEach((node) => {
    const event = {title: node.dataset.title || "일정",time: node.dataset.time || "",status: node.dataset.status || "candidate",description: node.dataset.description || "",room: node.dataset.room || "채팅방",color: node.dataset.color || "0"};
    const date = node.dataset.date;
    if (!events.has(date)) events.set(date, []);
    events.get(date).push(event);
  });
  const today = new Date();
  let visibleMonth = new Date(today.getFullYear(), today.getMonth(), 1);

  function render() {
    grid.replaceChildren();
    label.textContent = `${visibleMonth.getFullYear()}년 ${visibleMonth.getMonth() + 1}월`;
    const first = new Date(visibleMonth.getFullYear(), visibleMonth.getMonth(), 1);
    const start = new Date(first);
    start.setDate(first.getDate() - first.getDay());
    for (let index = 0; index < 42; index += 1) {
      const date = new Date(start);
      date.setDate(start.getDate() + index);
      const dateKey = keyFor(date);
      const day = document.createElement("div");
      day.className = "calendar-day";
      day.setAttribute("role", "gridcell");
      day.setAttribute("aria-label", `${date.getFullYear()}년 ${date.getMonth() + 1}월 ${date.getDate()}일`);
      if (date.getMonth() !== visibleMonth.getMonth()) day.classList.add("outside");
      if (dateKey === keyFor(today)) day.classList.add("today");
      const number = document.createElement("span");
      number.className = "day-number";
      number.textContent = date.getDate();
      day.append(number);
      const items = events.get(dateKey) || [];
      items.slice(0, 2).forEach((item) => {
        const chip = document.createElement("span");
        chip.className = `calendar-event ${item.status} room-color-${item.color}`;
        chip.textContent = `${item.time === "00:00" ? "" : `${item.time} `}${item.title}`;
        chip.title = item.description ? `${item.room} · ${item.title} · ${item.description}` : `${item.room} · ${item.title}`;
        day.append(chip);
      });
      if (items.length > 2) {
        const more = document.createElement("span");
        more.className = "calendar-more";
        more.textContent = `+${items.length - 2}개`;
        day.append(more);
      }
      grid.append(day);
    }
  }
  document.getElementById("calendar-prev")?.addEventListener("click", () => {visibleMonth = new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() - 1, 1);render();});
  document.getElementById("calendar-next")?.addEventListener("click", () => {visibleMonth = new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() + 1, 1);render();});
  render();
})();
