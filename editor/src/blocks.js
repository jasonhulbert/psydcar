// Block rendering for live preview: the front matter properties block, tables, and HTML blocks.
// These widgets replace whole lines, so they come from a state field rather than the view plugin.
// Each one shows its source again while the cursor is inside it, and clicking it puts the cursor
// where you clicked. In Reading mode they stay rendered and a click follows links.

import { syntaxTree } from "@codemirror/language";
import { Facet, StateEffect, StateField } from "@codemirror/state";
import { Decoration, EditorView, WidgetType } from "@codemirror/view";
import { readProperties } from "./front-matter.js";

// True in Reading mode: the document is read-only and everything stays rendered.
export const readingMode = Facet.define({ combine: (values) => values.some(Boolean) });

// An icon from the dashboard's sprite, which the page inlines.
export function iconElement(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon cm-md-icon");
  svg.setAttribute("aria-hidden", "true");
  const use = svg.appendChild(document.createElementNS("http://www.w3.org/2000/svg", "use"));
  use.setAttribute("href", `/assets/icons.svg#${name}`);
  return svg;
}

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
const SKIPPED = new Set(["EmphasisMark", "CodeMark", "LinkMark", "StrikethroughMark", "URL", "LinkTitle", "LinkLabel", "QuoteMark", "WikiLinkMark", "FootnoteMark"]);

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
      case "WikiLink": case "WikiEmbed": {
        const target = child.getChild("WikiLinkTarget");
        const label = child.getChild("WikiLinkLabel") || target;
        if (target) parts.push({ tag: "a", wiki: true, url: state.sliceDoc(target.from, target.to).trim(), children: [{ text: state.sliceDoc(label.from, label.to) }] });
        break;
      }
      case "FootnoteReference": parts.push({ tag: "sup", children: [{ text: state.sliceDoc(child.from + 2, child.to - 1) }] }); break;
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
      if (part.wiki) element.dataset.wiki = "";
      element.title = part.url;
    }
    if (part.tag === "code") element.className = "cm-md-inline-code";
    renderParts(element, part.children, options);
  }
}

