// Собирает один автономный HTML: встраивает шрифты (base64), чтобы файл открывался без интернета.
// Запуск: node kino/build.mjs  →  kino/strategic-session-kino.html и kino/template-kino.html (чистый шаблон)
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const CYR = "U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116";
const LAT = "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD";

const face = (family, weights, file, range) => {
  const b64 = readFileSync(join(here, "fonts", file)).toString("base64");
  return `@font-face{font-family:'${family}';font-style:normal;font-weight:${weights};font-display:block;` +
    `src:url(data:font/woff2;base64,${b64}) format('woff2');unicode-range:${range}}`;
};

const fonts = [
  face("Oswald", "200 700", "oswald-cyrillic-wght-normal.woff2", CYR),
  face("Oswald", "200 700", "oswald-latin-wght-normal.woff2", LAT),
  face("Manrope", "200 800", "manrope-cyrillic-wght-normal.woff2", CYR),
  face("Manrope", "200 800", "manrope-latin-wght-normal.woff2", LAT),
].join("\n");

const html = readFileSync(join(here, "src", "deck.html"), "utf8").replace("/*@@FONTS@@*/", fonts);
const write = (name, text) => {
  const out = join(here, name);
  writeFileSync(out, text);
  console.log(`${out} — ${(text.length / 1024).toFixed(0)} КБ`);
};
write("strategic-session-kino.html", html);

// Чистый шаблон: тот же движок, титульный слайд и один пустой слайд-конструктор
const OPEN = '<div id="stage">', CLOSE = '</div>\n\n<div id="grain"';
const a = html.indexOf(OPEN) + OPEN.length, b = html.indexOf(CLOSE);
if (a < OPEN.length || b < 0) throw new Error("не найдены границы слайдов в deck.html");
const slides = readFileSync(join(here, "src", "template.html"), "utf8");
write("template-kino.html", (html.slice(0, a) + slides + html.slice(b)).replace(/<title>.*?<\/title>/, "<title>Презентация</title>"));
