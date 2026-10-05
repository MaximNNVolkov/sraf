// Сборка презентации «Стратегическая сессия Домклик ВВБ» (стиль «Хлопушка»).
// Запуск: node build.js  →  strategic-session-domclick-vvb.pptx
const pptxgen = require("pptxgenjs");
const sharp = require("sharp");
const fs = require("fs");
const path = require("path");

const OUT = path.join(__dirname, "strategic-session-domclick-vvb.pptx");
const ASSETS = path.join(__dirname, "assets");

// Палитра
const K = {
  bg: "0A0D0B", card: "131A15", photo: "1A241D", rule: "2A372E", head: "18221B",
  text: "F2F0EA", soft: "B9C2BA", muted: "8A958D", icon: "3E5246",
  green: "2BD36A", amber: "F5B544",
  amberCard: "221A0E", amberDeep: "1E180C", amberSoft: "E5D9C0", amberLight: "F7DDA8",
  rank1: "4A3A14", rank2: "1B4A2C", rank3: "1F2A22", greenDeep: "10261A", greenLight: "BDF0CF",
};

// Холст 13.333" × 7.5"
const SW = 13.333, SH = 7.5, M = 0.89, W = SW - 2 * M;
const FOOTER = "Стратегическая сессия · Домклик ВВБ";

// ---------- фоны и полосы хлопушки ----------
function radial(cx, cy, r, stops) {
  const s = stops.map(([o, c]) => `<stop offset="${o}" stop-color="#${c}"/>`).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080">
<defs><radialGradient id="g" cx="${cx}" cy="${cy}" r="${r}" gradientUnits="userSpaceOnUse">${s}</radialGradient></defs>
<rect width="1920" height="1080" fill="#${K.bg}"/><rect width="1920" height="1080" fill="url(#g)"/></svg>`;
}
function band(h, stripe, line) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="${h + line}">
<defs><pattern id="p" width="${stripe * 2}" height="${stripe * 2}" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
<rect width="${stripe * 2}" height="${stripe * 2}" fill="#${K.bg}"/><rect width="${stripe}" height="${stripe * 2}" fill="#${K.text}"/></pattern></defs>
<rect width="1920" height="${h}" fill="url(#p)"/><rect y="${h}" width="1920" height="${line}" fill="#${K.green}"/></svg>`;
}
const IMAGES = {
  top: radial(960, 0, 1250, [[0, "1A271F"], [1, K.bg]]),
  cover: radial(576, 378, 1300, [[0, "23332A"], [0.45, "121A15"], [1, K.bg]]),
  section: radial(1500, 540, 1250, [[0, "23332A"], [0.45, "121A15"], [1, K.bg]]),
  amber: radial(960, 0, 1350, [[0, "3A2C12"], [0.5, K.amberDeep], [1, "0D0B07"]]),
  kino: radial(960, 540, 1100, [[0, "2A2410"], [0.5, "14130C"], [1, K.bg]]),
  bottom: radial(960, 1080, 1250, [[0, "1C2A21"], [0.5, "121A15"], [1, K.bg]]),
  band: band(40, 40, 6),
  bandWide: band(72, 56, 8),
};
async function renderImages() {
  fs.mkdirSync(ASSETS, { recursive: true });
  const out = {};
  for (const [name, svg] of Object.entries(IMAGES)) {
    const file = path.join(ASSETS, `${name}.png`);
    await sharp(Buffer.from(svg)).png().toFile(file);
    out[name] = file;
  }
  return out;
}

