/*
 * Движок показа слайдов.
 * - масштабирует холст 1920×1080 под окно;
 * - переключает слайды (←/→, PageUp/PageDown, пробел, свайп, кнопки);
 * - перезапускает анимации при входе на слайд (R — повторить на текущем);
 * - F — полноэкранный режим; номер слайда хранится в адресе (#2).
 */
(() => {
  const slides = [...document.querySelectorAll(".slide")];
  if (!slides.length) return;

  const root = document.documentElement;
  let current = 0;

  // Номера страниц в футерах: <span class="page-num" data-page></span>
  slides.forEach((slide, i) => {
    slide.querySelectorAll("[data-page]").forEach((el) => {
      el.textContent = String(i + 1).padStart(2, "0") + " / " + String(slides.length).padStart(2, "0");
    });
  });

  // Навигация и индикатор прогресса
  const nav = document.createElement("div");
  nav.className = "deck-nav";
  nav.innerHTML =
    '<button type="button" data-act="prev" aria-label="Предыдущий слайд">‹</button>' +
    '<span class="counter" aria-live="polite"></span>' +
    '<button type="button" data-act="next" aria-label="Следующий слайд">›</button>' +
    '<button type="button" data-act="replay" aria-label="Повторить анимацию" title="Повторить анимацию (R)">↻</button>' +
    '<button type="button" data-act="full" aria-label="Полный экран" title="Полный экран (F)">⤢</button>';
  const counter = nav.querySelector(".counter");
  const bar = document.createElement("div");
  bar.className = "progress-line";
  document.body.append(nav, bar);

  function fit() {
    const scale = Math.min(innerWidth / 1920, innerHeight / 1080);
    root.style.setProperty("--scale", scale);
  }

  function show(index) {
    current = Math.max(0, Math.min(slides.length - 1, index));
    slides.forEach((s) => s.classList.remove("active"));
    const slide = slides[current];
    void slide.offsetWidth; // перезапуск CSS-анимаций
    slide.classList.add("active");
    counter.textContent = current + 1 + " / " + slides.length;
    bar.style.width = ((current + 1) / slides.length) * 100 + "%";
    history.replaceState(null, "", "#" + (current + 1));
  }

  const actions = {
    next: () => show(current + 1),
    prev: () => show(current - 1),
    replay: () => show(current),
    full: () => {
      if (document.fullscreenElement) document.exitFullscreen();
      else root.requestFullscreen?.();
    },
  };

  nav.addEventListener("click", (e) => {
    const act = e.target.closest("button")?.dataset.act;
    if (act) actions[act]();
  });

  document.addEventListener("keydown", (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.target.closest?.("button") && (e.key === " " || e.key === "Enter")) return;
    switch (e.key) {
      case "ArrowRight": case "ArrowDown": case "PageDown": case " ": actions.next(); break;
      case "ArrowLeft": case "ArrowUp": case "PageUp": actions.prev(); break;
      case "Home": show(0); break;
      case "End": show(slides.length - 1); break;
      case "r": case "R": case "к": case "К": actions.replay(); break;
      case "f": case "F": case "а": case "А": actions.full(); break;
      default: return;
    }
    e.preventDefault();
  });

  let touchX = null;
  document.addEventListener("touchstart", (e) => { touchX = e.touches[0].clientX; }, { passive: true });
  document.addEventListener("touchend", (e) => {
    if (touchX === null) return;
    const dx = e.changedTouches[0].clientX - touchX;
    if (Math.abs(dx) > 50) (dx < 0 ? actions.next : actions.prev)();
    touchX = null;
  });

  let hideTimer;
  document.addEventListener("mousemove", () => {
    document.body.classList.add("show-ui");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => document.body.classList.remove("show-ui"), 2000);
  });

  addEventListener("resize", fit);
  fit();
  show((parseInt(location.hash.slice(1), 10) || 1) - 1);
})();
