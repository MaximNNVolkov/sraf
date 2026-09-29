#!/usr/bin/env node
/*
 * Проверка компоновки слайдов (холст 1920×1080).
 *
 *   node .claude/skills/business-slides/scripts/check_layout.mjs <file.html> [--out <dir>]
 *
 * Для каждого <section class="slide"> отключает анимации, рендерит слайд
 * и проверяет:
 *   - контент не выходит за безопасную зону и не обрезан;
 *   - текстовые блоки не наезжают друг на друга;
 *   - контент занимает всю ширину, а не одну сторону слайда;
 *   - по вертикали нет большой пустоты;
 *   - шрифт не мельче 20px.
 * Скриншоты слайдов сохраняются в --out (по умолчанию ./slide-check).
 * Код выхода 1, если найдены ошибки.
 */
import { existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  console.error("Не найден пакет playwright. Установите: npm install (в корне репозитория) или npm i -D playwright");
  process.exit(2);
}

const args = process.argv.slice(2);
const file = args.find((a) => !a.startsWith("--"));
const outIdx = args.indexOf("--out");
const outDir = resolve(outIdx >= 0 ? args[outIdx + 1] : "slide-check");
if (!file || !existsSync(file)) {
  console.error("Использование: check_layout.mjs <file.html> [--out <dir>]");
  process.exit(2);
}
mkdirSync(outDir, { recursive: true });

const executablePath =
  process.env.CHROME_PATH || (existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);
const browser = await chromium.launch(executablePath ? { executablePath } : {});
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
await page.goto(pathToFileURL(resolve(file)).href);
await page.addStyleTag({
  content: `.a, .slide.active .a { animation: none !important; opacity: 1 !important; transform: none !important; stroke-dashoffset: 0 !important; }
            .slide { transition: none !important; }
            .deck-nav, .progress-line { display: none !important; }`,
});
await page.evaluate(() => document.fonts.ready);

const count = await page.locator(".slide").count();
if (!count) {
  console.error("В файле нет элементов .slide");
  process.exit(2);
}

let errors = 0;
let warnings = 0;

for (let i = 0; i < count; i++) {
  const report = await page.evaluate(measure, i);
  await page.screenshot({ path: `${outDir}/slide-${String(i + 1).padStart(2, "0")}.png` });

  console.log(`\nСлайд ${i + 1}${report.label ? ` — ${report.label}` : ""}`);
  console.log(
    `  ширина контента: ${pct(report.stats.widthShare)}, заполнение лево/право: ${pct(report.stats.leftShare)} / ${pct(1 - report.stats.leftShare)}` +
      (report.stats.fillY != null ? `, заполнение по вертикали: ${pct(report.stats.fillY)}` : "")
  );
  for (const e of report.errors) console.log(`  ✖ ${e}`);
  for (const w of report.warnings) console.log(`  ⚠ ${w}`);
  if (!report.errors.length && !report.warnings.length) console.log("  ✔ без замечаний");
  errors += report.errors.length;
  warnings += report.warnings.length;
}

await browser.close();
console.log(`\nИтого: ошибок ${errors}, предупреждений ${warnings}. Скриншоты: ${outDir}`);
process.exit(errors ? 1 : 0);

function pct(x) {
  return Math.round(x * 100) + "%";
}