// ---------- помощники ----------
let pres;
function txt(slide, text, o) {
  slide.addText(text, {
    isTextBox: true, margin: 0, fontFace: "Arial", color: K.text, valign: "top",
    ...o,
  });
}
function eyebrow(slide, text, o) {
  txt(slide, text.toUpperCase(), { fontSize: 14, bold: true, color: K.green, charSpacing: 4, ...o });
}
function card(slide, x, y, w, h, line, fill = K.card, name) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h, rectRadius: 0.16, fill: { color: fill }, line: { color: line, width: 1.5 }, objectName: name,
  });
}
// Рамка под фото: фигура, в которую вставляется рисунок (Формат фигуры → Заливка → Рисунок)
function photo(slide, x, y, w, h, label, round = false) {
  slide.addText(label, {
    shape: round ? pres.shapes.OVAL : pres.shapes.ROUNDED_RECTANGLE,
    x, y, w, h, rectRadius: 0.14, margin: 0,
    fill: { color: K.photo }, line: { color: K.green, width: 1.5 },
    fontFace: "Arial", fontSize: 12, color: K.muted, align: "center", valign: "middle",
    objectName: "Фото",
  });
}

function defineLayouts(img) {
  const footer = [
    { text: { text: FOOTER, options: { x: M, y: 6.72, w: 8, h: 0.3, fontFace: "Arial", fontSize: 12, color: K.muted, margin: 0 } } },
  ];
  const slideNum = { x: SW - M - 1, y: 6.72, w: 1, h: 0.3, fontFace: "Arial", fontSize: 12, color: K.muted, align: "right" };
  const thinBand = { image: { x: 0, y: 0, w: SW, h: SW * (46 / 1920), path: img.band } };
  const ph = (name, type, o) => ({
    placeholder: { options: { name, type, margin: 0, fontFace: "Arial", valign: "top", align: "left", ...o }, text: "" },
  });
  const eyebrowPh = (y, extra = {}) =>
    ph("eyebrow", "body", { x: M, y, w: W, h: 0.35, fontSize: 14, bold: true, color: K.green, charSpacing: 4, ...extra });

  pres.defineSlideMaster({
    title: "Обложка",
    background: { path: img.cover },
    objects: [{ image: { x: 0, y: 0, w: SW, h: SW * (80 / 1920), path: img.bandWide } }],
  });
  pres.defineSlideMaster({
    title: "Раздел",
    background: { path: img.section },
    objects: [
      thinBand,
      eyebrowPh(2.4, { w: 7 }),
      ph("title", "title", { x: M, y: 2.85, w: 7, h: 1.05, fontSize: 48, bold: true, color: K.text }),
      ph("sub", "body", { x: M, y: 4.15, w: 7, h: 0.8, fontSize: 18, color: K.soft }),
    ],
  });
  pres.defineSlideMaster({
    title: "Заголовок и контент",
    background: { path: img.top },
    objects: [
      thinBand, ...footer,
      eyebrowPh(0.89),
      ph("title", "title", { x: M, y: 1.25, w: W, h: 0.7, fontSize: 36, bold: true, color: K.text }),
    ],
    slideNumber: slideNum,
  });
  pres.defineSlideMaster({
    title: "Спикер",
    background: { path: img.top },
    objects: [
      thinBand, ...footer,
      eyebrowPh(2.2, { w: 5.7 }),
      ph("title", "title", { x: M, y: 2.65, w: 5.7, h: 1.6, fontSize: 40, bold: true, color: K.text }),
      ph("sub", "body", { x: M, y: 4.45, w: 5.7, h: 0.5, fontSize: 18, color: K.soft }),
    ],
    slideNumber: slideNum,
  });
  pres.defineSlideMaster({
    title: "Акцент",
    background: { path: img.amber },
    objects: [
      thinBand,
      { text: { text: FOOTER, options: { x: M, y: 6.72, w: 8, h: 0.3, fontFace: "Arial", fontSize: 12, color: "A89F8A", margin: 0 } } },
      eyebrowPh(0.89),
      ph("title", "title", { x: M, y: 1.25, w: W, h: 1.4, fontSize: 40, bold: true, color: K.text }),
    ],
    slideNumber: { ...slideNum, color: "A89F8A" },
  });
  pres.defineSlideMaster({ title: "Финал", background: { path: img.kino }, objects: [thinBand] });
  pres.defineSlideMaster({ title: "Цифры", background: { path: img.bottom }, objects: [thinBand] });
}