// Local images load through the dashboard. Remote images stay blocked, so they show their alt text.
// A wiki embed such as "![[photo.png]]" names the image instead of giving its path.
export function imageElement(url, alt, { resolveImage, onLoad, wiki = false, width = 0 } = {}) {
  const src = !wiki && /^data:image\//i.test(url) ? url : resolveImage?.(url, { wiki });
  const fallback = (note) => {
    const span = document.createElement("span");
    span.className = "cm-md-img-missing";
    span.textContent = alt ? `${alt} · ${note}` : note;
    span.title = url;
    return span;
  };
  if (!src) return fallback(wiki ? "Image not found" : /^[a-z][a-z\d+.-]*:|^\/\//i.test(url) ? "Remote images aren't loaded" : "Image isn't in this sidecar");
  const image = document.createElement("img");
  image.className = "cm-md-img";
  image.alt = alt;
  if (width > 0) image.width = width;
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

// A link in a block widget follows on Cmd/Ctrl-click, or on a plain click in Reading mode.
function followLink(view, event, options) {
  const anchor = event.target.closest("a[data-url]");
  if (!anchor || !(event.metaKey || event.ctrlKey || view.state.facet(readingMode))) return false;
  event.preventDefault();
  options.go?.(view, anchor.dataset.wiki !== undefined ? { wiki: anchor.dataset.url } : { url: anchor.dataset.url });
  return true;
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
      if (event.button !== 0 || followLink(view, event, this.options) || view.state.facet(readingMode)) return;
      event.preventDefault();
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
      if (event.target.closest("[data-tag]")) { event.preventDefault(); return; }
      if (view.state.facet(readingMode)) return;
      event.preventDefault();
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

// HTML blocks render through an allowlist. The markup is parsed in an inert document, and only
// known elements and attributes are copied into the page, so scripts, styles, and event handlers
// never reach it. Links and images go through the same paths as Markdown links and images.
const HTML_ELEMENTS = new Set([
  "a", "abbr", "b", "blockquote", "br", "caption", "cite", "code", "dd", "del", "details", "dfn", "div", "dl", "dt",
  "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li", "mark",
  "ol", "p", "pre", "q", "s", "samp", "small", "span", "strong", "sub", "summary", "sup", "table", "tbody", "td",
  "tfoot", "th", "thead", "time", "tr", "u", "ul", "var",
]);
// Elements dropped with their content. Anything else unknown keeps its content but loses the element.
const HTML_DROPPED = new Set([
  "script", "style", "template", "noscript", "iframe", "frame", "frameset", "object", "embed", "applet", "form", "input",
  "button", "select", "option", "textarea", "svg", "math", "video", "audio", "source", "track", "canvas", "link", "meta", "base", "title", "head", "dialog",
]);
const ALIGN = /^(left|center|right)$/i;
const whole = (value, max) => (/^\d{1,4}$/.test(value || "") ? Math.min(Number(value), max) : 0);

function copyHtml(source, target, options) {
  for (const node of source.childNodes) {
    if (node.nodeType === Node.TEXT_NODE) { target.append(node.data); continue; }
    if (node.nodeType !== Node.ELEMENT_NODE) continue;
    const tag = node.localName;
    if (HTML_DROPPED.has(tag)) continue;
    if (tag === "img") {
      const src = node.getAttribute("src") || "";
      const image = imageElement(src, node.getAttribute("alt") || "", { ...options, width: whole(node.getAttribute("width"), 4000) });
      if (image.tagName === "IMG") {
        const height = whole(node.getAttribute("height"), 4000);
        if (height && image.width) image.height = height;
        const align = node.getAttribute("align");
        if (align && ALIGN.test(align)) image.classList.add(`cm-md-align-${align.toLowerCase()}`);
      }
      target.append(image);
      continue;
    }
    if (tag === "center") {
      const block = target.appendChild(document.createElement("div"));
      block.className = "cm-md-align-center";
      copyHtml(node, block, options);
      continue;
    }
    if (!HTML_ELEMENTS.has(tag)) { copyHtml(node, target, options); continue; }
    const copy = target.appendChild(document.createElement(tag));
    const title = node.getAttribute("title");
    if (title) copy.title = title;
    const align = node.getAttribute("align");
    if (align && ALIGN.test(align)) copy.classList.add(`cm-md-align-${align.toLowerCase()}`);
    if (tag === "a") {
      const href = node.getAttribute("href");
      if (href) {
        copy.className = "cm-md-link";
        copy.dataset.url = href;
        copy.title = title || href;
      }
    } else if (tag === "details") {
      copy.open = node.hasAttribute("open");
      // GitHub-style details often hold Markdown in separate blocks, leaving only the summary here.
      // Then the summary shows as a label, since there's nothing to open.
      if ([...node.children].every((child) => child.localName === "summary") && !node.textContent.replace(node.querySelector("summary")?.textContent || "", "").trim()) {
        copy.remove();
        const label = target.appendChild(document.createElement("div"));
        label.className = "cm-md-summary";
        copyHtml(node.querySelector("summary") || node, label, options);
        continue;
      }
    } else if (tag === "td" || tag === "th") {
      const colSpan = whole(node.getAttribute("colspan"), 100);
      const rowSpan = whole(node.getAttribute("rowspan"), 100);
      if (colSpan) copy.colSpan = colSpan;
      if (rowSpan) copy.rowSpan = rowSpan;
    } else if (tag === "ol") {
      const start = node.getAttribute("start");
      if (/^-?\d{1,6}$/.test(start || "")) copy.start = Number(start);
    }
    copyHtml(node, copy, options);
  }
}

// Returns the rendered markup, or null when it would show nothing, such as a closing tag on its own.
function renderHtml(html, options) {
  // Style attributes are dropped anyway. Removing them first keeps the page's CSP from reporting them while parsing.
  const parsed = new DOMParser().parseFromString(html.replace(/\sstyle\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, ""), "text/html");
  const fragment = document.createDocumentFragment();
  copyHtml(parsed.body, fragment, options);
  const visible = fragment.textContent.trim() || fragment.querySelector?.("img, hr, .cm-md-img-missing");
  return visible ? fragment : null;
}

// Whether an HTML block shows anything, cached by its source because blocks rebuild as the cursor moves.
const htmlShown = new Map();
export function showsHtml(source) {
  if (!htmlShown.has(source)) {
    if (htmlShown.size > 500) htmlShown.clear();
    htmlShown.set(source, Boolean(renderHtml(source, {})));
  }
  return htmlShown.get(source);
}

class HtmlWidget extends WidgetType {
  constructor(source, options) { super(); this.source = source; this.options = options; }
  eq(other) { return other.source === this.source; }
  toDOM(view) {
    const wrap = document.createElement("div");
    wrap.className = "cm-md-html";
    const content = renderHtml(this.source, { ...this.options, onLoad: () => view.requestMeasure() });
    if (content) wrap.append(content);
    wrap.addEventListener("toggle", () => view.requestMeasure(), true);
    wrap.addEventListener("mousedown", (event) => {
      // A summary opens and closes its details, like a link in Reading mode.
      if (event.button !== 0 || event.target.closest("summary") || followLink(view, event, this.options) || view.state.facet(readingMode)) return;
      event.preventDefault();
      placeCursor(view, wrap, 0);
    });
    return wrap;
  }
  ignoreEvent() { return true; }
}

function build(state, options) {
  const focused = state.field(focusField) && !state.facet(readingMode);
  const ranges = [];
  const touched = (from, to) => focused && state.selection.ranges.some((range) => range.from <= to && range.to >= from);
  syntaxTree(state).iterate({
    enter(ref) {
      if (CONTAINERS.has(ref.name)) return true;
      if (ref.name !== "FrontMatter" && ref.name !== "Table" && ref.name !== "HTMLBlock") return false;
      // HTML blocks inside lists and quotes stay as source, so the list or quote keeps its marks.
      if (ref.name === "HTMLBlock" && ref.node.parent?.name !== "Document") return false;
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
      } else if (ref.name === "HTMLBlock") {
        if (showsHtml(source)) widget = new HtmlWidget(source, options);
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
        || transaction.startState.facet(readingMode) !== transaction.state.facet(readingMode)
        || syntaxTree(transaction.startState) !== syntaxTree(transaction.state)) {
        return build(transaction.state, options);
      }
      return value;
    },
    provide: (self) => EditorView.decorations.from(self),
  });
  return [focusField, trackFocus, field];
}
