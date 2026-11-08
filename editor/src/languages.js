import { LanguageDescription, LanguageSupport, StreamLanguage } from "@codemirror/language";
import { cpp } from "@codemirror/lang-cpp";
import { css } from "@codemirror/lang-css";
import { go } from "@codemirror/lang-go";
import { html } from "@codemirror/lang-html";
import { java } from "@codemirror/lang-java";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { python } from "@codemirror/lang-python";
import { rust } from "@codemirror/lang-rust";
import { sql } from "@codemirror/lang-sql";
import { yaml } from "@codemirror/lang-yaml";
import { csharp } from "@codemirror/legacy-modes/mode/clike";
import { shell } from "@codemirror/legacy-modes/mode/shell";
import { toml } from "@codemirror/legacy-modes/mode/toml";

const legacy = (mode) => () => new LanguageSupport(StreamLanguage.define(mode));

// Each entry lists the file extensions Psydcar indexes and the names used on Markdown code fences.
const LANGUAGES = [
  { name: "JavaScript", extensions: ["js"], alias: ["javascript", "js", "mjs", "cjs"], load: () => javascript() },
  { name: "JSX", extensions: ["jsx"], alias: ["jsx"], load: () => javascript({ jsx: true }) },
  { name: "TypeScript", extensions: ["ts"], alias: ["typescript", "ts"], load: () => javascript({ typescript: true }) },
  { name: "TSX", extensions: ["tsx"], alias: ["tsx"], load: () => javascript({ typescript: true, jsx: true }) },
  { name: "Python", extensions: ["py"], alias: ["python", "py"], load: () => python() },
  { name: "JSON", extensions: ["json"], alias: ["json", "jsonc"], load: () => json() },
  { name: "YAML", extensions: ["yaml", "yml"], alias: ["yaml", "yml"], load: () => yaml() },
  { name: "TOML", extensions: ["toml"], alias: ["toml"], load: legacy(toml) },
  { name: "Go", extensions: ["go"], alias: ["go", "golang"], load: () => go() },
  { name: "Rust", extensions: ["rs"], alias: ["rust", "rs"], load: () => rust() },
  { name: "Java", extensions: ["java"], alias: ["java"], load: () => java() },
  { name: "C++", extensions: ["c", "h", "cpp", "hpp"], alias: ["c", "cpp", "c++", "h", "hpp"], load: () => cpp() },
  { name: "C#", extensions: ["cs"], alias: ["csharp", "cs", "c#"], load: legacy(csharp) },
  { name: "Shell", extensions: ["sh"], alias: ["shell", "sh", "bash", "zsh", "console"], load: legacy(shell) },
  { name: "SQL", extensions: ["sql"], alias: ["sql"], load: () => sql() },
  { name: "HTML", extensions: ["html"], alias: ["html", "xml", "svg"], load: () => html() },
  { name: "CSS", extensions: ["css"], alias: ["css"], load: () => css() },
];

const cache = new Map();

function support(entry) {
  if (!cache.has(entry.name)) cache.set(entry.name, entry.load());
  return cache.get(entry.name);
}

export function languageForPath(path) {
  const extension = path.split(".").pop().toLowerCase();
  const entry = LANGUAGES.find((item) => item.extensions.includes(extension));
  return entry ? { name: entry.name, support: support(entry) } : null;
}

export const fenceLanguages = LANGUAGES.map((entry) => LanguageDescription.of({
  name: entry.name, alias: entry.alias, extensions: entry.extensions, support: support(entry),
}));
