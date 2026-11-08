// YAML front matter: a Markdown parser extension that recognizes the block, and a small reader
// that turns it into properties for display. The reader handles the shapes notes use, such as
// text, lists, and tags. Anything more nested is shown as written.

import { yamlLanguage } from "@codemirror/lang-yaml";
import { parseMixed } from "@lezer/common";
import { tags } from "@lezer/highlight";

const LOOKAHEAD = 64 * 1024;
const FENCE = /^---[ \t]*$/;

// Front matter starts on the first line with "---" and ends at the next "---" or "..." line.
// Without a closing line, the opening "---" stays a horizontal rule.
export const frontMatter = {
  defineNodes: [
    { name: "FrontMatter", block: true },
    { name: "FrontMatterMark", style: tags.processingInstruction },
    "FrontMatterContent",
  ],
  parseBlock: [{
    name: "FrontMatter",
    before: "HorizontalRule",
    parse(cx, line) {
      if (cx.lineStart !== 0 || cx.depth !== 1 || !FENCE.test(line.text) || !cx.input) return false;
      const head = cx.input.read(0, Math.min(cx.input.length, LOOKAHEAD));
      const close = /\n(?:---|\.\.\.)[ \t]*(?=\n|$)/.exec(head);
      if (!close) return false;
      const closeStart = close.index + 1;
      const end = close.index + close[0].length;
      while (cx.lineStart < closeStart && cx.nextLine()) { /* Consume the block's lines. */ }
      cx.nextLine();
      const children = [cx.elt("FrontMatterMark", 0, 3)];
      if (close.index > 4) children.push(cx.elt("FrontMatterContent", 4, close.index));
      children.push(cx.elt("FrontMatterMark", closeStart, closeStart + 3));
      cx.addElement(cx.elt("FrontMatter", 0, end, children));
      return true;
    },
  }],
  // Highlight the YAML inside, for Source mode and while the raw block is showing.
  wrap: parseMixed((node) => (node.name === "FrontMatterContent" ? { parser: yamlLanguage.parser } : null)),
};

function stripComment(value) {
  if (/^["']/.test(value)) return value;
  return value.replace(/(^|\s)#.*$/, "").trim();
}

function unquote(value) {
  const text = value.trim();
  if (text.length >= 2 && text[0] === text[text.length - 1] && (text[0] === '"' || text[0] === "'")) return text.slice(1, -1);
  return text;
}

const isTagKey = (key) => /^tags?$/i.test(key);

function tagNames(values) {
  const names = [];
  for (const value of values) {
    const name = unquote(stripComment(value)).replace(/^#/, "").trim();
    if (name && !names.includes(name)) names.push(name);
  }
  return names;
}

// Returns [{ key, offset, kind, value }], where offset is the key's position in the YAML text and
// kind is "text", "list", "tags", or "raw". Lists and tags carry an array of strings.
export function readProperties(yaml) {
  const lines = yaml.split("\n");
  const properties = [];
  let offset = 0;
  const starts = lines.map((line) => { const start = offset; offset += line.length + 1; return start; });
  for (let index = 0; index < lines.length; index++) {
    const match = /^([^\s#-][^:]*?|"[^"]*"|'[^']*'):(?:[ \t]+(.*))?$/.exec(lines[index]);
    if (!match) continue;
    const key = unquote(match[1]);
    const start = starts[index];
    const value = stripComment(match[2] ?? "");
    const body = [];
    while (index + 1 < lines.length && (/^[ \t]/.test(lines[index + 1]) || /^-(\s|$)/.test(lines[index + 1]) || !lines[index + 1].trim())) {
      body.push(lines[++index]);
    }
    while (body.length && !body[body.length - 1].trim()) body.pop();
    const filled = body.filter((line) => line.trim());

    if (value.startsWith("[") && value.endsWith("]")) {
      const items = value.slice(1, -1).split(",").map((item) => unquote(item)).filter(Boolean);
      properties.push(isTagKey(key) ? { key, offset: start, kind: "tags", value: tagNames(items) } : { key, offset: start, kind: "list", value: items });
    } else if (/^[|>][+-]?\d*$/.test(value)) {
      const indent = Math.min(...filled.map((line) => /^\s*/.exec(line)[0].length));
      const text = body.map((line) => line.slice(indent)).join(value[0] === "|" ? "\n" : " ");
      properties.push({ key, offset: start, kind: "text", value: text.trim() });
    } else if (value) {
      if (isTagKey(key)) properties.push({ key, offset: start, kind: "tags", value: tagNames(value.split(/[,\s]+/)) });
      else properties.push({ key, offset: start, kind: "text", value: [unquote(value), ...filled.map((line) => line.trim())].join(" ") });
    } else if (filled.length && filled.every((line) => /^\s*-(\s|$)/.test(line) && !/^\s*-\s+[^"'\s][^:]*:(\s|$)/.test(line))) {
      const items = filled.map((line) => unquote(stripComment(line.replace(/^\s*-\s*/, "")))).filter(Boolean);
      properties.push(isTagKey(key) ? { key, offset: start, kind: "tags", value: tagNames(items) } : { key, offset: start, kind: "list", value: items });
    } else if (filled.length) {
      const indent = Math.min(...filled.map((line) => /^\s*/.exec(line)[0].length));
      properties.push({ key, offset: start, kind: "raw", value: body.map((line) => line.slice(indent)).join("\n") });
    } else {
      properties.push({ key, offset: start, kind: isTagKey(key) ? "tags" : "text", value: isTagKey(key) ? [] : "" });
    }
  }
  return properties;
}