function add(layout, section) {
  return pres.addSlide({ masterName: layout, sectionTitle: section });
}
const S1 = "Награждение", S2 = "Драйверы и практики", S3 = "Куда идём в 4 квартале", S4 = "Командная работа";

// ---------- слайды ----------
function cover() {
  pres.addSection({ title: "Обложка" });
  const s = add("Обложка", "Обложка");
  eyebrow(s, "Мотор! · Домклик ВВБ", { x: M, y: 1.9, w: W, h: 0.35, charSpacing: 6 });
  txt(s, "Стратегическая сессия руководителей", { x: M, y: 2.4, w: 10.5, h: 1.95, fontSize: 56, bold: true, lineSpacingMultiple: 0.95 });
  const y = 4.75, h = 1.05;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: M, y, w: W, h, rectRadius: 0.06, fill: { type: "none" }, line: { color: K.text, width: 1.5 }, objectName: "Хлопушка" });
  const cells = [["Продакшн", "Домклик", K.text], ["Площадка", "Волго-Вятский банк", K.text], ["Дубль", "4 квартал", K.green]];
  cells.forEach(([label, value, color], i) => {
    const x = M + (W / 3) * i;
    if (i) s.addShape(pres.shapes.LINE, { x, y, w: 0, h, line: { color: K.text, width: 1.5 } });
    txt(s, label.toUpperCase(), { x: x + 0.25, y: y + 0.18, w: W / 3 - 0.5, h: 0.3, fontSize: 12, color: "9FAAA2", charSpacing: 3 });
    txt(s, value, { x: x + 0.25, y: y + 0.5, w: W / 3 - 0.5, h: 0.4, fontSize: 18, bold: true, color });
  });
}

function sectionSlide(section, num, eyebrowText, title, sub) {
  pres.addSection({ title: section });
  const s = add("Раздел", section);
  txt(s, num, { x: 7.4, y: 1.75, w: 5.04, h: 3.2, fontSize: 180, bold: true, align: "right", color: K.green, transparency: 75 });
  s.addText(eyebrowText.toUpperCase(), { placeholder: "eyebrow" });
  s.addText(title, { placeholder: "title" });
  s.addText(sub, { placeholder: "sub" });
  return s;
}

function threeCards(s, items, photoLabel) {
  const gap = 0.28, w = (W - 2 * gap) / 3, y = 2.4, h = 3.95;
  items.forEach(([label, name, extra], i) => {
    const x = M + i * (w + gap);
    card(s, x, y, w, h, K.green);
    photo(s, x + 0.22, y + 0.22, w - 0.44, 2.1, photoLabel);
    txt(s, label, { x: x + 0.22, y: y + 2.5, w: w - 0.44, h: 0.32, fontSize: 14, bold: true, color: K.amber });
    txt(s, name, { x: x + 0.22, y: y + 2.88, w: w - 0.44, h: 0.42, fontSize: 18, bold: true });
    if (extra) txt(s, extra, { x: x + 0.22, y: y + 3.33, w: w - 0.44, h: 0.32, fontSize: 14, color: K.soft });
  });
}

function ekp() {
  const s = add("Заголовок и контент", S1);
  s.addText("НАГРАЖДЕНИЕ · ЕКП", { placeholder: "eyebrow" });
  s.addText("Первые места в своих кластерах", { placeholder: "title" });
  threeCards(s, [
    ["#1 в кластере", "Банк Татарстан"],
    ["#1 в кластере", "Мордовское ГОСБ"],
    ["#1 в кластере", "ГОСБ Марий Эл"],
  ], "Фото руководителя");
}

