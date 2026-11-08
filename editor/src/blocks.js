// Block rendering for live preview: the front matter properties block and tables. These widgets
// replace whole lines, so they come from a state field rather than the view plugin. Each one shows
// its source again while the cursor is inside it, and clicking it puts the cursor where you clicked.

import { syntaxTree } from "@codemirror/language";
import { StateEffect, StateField } from "@codemirror/state";
import { Decoration, EditorView, WidgetType } from "@codemirror/view";
import { readProperties } from "./front-matter.js";

const setFocus = StateEffect.define();
const focusField = StateField.define({
  create: () => false,
  update(value, transaction) {
    for (const effect of transaction.effects) if (effect.is(setFocus)) value = effect.value;
    return value;
  },
});
const trackFocus = EditorView.focusChangeEffect.of((_state, focusing) => setFocus.of(focusing));

const CONTAINERS = new Set(["Document", "BulletList", "OrderedList", "ListItem", "Blockquote"]);
const SKIPPED = new Set(["EmphasisMark", "CodeMark", "LinkMark", "StrikethroughMark", "URL", "LinkTitle", "LinkLabel", "QuoteMark"]);

// Text areas parse their content as plain text, so decoding an entity here can't create elements.
const decoder = document.createElement("textarea");
function decodeEntity(text) {
  if (!/^&(?:#\d{1,7}|#x[\da-f]{1,6}|[a-z][a-z\d]{1,31});$/i.test(text)) return text;
  decoder.innerHTML = text;
  return decoder.value;
}

// A plain description of a cell's inline Markdown, so widgets can compare and redraw it without the syntax tree.
function inlineParts(state, node, from, to) {
  const parts = [];
  let pos = from;
  const text = (start, end) => { if (end > start) parts.push({ text: state.sliceDoc(start, end) }); };
  for (let child = node.firstChild; child; child = child.nextSibling) {
    if (child.to <= from || child.from >= to) continue;
    text(pos, child.from);
    pos = child.to;
    const marks = () => {
      const list = [];
      for (let mark = child.firstChild; mark; mark = mark.nextSibling) if (mark.name === "LinkMark") list.push(mark);
      return list;
    };
    const url = () => { const target = child.getChild("URL"); return target ? state.sliceDoc(target.from, target.to) : ""; };
    switch (child.name) {
      case "StrongEmphasis": parts.push({ tag: "strong", children: inlineParts(state, child, child.from, child.to) }); break;
      case "Emphasis": parts.push({ tag: "em", children: inlineParts(state, child, child.from, child.to) }); break;
      case "Strikethrough": parts.push({ tag: "s", children: inlineParts(state, child, child.from, child.to) }); break;
      case "InlineCode": {
        const code = child.getChildren("CodeMark");
        parts.push({ tag: "code", children: [{ text: state.sliceDoc(code[0]?.to ?? child.from, code[1]?.from ?? child.to) }] });
        break;
      }
      case "Link": {
        const list = marks();
        const label = list.length >= 2 ? [list[0].to, list[1].from] : [child.from, child.to];
        parts.push({ tag: "a", url: url(), children: inlineParts(state, child, label[0], label[1]) });
        break;
      }
      case "Image": {
        const list = marks();
        parts.push({ tag: "img", url: url(), alt: list.length >= 2 ? state.sliceDoc(list[0].to, list[1].from) : "" });
        break;
      }
      case "Autolink": {
        const list = marks();
        const address = list.length === 2 ? state.sliceDoc(list[0].to, list[1].from) : state.sliceDoc(child.from, child.to);
        parts.push({ tag: "a", url: address, children: [{ text: address }] });
        break;
      }
      case "URL": parts.push({ tag: "a", url: state.sliceDoc(child.from, child.to), children: [{ text: state.sliceDoc(child.from, child.to) }] }); break;
      case "Escape": parts.push({ text: state.sliceDoc(child.from + 1, child.to) }); break;
      case "Entity": parts.push({ text: decodeEntity(state.sliceDoc(child.from, child.to)) }); break;
      case "HardBreak": parts.push({ tag: "br" }); break;
      default:
        if (!SKIPPED.has(child.name)) text(child.from, child.to);
    }
  }
  text(pos, to);
  return parts;
}

function renderParts(parent, parts, options) {
  for (const part of parts) {
    if (part.text !== undefined) { parent.append(part.text); continue; }
    if (part.tag === "br") { parent.append(document.createElement("br")); continue; }
    if (part.tag === "img") { parent.append(imageElement(part.url, part.alt, options)); continue; }
    const element = parent.appendChild(document.createElement(part.tag));
    if (part.tag === "a") {
      element.className = "cm-md-link";
      element.dataset.url = part.url;
      element.title = part.url;
    }
    if (part.tag === "code") element.className = "cm-md-inline-code";
    renderParts(element, part.children, options);
  }
}

// Local images load through the dashboard. Remote images stay blocked, so they show their alt text.
export function imageElement(url, alt, { resolveImage, onLoad } = {}) {
  const src = /^data:image\//i.test(url) ? url : resolveImage?.(url);
  const fallback = (note) => {
    const span = document.createElement("span");
    span.className = "cm-md-img-missing";
    span.textContent = alt ? `${alt} · ${note}` : note;
    span.title = url;
    return span;
  };
  if (!src) return fallback(/^[a-z][a-z\d+.-]*:|^\/\//i.test(url) ? "Remote images aren't loaded" : "Image isn't in this sidecar");
  const image = document.createElement("img");
  image.className = "cm-md-img";
  image.alt = alt;
  image.title = alt || url;
  image.src = src;
  image.addEventListener("error", () => image.replaceWith(fallback("Image not found")));
  if (onLoad) image.addEventListener("load", onLoad);
  return image;
}

function rowCells(state, row) {
  const delimiters = row.getChildren("TableDelimiter");
  const bounds = [];
  let start = row.from;
  for (const delimiter of delimiters) { bounds.push([start, delimiter.from]); start = delimiter.to; }
  bounds.push([start, row.to]);
  const text = state.sliceDoc(row.from, row.to);
  if (bounds.length > 1 && /^\s*\|/.test(text) && !state.sliceDoc(...bounds[0]).trim()) bounds.shift();
  if (bounds.length > 1 && /\|\s*$/.test(text) && !state.sliceDoc(...bounds[bounds.length - 1]).trim()) bounds.pop();
  const cells = row.getChildren("TableCell");
  return bounds.map(([from, to]) => {
    const cell = cells.find((item) => item.from >= from && item.to <= to);
    return { from: cell ? cell.from : from, parts: cell ? inlineParts(state, cell, cell.from, cell.to) : [] };
  });
}

function tableModel(state, node) {
  const start = state.doc.lineAt(node.from).from;
  const header = node.getChild("TableHeader");
  if (!header) return null;
  const rows = [];
  let align = [];
  for (let child = node.firstChild; child; child = child.nextSibling) {
    if (child.name === "TableDelimiter") {
      align = state.sliceDoc(child.from, child.to).replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((cell) => {
        const spec = cell.trim();
        return spec.startsWith(":") && spec.endsWith(":") ? "center" : spec.endsWith(":") ? "right" : spec.startsWith(":") ? "left" : "";
      });
    } else if (child.name === "TableHeader" || child.name === "TableRow") {
      rows.push(rowCells(state, child).map((cell) => ({ offset: cell.from - start, parts: cell.parts })));
    }
  }
  const columns = rows[0].length;
  return { align, columns, rows: rows.map((row) => Array.from({ length: columns }, (_, index) => row[index] || { offset: row.at(-1)?.offset ?? 0, parts: [] })) };
}

// Put the cursor at an offset from the start of a block widget, which shows the block's source.
function placeCursor(view, dom, offset) {
  const pos = Math.min(view.posAtDOM(dom) + offset, view.state.doc.length);
  view.dispatch({ selection: { anchor: pos }, scrollIntoView: false });
  view.focus();
}

class TableWidget extends WidgetType {
  constructor(source, model, options) { super(); this.source = source; this.model = model; this.options = options; }
  eq(other) { return other.source === this.source; }
  toDOM(view) {
    const wrap = document.createElement("div");
    wrap.className = "cm-md-table-block";
    const scroll = wrap.appendChild(document.createElement("div"));
    scroll.className = "cm-md-table-scroll";
    const table = scroll.appendChild(document.createElement("table"));
    table.className = "cm-md-table-grid";
    const { rows, align } = this.model;
    rows.forEach((row, index) => {
      const section = index === 0 ? table.createTHead() : (table.tBodies[0] || table.createTBody());
      const line = section.insertRow();
      row.forEach((cell, column) => {
        const element = line.appendChild(document.createElement(index === 0 ? "th" : "td"));
        element.dataset.offset = String(cell.offset);
        if (align[column]) element.classList.add(`cm-md-align-${align[column]}`);
        // The inner box caps how wide a column grows before its text wraps.
        const content = element.appendChild(document.createElement("div"));
        content.className = "cm-md-cell";
        renderParts(content, cell.parts, { ...this.options, onLoad: () => view.requestMeasure() });
      });
    });
    wrap.addEventListener("mousedown", (event) => {
      if (event.button !== 0) return;
      const anchor = event.target.closest("a[data-url]");
      event.preventDefault();
      if (anchor && (event.metaKey || event.ctrlKey)) { this.options.follow?.(view, anchor.dataset.url); return; }
      const cell = event.target.closest("[data-offset]");
      placeCursor(view, wrap, cell ? Number(cell.dataset.offset) : 0);
    });
    return wrap;
  }
  ignoreEvent() { return true; }
}

class PropertiesWidget extends WidgetType {
  constructor(source, properties, options) { super(); this.source = source; this.properties = properties; this.options = options; }
  eq(other) { return other.source === this.source; }
  toDOM(view) {
    const wrap = document.createElement("div");
    wrap.className = "cm-md-properties";
    const title = wrap.appendChild(document.createElement("div"));
    title.className = "cm-md-properties-title";
    title.textContent = "Properties";
    const list = wrap.appendChild(document.createElement("dl"));
    list.className = "cm-md-properties-list";
    if (!this.properties.length) {
      const empty = list.appendChild(document.createElement("div"));
      empty.className = "cm-md-properties-empty";
      empty.textContent = "No properties";
    }
    for (const property of this.properties) {
      const row = list.appendChild(document.createElement("div"));
      row.className = "cm-md-property";
      row.dataset.offset = String(property.offset + 4);
      const key = row.appendChild(document.createElement("dt"));
      key.textContent = property.key;
      const value = row.appendChild(document.createElement("dd"));
      value.className = `cm-md-property-${property.kind}`;
      if (property.kind === "tags" || property.kind === "list") {
        for (const item of property.value) {
          const tag = property.kind === "tags";
          const chip = value.appendChild(document.createElement(tag && this.options.onTag ? "button" : "span"));
          chip.className = tag ? "cm-md-tag" : "cm-md-chip";
          chip.textContent = tag ? `#${item}` : item;
          if (chip.tagName === "BUTTON") {
            chip.type = "button";
            chip.title = `Show files tagged #${item}`;
            chip.dataset.tag = item;
          }
        }
      } else {
        value.textContent = property.value;
      }
      if (!property.value.length) { value.classList.add("is-empty"); value.textContent = "Empty"; }
    }
    wrap.addEventListener("mousedown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      if (event.target.closest("[data-tag]")) return;
      const row = event.target.closest("[data-offset]");
      placeCursor(view, wrap, row ? Number(row.dataset.offset) : 4);
    });
    wrap.addEventListener("click", (event) => {
      const chip = event.target.closest("[data-tag]");
      if (chip) this.options.onTag?.(chip.dataset.tag);
    });
    return wrap;
  }
  ignoreEvent() { return true; }
}

function build(state, options) {
  const focused = state.field(focusField);
  const ranges = [];
  const touched = (from, to) => focused && state.selection.ranges.some((range) => range.from <= to && range.to >= from);
  syntaxTree(state).iterate({
    enter(ref) {
      if (CONTAINERS.has(ref.name)) return true;
      if (ref.name !== "FrontMatter" && ref.name !== "Table") return false;
      const from = state.doc.lineAt(ref.from).from;
      const to = state.doc.lineAt(ref.to).to;
      if (touched(from, to)) return false;
      const source = state.sliceDoc(from, to);
      let widget = null;
      if (ref.name === "FrontMatter") {
        // The YAML between the fences. Its node holds a mounted YAML tree, so read it by position.
        const close = ref.node.getChildren("FrontMatterMark").at(-1);
        const yaml = close && close.from - 1 > ref.from + 4 ? state.sliceDoc(ref.from + 4, close.from - 1) : "";
        widget = new PropertiesWidget(source, readProperties(yaml), options);
      } else {
        const model = tableModel(state, ref.node);
        if (model) widget = new TableWidget(source, model, options);
      }
      if (widget) ranges.push(Decoration.replace({ widget, block: true }).range(from, to));
      return false;
    },
  });
  return Decoration.set(ranges, true);
}

export function blockPreview(options) {
  const field = StateField.define({
    create: (state) => build(state, options),
    update(value, transaction) {
      const focusChanged = transaction.effects.some((effect) => effect.is(setFocus));
      if (transaction.docChanged || transaction.selection || focusChanged
        || syntaxTree(transaction.startState) !== syntaxTree(transaction.state)) {
        return build(transaction.state, options);
      }
      return value;
    },
    provide: (self) => EditorView.decorations.from(self),
  });
  return [focusField, trackFocus, field];
}