/* ---------- Выполняется в браузере ---------- */
function measure(index) {
  const W = 1920, H = 1080;
  const SAFE = { l: 80, r: W - 80, t: 40, b: H - 36 };
  const SAFE_W = 1680; // ширина рабочей области контентного слайда
  const DECO = ".blob, .corner-deco, .title-grid, .node-pulse, [data-deco]";
  const BOX = ".card, .kpi, .thesis, .step-item, .visual, .takeaway, .table-wrap, img, svg, canvas, video";

  const slides = [...document.querySelectorAll(".slide")];
  slides.forEach((s) => s.classList.remove("active"));
  const slide = slides[index];
  slide.classList.add("active");
  const origin = slide.getBoundingClientRect();

  const errors = [];
  const warnings = [];
  const rel = (r) => ({ l: r.left - origin.left, t: r.top - origin.top, r: r.right - origin.left, b: r.bottom - origin.top });
  const visible = (el) => {
    const cs = getComputedStyle(el);
    return cs.display !== "none" && cs.visibility !== "hidden" && parseFloat(cs.opacity) > 0;
  };
  const describe = (el) => {
    const text = (el.textContent || "").trim().replace(/\s+/g, " ");
    return `<${el.tagName.toLowerCase()}${el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/)[0] : ""}>` +
      (text ? ` «${text.slice(0, 40)}${text.length > 40 ? "…" : ""}»` : "");
  };
  const isDeco = (el) => !!el.closest(DECO);
  const inSvg = (el) => !!el.parentElement?.closest("svg");

  // Атомы: фрагменты текста (точные прямоугольники строк) и визуальные блоки
  const texts = [];
  const boxes = [];
  for (const el of slide.querySelectorAll("*")) {
    if (isDeco(el) || inSvg(el) || !visible(el)) continue;
    if (el.matches(BOX)) {
      const r = rel(el.getBoundingClientRect());
      if (r.r - r.l > 0 && r.b - r.t > 0) boxes.push({ el, r });
    }
    const rects = [];
    for (const node of el.childNodes) {
      if (node.nodeType !== 3 || !node.textContent.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const cr of range.getClientRects()) if (cr.width > 1 && cr.height > 1) rects.push(rel(cr));
    }
    if (rects.length) {
      texts.push({ el, rects });
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs < 20) warnings.push(`мелкий шрифт ${fs}px: ${describe(el)}`);
    }
  }

  // 1. Выход за безопасную зону
  const all = [...texts.flatMap((t) => t.rects.map((r) => ({ el: t.el, r }))), ...boxes];
  for (const { el, r } of all) {
    if (r.l < SAFE.l - 1 || r.r > SAFE.r + 1 || r.t < SAFE.t - 1 || r.b > SAFE.b + 1) {
      errors.push(`выходит за край слайда (x ${Math.round(r.l)}–${Math.round(r.r)}, y ${Math.round(r.t)}–${Math.round(r.b)}): ${describe(el)}`);
    }
  }

  // 2. Обрезанный контент
  for (const el of slide.querySelectorAll("*")) {
    if (isDeco(el) || inSvg(el) || !visible(el)) continue;
    const cs = getComputedStyle(el);
    if (cs.overflow === "visible" && cs.overflowX === "visible" && cs.overflowY === "visible") continue;
    if (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2) {
      errors.push(`содержимое обрезано: ${describe(el)}`);
    }
  }

  // 3. Наложение текстов
  const seen = new Set();
  for (let a = 0; a < texts.length; a++) {
    for (let b = a + 1; b < texts.length; b++) {
      const A = texts[a], B = texts[b];
      if (A.el.contains(B.el) || B.el.contains(A.el)) continue;
      const hit = A.rects.some((ra) => B.rects.some((rb) =>
        Math.min(ra.r, rb.r) - Math.max(ra.l, rb.l) > 4 && Math.min(ra.b, rb.b) - Math.max(ra.t, rb.t) > 4));
      const key = describe(A.el) + describe(B.el);
      if (hit && !seen.has(key)) {
        seen.add(key);
        errors.push(`текст наезжает на другой текст: ${describe(A.el)} и ${describe(B.el)}`);
      }
    }
  }

  // 4. Баланс: область анализа — .body, если есть, иначе весь слайд без футера
  const body = slide.querySelector(".body");
  const footer = slide.querySelector(".slide-footer");
  const scope = (el) => (body ? body.contains(el) : !(footer && footer.contains(el)));
  const mass = [
    ...texts.filter((t) => scope(t.el)).flatMap((t) => t.rects),
    ...boxes.filter((b) => scope(b.el)).map((b) => b.r),
  ];

  const stats = { widthShare: 0, leftShare: 0.5, fillY: null };
  if (mass.length) {
    const minX = Math.min(...mass.map((r) => r.l));
    const maxX = Math.max(...mass.map((r) => r.r));
    const minY = Math.min(...mass.map((r) => r.t));
    const maxY = Math.max(...mass.map((r) => r.b));
    stats.widthShare = Math.min(1, (maxX - minX) / SAFE_W);

    // Баланс по покрытию: какую часть каждой половины рабочей области
    // занимают проекции блоков на ось X (площадь не учитывается, чтобы
    // крупная панель не «перевешивала» колонку текста)
    const spans = mass.map((r) => [r.l, r.r]).sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const [l, r] of spans) {
      const last = merged[merged.length - 1];
      if (last && l <= last[1]) last[1] = Math.max(last[1], r);
      else merged.push([l, r]);
    }
    const cover = (from, to) => merged.reduce((sum, [l, r]) => sum + Math.max(0, Math.min(r, to) - Math.max(l, from)), 0);
    const half = SAFE_W / 2;
    const left = cover(W / 2 - half, W / 2);
    const right = cover(W / 2, W / 2 + half);
    stats.leftShare = left + right ? left / (left + right) : 0.5;

    if (stats.widthShare < 0.75) {
      errors.push(`контент занимает только ${Math.round(stats.widthShare * 100)}% ширины рабочей области — растяните на всю ширину (сетка .grid-*, .split) или добавьте визуальный блок`);
    }
    const side = stats.leftShare > 0.5 ? "влево" : "вправо";
    const dominant = Math.max(stats.leftShare, 1 - stats.leftShare);
    if (dominant > 0.75) errors.push(`контент смещён ${side}: ${Math.round(dominant * 100)}% заполненной ширины приходится на одну половину слайда`);
    else if (dominant > 0.67) warnings.push(`контент заметно смещён ${side}: ${Math.round(dominant * 100)}% заполненной ширины на одной половине`);

    if (body) {
      const br = rel(body.getBoundingClientRect());
      const padTop = parseFloat(getComputedStyle(body).paddingTop) || 0;
      const avail = br.b - br.t - padTop;
      stats.fillY = Math.min(1, (maxY - minY) / avail);
      if (maxY - minY > avail + 2) errors.push("контент не помещается по высоте — сократите текст или разбейте на два слайда");
      else if (stats.fillY < 0.5) warnings.push(`контент занимает ${Math.round(stats.fillY * 100)}% высоты — увеличьте элементы, шрифт или добавьте блок`);
    }
  } else {
    warnings.push("не найден контент для анализа баланса");
  }

  return { label: slide.getAttribute("aria-label") || "", errors, warnings, stats };
}