function kpe() {
  const s = add("Заголовок и контент", S1);
  s.addText("НАГРАЖДЕНИЕ · КПЭ", { placeholder: "eyebrow" });
  s.addText("ТОП-3 ГОСБ по выполнению показателей", { placeholder: "title" });
  const gap = 0.22, w = (W - 3 * gap) / 4, base = 6.3;
  const cols = [
    ["Мордовское ГОСБ", "2", 1.3, K.rank2, K.green, K.green, 32],
    ["Кировское ГОСБ", "1", 1.8, K.rank1, K.amber, K.amber, 40],
    ["ГОСБ Марий Эл", "1", 1.8, K.rank1, K.amber, K.amber, 40],
    ["Чувашское ГОСБ", "3", 0.9, K.rank3, K.muted, K.soft, 26],
  ];
  cols.forEach(([name, rank, bh, fill, line, numColor, size], i) => {
    const x = M + i * (w + gap), top = base - bh;
    s.addText([
      { text: rank, options: { fontSize: size, bold: true, color: numColor, breakLine: true } },
      { text: "ранг", options: { fontSize: 12, color: K.text } },
    ], { shape: pres.shapes.RECTANGLE, x, y: top, w, h: bh, fill: { color: fill }, line: { color: line, width: 1.5 },
      align: "center", valign: "middle", margin: 0, fontFace: "Arial", objectName: `Ранг ${rank}` });
    txt(s, name, { x, y: top - 0.5, w, h: 0.4, fontSize: 16, bold: true, align: "center" });
    photo(s, x + (w - 1.0) / 2, top - 1.62, 1.0, 1.0, "Фото", true);
  });
}

function best() {
  const s = add("Заголовок и контент", S1);
  s.addText("НАГРАЖДЕНИЕ · СОТРУДНИКИ", { placeholder: "eyebrow" });
  s.addText("Лучшие сотрудники", { placeholder: "title" });
  threeCards(s, [
    ["МРП", "Алексей Кокель", "Чувашское ГОСБ"],
    ["РОДК", "[Имя Фамилия]", "[ГОСБ]"],
    ["КМ ДК", "[Имя Фамилия]", "[ГОСБ]"],
  ], "Фото сотрудника");
}

function speakerBlock(s, x, y, w, h, name) {
  photo(s, x, y, w, h, "Фото спикера");
  txt(s, "СПИКЕР", { x, y: y + h + 0.15, w, h: 0.28, fontSize: 12, bold: true, color: K.green, charSpacing: 4 });
  txt(s, name, { x, y: y + h + 0.48, w, h: 0.75, fontSize: 18, bold: true });
}
function speakerSlide(section, eyebrowText, title, sub, name) {
  const s = add("Спикер", section);
  s.addText(eyebrowText.toUpperCase(), { placeholder: "eyebrow" });
  s.addText(title, { placeholder: "title" });
  if (sub) s.addText(sub, { placeholder: "sub" });
  if (name) speakerBlock(s, SW - M - 3.3, 0.95, 3.3, 3.85, name);
  return s;
}

