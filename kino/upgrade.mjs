// Переносит сохранённую копию презентации (с вашими текстами и фото) на новую версию движка:
// берёт слайды из копии, а стили, панель и скрипт — из свежей сборки.
// Запуск: node kino/build.mjs && node kino/upgrade.mjs <копия.html> [результат.html]
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const [src, out = src.replace(/\.html?$/i, "") + "-new.html"] = process.argv.slice(2);
if (!src) { console.error("Укажите файл: node kino/upgrade.mjs копия.html"); process.exit(1); }

const OPEN = '<div id="stage">', CLOSE = '<div id="grain"';
const parts = html => {
  const a = html.indexOf(OPEN), b = html.indexOf(CLOSE);
  if (a < 0 || b < 0) throw new Error("не похоже на файл презентации");
  return [html.slice(0, a + OPEN.length), html.slice(a + OPEN.length, b), html.slice(b)];
};
const [head, , tail] = parts(readFileSync(join(here, "strategic-session-kino.html"), "utf8"));
const [, stage] = parts(readFileSync(src, "utf8"));
writeFileSync(out, head + stage + tail);
console.log(`${out} — ${(Buffer.byteLength(head + stage + tail) / 1048576).toFixed(1)} МБ`);