function neipoteka() {
  const s = add("Заголовок и контент", S2);
  s.addText("ЧАСТЬ 2 · НЕИПОТЕКА", { placeholder: "eyebrow" });
  s.addText("Драйверы 4 квартала", { placeholder: "title" });
  txt(s, "Сколько нам даст каждый драйвер", { x: M, y: 2.08, w: W, h: 0.35, fontSize: 16, color: K.soft });
  const gap = 0.28, w = (W - 2 * gap) / 3, y = 2.65, h = 2.8;
  ["Прирост аккредитивов и СБР", "Увеличение среднего чека", "Рост проникновения СсГ"].forEach((name, i) => {
    const x = M + i * (w + gap);
    card(s, x, y, w, h, K.green);
    txt(s, `ДРАЙВЕР ${i + 1}`, { x: x + 0.28, y: y + 0.28, w: w - 0.56, h: 0.3, fontSize: 12, bold: true, color: K.green, charSpacing: 3 });
    txt(s, name, { x: x + 0.28, y: y + 0.65, w: w - 0.56, h: 0.75, fontSize: 18, bold: true });
    txt(s, "+[__]", { x: x + 0.28, y: y + 1.45, w: w - 0.56, h: 0.7, fontSize: 40, bold: true, color: K.amber });
    txt(s, "вклад в неипотеку", { x: x + 0.28, y: y + 2.2, w: w - 0.56, h: 0.3, fontSize: 12, color: K.soft });
  });
  s.addShape(pres.shapes.LINE, { x: M, y: 5.7, w: W, h: 0, line: { color: K.rule, width: 1.5 } });
  txt(s, "Итого потенциал", { x: M, y: 5.85, w: 6, h: 0.45, fontSize: 18, bold: true, valign: "middle" });
  txt(s, "+[__]", { x: SW - M - 3, y: 5.82, w: 3, h: 0.5, fontSize: 26, bold: true, color: K.green, align: "right", valign: "middle" });
}

function tatarstan() {
  const s = speakerSlide(S2, "Часть 2 · Неипотека", "Лучшие практики Татарстана", null, null);
  [["Что помогло сделать неипотеку", K.green], ["Что поменяли", K.amber]].forEach(([t, c], i) => {
    s.addText(t, { shape: pres.shapes.RECTANGLE, x: M, y: 4.45 + i * 0.7, w: 5.4, h: 0.55, fill: { color: K.card },
      line: { color: c, width: 1.5 }, margin: 10, fontFace: "Arial", fontSize: 16, color: K.text, valign: "middle" });
  });
  const w = 2.6, gap = 0.3, x0 = SW - M - (2 * w + gap);
  speakerBlock(s, x0, 0.95, w, 3.3, "Наталья Шереметьева");
  speakerBlock(s, x0 + w + gap, 0.95, w, 3.3, "Арина Кирюшина");
}

function s3() {
  pres.addSection({ title: S3 });
  const s = add("Спикер", S3);
  txt(s, "03", { x: M, y: 0.8, w: 3, h: 1.3, fontSize: 88, bold: true, color: K.green, transparency: 60 });
  s.addText("ЧАСТЬ 3", { placeholder: "eyebrow" });
  s.addText("Куда пойдём в 4 квартале", { placeholder: "title" });
  txt(s, "ТОП 1 или ТОП 3 / 33%", { x: M, y: 4.45, w: 5.7, h: 0.5, fontSize: 24, bold: true, color: K.amber });
  speakerBlock(s, SW - M - 3.3, 0.95, 3.3, 3.85, "Петр Пеньковский");
}

function q3() {
  const s = add("Заголовок и контент", S3);
  s.addText("ЧАСТЬ 3 · ИТОГИ", { placeholder: "eyebrow" });
  s.addText("Итоги 3 квартала", { placeholder: "title" });
  const gap = 0.17, w = (W - 4 * gap) / 5, y = 2.55, h = 3.55;
  [["ЕКП", "ТОП 1", K.green], ["КПЭ", "ТОП 1", K.green], ["Выдача ЖК", "ТОП 1", K.green],
   ["Динамика ОД", "5 ранг", K.rule], ["Доп. ОД", "142%", K.amber]].forEach(([label, value, line], i) => {
    const x = M + i * (w + gap);
    card(s, x, y, w, h, line);
    txt(s, label, { x: x + 0.25, y: y + 0.3, w: w - 0.5, h: 0.35, fontSize: 14, bold: true, color: K.soft });
    txt(s, value, { x: x + 0.25, y: y + h - 0.9, w: w - 0.5, h: 0.6, fontSize: 26, bold: true,
      color: line === K.rule ? K.text : line });
  });
}

function gaps() {
  const s = add("Заголовок и контент", S3);
  s.addText("ЧАСТЬ 3 · ИТОГИ 3 КВАРТАЛА", { placeholder: "eyebrow" });
  s.addText("ТБ — ТОП 3, ГОСБ — ТОП 33%", { placeholder: "title" });
  const head = (t, align = "left") => ({ text: t, options: { bold: true, color: K.soft, fill: { color: K.head }, align } });
  const rows = [
    ["Доля высокомаржинальных продуктов", "7 ранг", "3 ранг", false],
    ["Проникновение СБР + Аккредитив", "9 ранг", "3 ранг", false],
    ["Кроссы на сделку", "3 ранг", "1 ранг", false],
    ["Эффективность задач", "46%", "52%", false],
    ["Проникновение СсГ", "3,8%", "10%", false],
    ["Проникновение РПС", "67%", "60%", true],
  ];
  const data = [[head("Показатель"), head("Факт", "right"), head("Цель", "right"), head("Итог", "right")]];
  rows.forEach(([name, fact, goal, ok], i) => {
    const fill = { color: i % 2 ? K.card : K.bg };
    const c = ok ? K.green : K.amber;
    data.push([
      { text: name, options: { fill } },
      { text: fact, options: { fill, color: c, align: "right", bold: true } },
      { text: goal, options: { fill, align: "right" } },
      { text: ok ? "выше цели" : "ниже цели", options: { fill, color: c, align: "right" } },
    ]);
  });
  s.addTable(data, {
    x: M, y: 2.45, w: W, colW: [5.3, 2.085, 2.085, 2.086], rowH: 0.52,
    fontFace: "Arial", fontSize: 16, color: K.text, valign: "middle", margin: 0.12,
    border: { type: "solid", pt: 1, color: K.rule },
  });
}

function forecast() {
  const s = add("Акцент", S3);
  s.addText("ЧАСТЬ 3 · ПРОГНОЗ", { placeholder: "eyebrow" });
  s.addText("Текущее выполнение не приведёт ТБ в ТОП 1", { placeholder: "title" });
  txt(s, "При текущей динамике:", { x: M, y: 3.55, w: W, h: 0.35, fontSize: 16, color: K.amberSoft });
  const gap = 0.17, w = (W - 3 * gap) / 4, y = 4.1, h = 1.9;
  [["ЕКП", "ТОП 3"], ["Динамика ОД", "ТОП 5"], ["Выдача ЖК", "ТОП 3"], ["Доп. ОД", "100%"]].forEach(([label, value], i) => {
    const x = M + i * (w + gap);
    card(s, x, y, w, h, K.amber, K.amberCard);
    txt(s, label, { x: x + 0.25, y: y + 0.3, w: w - 0.5, h: 0.35, fontSize: 14, bold: true, color: K.amberSoft });
    txt(s, value, { x: x + 0.25, y: y + 0.9, w: w - 0.5, h: 0.65, fontSize: 28, bold: true, color: K.amber });
  });
}

function fork() {
  const s = add("Заголовок и контент", S3);
  s.addText("ЧАСТЬ 3 · ВЫБОР", { placeholder: "eyebrow" });
  s.addText("Куда идём?", { placeholder: "title" });
  // точка старта
  s.addText([
    { text: "Мы здесь", options: { fontSize: 12, color: K.soft, breakLine: true } },
    { text: "4 кв.", options: { fontSize: 18, bold: true, color: K.text } },
  ], { shape: pres.shapes.OVAL, x: 1.95, y: 3.35, w: 1.65, h: 1.65, fill: { color: K.card }, line: { color: K.text, width: 2 },
    align: "center", valign: "middle", margin: 0, fontFace: "Arial", objectName: "Старт" });
  // стрелки выбора
  s.addShape(pres.shapes.LINE, { x: 3.75, y: 2.85, w: 3.75, h: 1.32, flipV: true,
    line: { color: K.amber, width: 6, endArrowType: "triangle" }, objectName: "Стрелка: путь 1" });
  s.addShape(pres.shapes.LINE, { x: 3.75, y: 4.17, w: 3.75, h: 1.04,
    line: { color: K.green, width: 6, endArrowType: "triangle" }, objectName: "Стрелка: путь 2" });
  const paths = [
    [2.08, "ПУТЬ 1", "ТБ — ТОП 1", "ГОСБ — ТОП 1", K.amber, K.amberDeep, K.amberLight],
    [4.44, "ПУТЬ 2", "ТБ — ТОП 3", "ГОСБ — ТОП 33%", K.green, K.greenDeep, K.greenLight],
  ];
  paths.forEach(([y, label, main, sub, line, fill, subColor]) => {
    const x = 7.72, w = SW - M - x, h = 1.53;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.16, fill: { color: fill }, line: { color: line, width: 2.25 } });
    txt(s, label, { x: x + 0.3, y: y + 0.2, w: w - 0.6, h: 0.3, fontSize: 12, bold: true, color: line, charSpacing: 4 });
    txt(s, main, { x: x + 0.3, y: y + 0.5, w: w - 0.6, h: 0.5, fontSize: 24, bold: true });
    txt(s, sub, { x: x + 0.3, y: y + 1.03, w: w - 0.6, h: 0.35, fontSize: 16, bold: true, color: subColor });
  });
}

function top1() {
  const s = add("Заголовок и контент", S3);
  s.addText("ЧАСТЬ 3 · ПУТЬ 1", { placeholder: "eyebrow" });
  s.addText("Для ТОП 1 ТБ нужно", { placeholder: "title" });
  const gap = 0.22, w = (W - 3 * gap) / 4;
  const tile = (i, row, label, value, color, span = 1) => {
    const x = M + i * (w + gap), y = row ? 4.3 : 2.4, h = row ? 1.95 : 1.7, ww = w * span + gap * (span - 1);
    card(s, x, y, ww, h, color);
    txt(s, label, { x: x + 0.25, y: y + 0.25, w: ww - 0.5, h: 0.6, fontSize: 14, bold: true, color: K.soft });
    if (value) txt(s, value, { x: x + 0.25, y: y + h - 0.75, w: ww - 0.5, h: 0.5, fontSize: 26, bold: true, color });
    return { x, y, ww };
  };
  tile(0, 0, "ЕКП", "ТОП 1", K.amber);
  tile(1, 0, "ГОСБ в своих кластерах", "ТОП 1", K.amber);
  tile(2, 0, "Кроссы", "ТОП 1", K.amber);
  tile(3, 0, "Выдачи", "ТОП 1", K.amber);
  tile(0, 1, "Проникновение СБР + Аккредитив", "ТОП 33%", K.green);
  tile(1, 1, "Доля РПС", "120%", K.green);
  const { x, y, ww } = tile(2, 1, "Проникновение СсГ", null, K.green, 2);
  txt(s, [
    { text: "не менее 8%", options: { bold: true, color: K.green } },
    { text: " в партнёрских заявках", options: { breakLine: true } },
    { text: "не менее 10%", options: { bold: true, color: K.green } },
    { text: " в ЧМАК и ФЛ" },
  ], { x: x + 0.25, y: y + 0.75, w: ww - 0.5, h: 0.95, fontSize: 18, paraSpaceAfter: 4 });
}

function team() {
  const s = add("Заголовок и контент", S4);
  s.addText("ЧАСТЬ 4 · КОМАНДНАЯ РАБОТА", { placeholder: "eyebrow" });
  s.addText("Что поможет в 4 квартале быть в ТОП", { placeholder: "title" });
  txt(s, "Формат: 1 + 2 + 1 идеи", { x: M, y: 2.08, w: W, h: 0.35, fontSize: 16, color: K.soft });
  const gap = 0.28, w = (W - 2 * gap) / 3, y = 2.65, h = 3.6;
  [["1", "идея для ЦА", null, K.green],
   ["2", "быстрореализуемые идеи на уровне ГОСБ", "то, на что я могу повлиять", K.amber],
   ["1", "идея применения агента в работе", null, K.green]].forEach(([n, t, sub, c], i) => {
    const x = M + i * (w + gap);
    card(s, x, y, w, h, c);
    txt(s, n, { x: x + 0.3, y: y + 0.25, w: 1.5, h: 1.1, fontSize: 66, bold: true, color: c });
    txt(s, t, { x: x + 0.3, y: y + 1.5, w: w - 0.6, h: 1.0, fontSize: 18, bold: true });
    if (sub) txt(s, sub, { x: x + 0.3, y: y + 2.6, w: w - 0.6, h: 0.6, fontSize: 14, color: K.soft });
  });
}

function kino() {
  const s = add("Финал", S4);
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 3.19, y: 1.32, w: 6.95, h: 4.86, rectRadius: 0.28,
    fill: { type: "none" }, line: { color: K.green, width: 2.25, transparency: 50 }, objectName: "Кадр" });
  txt(s, "СТАРТ 2027", { x: M, y: 2.25, w: W, h: 0.4, fontSize: 16, bold: true, color: K.green, align: "center", charSpacing: 6 });
  txt(s, "КИНО", { x: M, y: 2.75, w: W, h: 1.3, fontSize: 66, bold: true, align: "center", valign: "middle" });
  txt(s, "оставляем?", { x: M, y: 4.15, w: W, h: 0.8, fontSize: 34, bold: true, color: K.green, align: "center" });
}

function egrn() {
  const s = add("Цифры", S4);
  const gap = 0.3, w = (W - 2 * gap) / 3;
  [["73%", "Доля ЕГРН", K.green], ["86%", "Доля ЕГРН с ПФ", K.amber], ["60%", "Доля ЕГРН без ПФ", K.text]].forEach(([v, l, c], i) => {
    const x = M + i * (w + gap);
    txt(s, v, { x, y: 2.35, w, h: 1.4, fontSize: 80, bold: true, color: c, align: "center", valign: "middle" });
    s.addShape(pres.shapes.LINE, { x: x + w / 2 - 0.55, y: 3.95, w: 1.1, h: 0, line: { color: c, width: 2 } });
    txt(s, l, { x, y: 4.15, w, h: 0.45, fontSize: 18, bold: true, align: "center" });
  });
}

(async () => {
  const img = await renderImages();
  pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  pres.title = "Стратегическая сессия Домклик ВВБ";
  pres.theme = { headFontFace: "Arial", bodyFontFace: "Arial" };
  defineLayouts(img);

  cover();
  sectionSlide(S1, "01", "Часть 1", "Награждение", "Лучшие территории (ГОСБ) и лучшие сотрудники");
  ekp(); kpe(); best();
  sectionSlide(S2, "02", "Часть 2", "Драйверы и практики", "Доп. ОД, неипотека, опыт Татарстана и Удмуртии");
  speakerSlide(S2, "Часть 2 · Доп. ОД", "Доп. ОД: как считается", null, "Мария Шачкова");
  neipoteka();
  tatarstan();
  speakerSlide(S2, "Часть 2 · РКиСП", "Успех Удмуртского ГОСБ 2025", "РКиСП. Лучшие практики", "Максим Павлов");
  s3(); q3(); gaps(); forecast(); fork(); top1();
  sectionSlide(S4, "04", "Часть 4", "Командная работа", "Идеи на 4 квартал, старт 2027, коллеги КИБ");
  team(); kino();
  speakerSlide(S4, "Часть 4 · Гости", "Коллеги КИБ", null, "Ирина Ягунова");
  egrn();

  await pres.writeFile({ fileName: OUT });
  console.log("written", OUT);
})();
