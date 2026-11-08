// Markdown live preview: the document text never changes, but syntax marks are hidden and
// rendered as formatting until the cursor reaches them. Revealing marks on the lines being
// edited keeps every character reachable without a separate source mode.

import { foldEffect, foldable, foldedRanges, syntaxTree, unfoldEffect } from "@codemirror/language";
import { Prec } from "@codemirror/state";
import { Decoration, EditorView, ViewPlugin, WidgetType, keymap } from "@codemirror/view";
import { blockPreview, iconElement, imageElement, readingMode, showsHtml } from "./blocks.js";
import { HEADINGS, revealAnchor, revealPosition, slug } from "./outline.js";
import { CALLOUT, CALLOUT_ICONS, calloutFamily } from "./syntax.js";

const hide = Decoration.replace({});
const lineClass = (name) => Decoration.line({ class: name });
const mark = (name) => Decoration.mark({ class: name });
const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
const openHint = `${isMac ? "⌘" : "Ctrl"}-click to open`;
const link = (url, reading) => {
  const hint = reading ? "" : openHint;
  return Decoration.mark({ class: "cm-md-link", attributes: { title: [url, hint].filter(Boolean).join("\n") } });
};
const IMAGE_PATH = /\.(png|jpe?g|gif|webp|avif|svg|bmp|ico)$/i;
// In Reading mode a plain click follows links. Otherwise it takes Cmd or Ctrl.
const followsClick = (event) => event.metaKey || event.ctrlKey || Boolean(event.target.closest?.(".cm-reading"));
const MAX_LIST_DEPTH = 8;

class TaskWidget extends WidgetType {
  constructor(checked, readOnly) { super(); this.checked = checked; this.readOnly = readOnly; }
  eq(other) { return other.checked === this.checked && other.readOnly === this.readOnly; }
  toDOM(view) {
    const wrap = document.createElement("span");
    wrap.className = "cm-md-list-mark cm-md-task-mark";
    const box = wrap.appendChild(document.createElement("input"));
    box.type = "checkbox";
    box.className = "cm-md-task";
    box.checked = this.checked;
    box.disabled = this.readOnly;
    box.setAttribute("aria-label", this.checked ? "Mark task as open" : "Mark task as done");
    box.addEventListener("mousedown", (event) => event.preventDefault());
    box.addEventListener("click", (event) => {
      event.preventDefault();
      // The widget covers the list marker too, for example "- [ ] ", so find the box inside it.
      const at = view.posAtDOM(wrap);
      const match = /^(?:[-*+]|\d+[.)])?[ \t]*\[([ xX])\]/.exec(view.state.sliceDoc(at, at + 16));
      if (!match) return;
      const pos = at + match[0].length - 2;
      view.dispatch({ changes: { from: pos, to: pos + 1, insert: match[1] === " " ? "x" : " " } });
    });
    return wrap;
  }
  ignoreEvent() { return true; }
}

class TextWidget extends WidgetType {
  constructor(text, className) { super(); this.text = text; this.className = className; }
  eq(other) { return other.text === this.text && other.className === this.className; }
  toDOM() {
    const span = document.createElement("span");
    span.className = this.className;
    span.textContent = this.text;
    return span;
  }
}

class RuleWidget extends WidgetType {
  eq() { return true; }
  toDOM() {
    const rule = document.createElement("span");
    rule.className = "cm-md-rule";
    rule.setAttribute("role", "separator");
    return rule;
  }
}

class ImageWidget extends WidgetType {
  constructor(url, alt, options, { wiki = false, width = 0 } = {}) {
    super();
    Object.assign(this, { url, alt, options, wiki, width });
  }
  eq(other) { return other.url === this.url && other.alt === this.alt && other.wiki === this.wiki && other.width === this.width; }
  toDOM(view) {
    const wrap = document.createElement("span");
    wrap.className = "cm-md-img-wrap";
    wrap.append(imageElement(this.url, this.alt, { ...this.options, wiki: this.wiki, width: this.width, onLoad: () => view.requestMeasure() }));
    // Cmd/Ctrl-click opens the image file. A plain click is left to the editor, which places the cursor.
    wrap.addEventListener("mousedown", (event) => {
      if (event.button !== 0 || !followsClick(event)) return;
      event.preventDefault();
      this.options.go(view, this.wiki ? { wiki: this.url } : { url: this.url });
    });
    return wrap;
  }
  ignoreEvent(event) { return followsClick(event); }
}

// The chevron beside a top-level heading folds the section under it.
class FoldWidget extends WidgetType {
  constructor(folded) { super(); this.folded = folded; }
  eq(other) { return other.folded === this.folded; }
  toDOM(view) {
    const button = document.createElement("button");
    button.type = "button";
    button.tabIndex = -1;
    button.className = `cm-md-fold${this.folded ? " is-folded" : ""}`;
    const label = this.folded ? "Expand section" : "Collapse section";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.setAttribute("aria-expanded", String(!this.folded));
    button.append(iconElement("chevron-right"));
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", (event) => {
      event.preventDefault();
      toggleSection(view, view.state.doc.lineAt(view.posAtDOM(button)));
    });
    return button;
  }
  ignoreEvent() { return true; }
}

function sectionFold(state, line) {
  let folded = null;
  foldedRanges(state).between(line.to, line.to, (from, to) => { if (from === line.to) folded = { from, to }; });
  return folded;
}

function toggleSection(view, line) {
  const folded = sectionFold(view.state, line);
  if (folded) { view.dispatch({ effects: unfoldEffect.of(folded) }); return; }
  const range = foldable(view.state, line.from, line.to);
  if (range) view.dispatch({ effects: foldEffect.of(range) });
}

class CalloutWidget extends WidgetType {
  constructor(family, label) { super(); this.family = family; this.label = label; }
  eq(other) { return other.family === this.family && other.label === this.label; }
  toDOM() {
    const span = document.createElement("span");
    span.className = "cm-md-callout-mark";
    span.append(iconElement(CALLOUT_ICONS[this.family]));
    if (this.label) span.append(this.label);
    return span;
  }
}

// A footnote reference shows as its number. Following it scrolls to the definition, and following
// the number on a definition scrolls back to the first reference.
class FootnoteWidget extends WidgetType {
  constructor(text, label, definition, options) { super(); Object.assign(this, { text, label, definition, options }); }
  eq(other) { return other.text === this.text && other.label === this.label && other.definition === this.definition; }
  toDOM(view) {
    const item = document.createElement(this.definition ? "span" : "sup");
    item.className = this.definition ? "cm-md-footnote-label" : "cm-md-footnote-ref";
    item.textContent = this.definition ? `${this.text}.` : this.text;
    item.title = this.definition ? `Footnote ${this.label}. Follow to go back to the text.` : `Footnote ${this.label}. Follow to read it.`;
    item.addEventListener("mousedown", (event) => {
      if (event.button !== 0 || !followsClick(event)) return;
      event.preventDefault();
      this.options.go(view, { footnote: this.label, definition: !this.definition });
    });
    return item;
  }
  ignoreEvent(event) { return followsClick(event); }
}

const tasks = [false, true].map((readOnly) => ({
  open: Decoration.replace({ widget: new TaskWidget(false, readOnly) }),
  done: Decoration.replace({ widget: new TaskWidget(true, readOnly) }),
}));
const bullet = Decoration.replace({ widget: new TextWidget("•", "cm-md-list-mark cm-md-bullet") });
const rawBullet = mark("cm-md-list-mark cm-md-bullet-raw");
const number = mark("cm-md-list-mark cm-md-list-number");
const rule = Decoration.replace({ widget: new RuleWidget() });

// Text areas parse their content as plain text, so decoding an entity here can't create elements.
const decoder = document.createElement("textarea");
function decodeEntity(text) {
  if (!/^&(?:#\d{1,7}|#x[\da-f]{1,6}|[a-z][a-z\d]{1,31});$/i.test(text)) return null;
  decoder.innerHTML = text;
  const value = decoder.value;
  return value === text ? null : value;
}

// Footnotes are numbered in the order they're first referenced. The numbers are cached per syntax tree.
const footnoteCache = new WeakMap();
const SKIP_FOR_FOOTNOTES = new Set(["FencedCode", "CodeBlock", "FrontMatter", "HTMLBlock", "CommentBlock", "Table", "FootnoteDefinition"]);
function footnoteNumbers(state) {
  const tree = syntaxTree(state);
  let numbers = footnoteCache.get(tree);
  if (numbers) return numbers;
  numbers = new Map();
  tree.iterate({
    enter(ref) {
      if (SKIP_FOR_FOOTNOTES.has(ref.name)) return false;
      if (ref.name !== "FootnoteReference") return undefined;
      const label = state.sliceDoc(ref.from + 2, ref.to - 1);
      if (!numbers.has(label)) numbers.set(label, numbers.size + 1);
      return false;
    },
  });
  footnoteCache.set(tree, numbers);
  return numbers;
}

function findFootnote(state, label, definition) {
  const wanted = definition ? "FootnoteDefinition" : "FootnoteReference";
  let found = null;
  syntaxTree(state).iterate({
    enter(ref) {
      if (found !== null) return false;
      if (ref.name === "FencedCode" || ref.name === "CodeBlock") return false;
      if (ref.name !== wanted) return undefined;
      const node = ref.node.getChild("FootnoteLabel");
      if (node && state.sliceDoc(node.from, node.to) === label) found = ref.from;
      return false;
    },
  });
  return found;
}

const inside = (node, name) => {
  for (let parent = node.parent; parent; parent = parent.parent) if (parent.name === name) return true;
  return false;
};
const listDepth = (node) => {
  let depth = 0;
  for (let parent = node.parent; parent; parent = parent.parent) if (parent.name === "BulletList" || parent.name === "OrderedList") depth++;
  return depth;
};

function build(view, options) {
  const { state } = view;
  const doc = state.doc;
  const ranges = [];
  const lines = new Set();
  const reading = state.facet(readingMode);
  // Reading mode renders everything. Otherwise the construct under the cursor shows its syntax.
  const focused = view.hasFocus && !reading;
  // Callout markers such as "[!note]", which the parser may also read as a link.
  const calloutMarks = new Set();
  // Lines inside list items, keyed by line start. Nested items are visited after their parents, so they win.
  const listLines = new Map();
  const codeLines = new Set();

  // A construct shows its raw syntax while any selection touches one of its lines.
  const active = (from, to) => {
    if (!focused) return false;
    const start = doc.lineAt(from).from;
    const end = doc.lineAt(to).to;
    return state.selection.ranges.some((range) => range.from <= end && range.to >= start);
  };
  const touching = (from, to) => focused && state.selection.ranges.some((range) => range.from <= to && range.to >= from);
  const addLines = (from, to, name) => {
    for (let number = doc.lineAt(from).number, last = doc.lineAt(to).number; number <= last; number++) {
      const line = doc.line(number);
      const key = `${line.from}:${name}`;
      if (lines.has(key)) continue;
      lines.add(key);
      ranges.push(lineClass(name).range(line.from));
    }
  };
  const children = (node) => {
    const list = [];
    for (let child = node.firstChild; child; child = child.nextSibling) list.push(child);
    return list;
  };
  const spaceAfter = (pos) => (doc.sliceString(pos, pos + 1) === " " ? pos + 1 : pos);
  const slice = (node) => doc.sliceString(node.from, node.to);

  for (const visible of view.visibleRanges) {
    syntaxTree(state).iterate({
      from: visible.from,
      to: visible.to,
      enter(ref) {
        const { name, from, to } = ref;
        const node = ref.node;

        if (name in HEADINGS) {
          const level = HEADINGS[name];
          const setext = name.startsWith("Setext");
          const textEnd = setext ? doc.lineAt(from).to : to;
          addLines(from, textEnd, `cm-md-heading cm-md-h${level}`);
          if (node.parent?.name === "Document") {
            const line = doc.lineAt(from);
            if (foldable(state, line.from, line.to)) {
              ranges.push(Decoration.widget({ widget: new FoldWidget(Boolean(sectionFold(state, line))), side: -1 }).range(line.from));
            }
          }
          if (active(from, to)) return;
          for (const child of children(node)) {
            if (child.name !== "HeaderMark") continue;
            if (setext) {
              ranges.push(hide.range(child.from, child.to));
              // The underline is on a line of its own. Collapse it so it doesn't leave a gap.
              if (doc.lineAt(child.from).number > doc.lineAt(from).number) addLines(child.from, child.from, "cm-md-setext-mark");
            } else if (child.from === from) ranges.push(hide.range(child.from, spaceAfter(child.to)));
            else ranges.push(hide.range(doc.sliceString(child.from - 1, child.from) === " " ? child.from - 1 : child.from, child.to));
          }
          return;
        }

        switch (name) {
          case "Emphasis":
          case "StrongEmphasis":
          case "Strikethrough":
            if (!active(from, to)) {
              for (const child of children(node)) {
                if (child.name === "EmphasisMark" || child.name === "StrikethroughMark") ranges.push(hide.range(child.from, child.to));
              }
            }
            return;

          case "InlineCode": {
            const marks = children(node).filter((child) => child.name === "CodeMark");
            if (marks.length === 2 && !active(from, to)) {
              ranges.push(hide.range(marks[0].from, marks[0].to), hide.range(marks[1].from, marks[1].to));
              if (marks[0].to < marks[1].from) ranges.push(mark("cm-md-inline-code").range(marks[0].to, marks[1].from));
            } else {
              ranges.push(mark("cm-md-inline-code").range(from, to));
            }
            return false;
          }

          case "Escape":
            if (!active(from, to)) ranges.push(hide.range(from, from + 1));
            return;

          case "Entity": {
            const value = active(from, to) ? null : decodeEntity(doc.sliceString(from, to));
            if (value) ranges.push(Decoration.replace({ widget: new TextWidget(value, "cm-md-entity") }).range(from, to));
            return;
          }

          case "Link": {
            if (calloutMarks.has(from)) return false;
            const marks = children(node).filter((child) => child.name === "LinkMark");
            if (marks.length < 2) return;
            const label = [marks[0].to, marks[1].from];
            const url = node.getChild("URL");
            const target = url ? doc.sliceString(url.from, url.to) : "";
            if (label[0] < label[1]) ranges.push(link(target, reading).range(label[0], label[1]));
            if (!active(from, to)) {
              ranges.push(hide.range(marks[0].from, marks[0].to), hide.range(marks[1].from, to));
            }
            return false;
          }

          case "Image": {
            // The image replaces its syntax. While the cursor is on its line, the syntax shows with the image below it.
            const marks = children(node).filter((child) => child.name === "LinkMark");
            const url = node.getChild("URL");
            if (marks.length < 2 || !url) return false;
            // Raw table source keeps its images as text. The rendered table shows them.
            if (inside(node, "Table")) return false;
            const widget = new ImageWidget(doc.sliceString(url.from, url.to), doc.sliceString(marks[0].to, marks[1].from), options);
            if (active(from, to)) {
              if (marks[0].to < marks[1].from) ranges.push(mark("cm-md-image").range(marks[0].to, marks[1].from));
              ranges.push(Decoration.widget({ widget, side: 1 }).range(to));
            } else {
              ranges.push(Decoration.replace({ widget }).range(from, to));
            }
            return false;
          }

          case "FrontMatter":
            addLines(from, to, "cm-md-frontmatter");
            return false;

          case "WikiLink":
          case "WikiEmbed": {
            const target = node.getChild("WikiLinkTarget");
            if (!target) return false;
            const address = slice(target).trim();
            const label = node.getChild("WikiLinkLabel");
            if (name === "WikiEmbed" && IMAGE_PATH.test(address)) {
              // "![[photo.png|300]]" sets the width. Any other label is the alt text.
              const caption = label ? slice(label).trim() : "";
              const width = /^\d+(x\d+)?$/.test(caption) ? parseInt(caption, 10) : 0;
              const widget = new ImageWidget(address, width ? "" : caption, options, { wiki: true, width });
              if (active(from, to)) {
                ranges.push(mark("cm-md-image").range(target.from, target.to));
                ranges.push(Decoration.widget({ widget, side: 1 }).range(to));
              } else {
                ranges.push(Decoration.replace({ widget }).range(from, to));
              }
              return false;
            }
            const shown = label || target;
            ranges.push(link(address, reading).range(shown.from, shown.to));
            if (!active(from, to)) ranges.push(hide.range(from, shown.from), hide.range(shown.to, to));
            return false;
          }

          case "FootnoteReference": {
            const label = doc.sliceString(from + 2, to - 1);
            if (active(from, to)) {
              ranges.push(mark("cm-md-footnote-ref-raw").range(from, to));
            } else {
              const number = footnoteNumbers(state).get(label) ?? label;
              ranges.push(Decoration.replace({ widget: new FootnoteWidget(String(number), label, false, options) }).range(from, to));
            }
            return false;
          }

          case "FootnoteDefinition": {
            addLines(from, to, "cm-md-footnote");
            const label = node.getChild("FootnoteLabel");
            if (!label || active(from, to)) return;
            const text = slice(label);
            const number = footnoteNumbers(state).get(text) ?? text;
            ranges.push(Decoration.replace({ widget: new FootnoteWidget(String(number), text, true, options) }).range(from, spaceAfter(label.to + 2)));
            return;
          }

          case "Autolink": {
            const marks = children(node).filter((child) => child.name === "LinkMark");
            if (marks.length === 2 && marks[0].to < marks[1].from) ranges.push(link(doc.sliceString(marks[0].to, marks[1].from), reading).range(marks[0].to, marks[1].from));
            if (marks.length === 2 && !active(from, to)) ranges.push(hide.range(marks[0].from, marks[0].to), hide.range(marks[1].from, marks[1].to));
            return false;
          }

          case "URL":
            ranges.push(link(doc.sliceString(from, to), reading).range(from, to));
            return;

          case "Blockquote": {
            const first = doc.lineAt(from);
            const markerStart = from + /^>[ \t]?/.exec(doc.sliceString(from, first.to))[0].length;
            const callout = CALLOUT.exec(doc.sliceString(markerStart, first.to));
            if (!callout) {
              addLines(from, to, "cm-md-quote");
              return;
            }
            // "> [!warning] Title": the marker becomes an icon, followed by the title or the type's name.
            const family = calloutFamily(callout[1]);
            addLines(from, to, `cm-md-callout cm-md-callout-${family}`);
            addLines(from, from, "cm-md-callout-title");
            addLines(to, to, "cm-md-callout-end");
            calloutMarks.add(markerStart);
            const markerEnd = markerStart + callout[0].length;
            if (active(first.from, first.to)) {
              ranges.push(mark("cm-md-callout-raw").range(markerStart, markerStart + callout[0].trimEnd().length));
            } else {
              const name = markerEnd < first.to ? "" : callout[1][0].toUpperCase() + callout[1].slice(1).toLowerCase();
              ranges.push(Decoration.replace({ widget: new CalloutWidget(family, name) }).range(markerStart, markerEnd));
            }
            return;
          }

          case "QuoteMark":
            if (!active(from, from)) ranges.push(hide.range(from, spaceAfter(to)));
            return;

          case "ListItem": {
            // Lists inside quotes keep the quote's own indentation.
            if (inside(node, "Blockquote")) return;
            const depth = Math.min(listDepth(node), MAX_LIST_DEPTH);
            const first = doc.lineAt(from).number;
            const start = Math.max(first, doc.lineAt(visible.from).number);
            const end = Math.min(doc.lineAt(to).number, doc.lineAt(visible.to).number);
            for (let number = start; number <= end; number++) listLines.set(doc.line(number).from, { depth, first: number === first });
            return;
          }

          case "ListMark": {
            const item = node.parent;
            const zone = spaceAfter(to);
            if (item?.parent?.name === "OrderedList") {
              ranges.push(number.range(from, zone));
              return;
            }
            if (item?.parent?.name !== "BulletList") return;
            const marker = item.getChild("Task")?.getChild("TaskMarker");
            if (marker) {
              // The checkbox stands in for the bullet, and both reveal together when the cursor reaches them.
              const end = spaceAfter(marker.to);
              if (!touching(from, marker.to)) {
                const checked = /x/i.test(doc.sliceString(marker.from, marker.to));
                ranges.push((checked ? tasks[+reading].done : tasks[+reading].open).range(from, end));
              }
              return;
            }
            // Raw and rendered bullets share one width, so text doesn't move when the cursor arrives.
            ranges.push((active(from, from) ? rawBullet : bullet).range(from, zone));
            return;
          }

          case "TaskMarker": {
            const checked = /x/i.test(doc.sliceString(from, to));
            const taskNode = node.parent;
            const inBullets = taskNode?.parent?.parent?.name === "BulletList";
            if (!inBullets && !touching(from, to)) ranges.push((checked ? tasks[+reading].done : tasks[+reading].open).range(from, to));
            if (checked && taskNode && spaceAfter(to) < taskNode.to) ranges.push(mark("cm-md-task-done").range(spaceAfter(to), taskNode.to));
            return;
          }

          case "HorizontalRule":
            addLines(from, to, "cm-md-rule-line");
            if (!active(from, to)) ranges.push(rule.range(from, to));
            return;

          case "FencedCode":
          case "CodeBlock": {
            addLines(from, to, "cm-md-codeblock");
            const first = doc.lineAt(from);
            const last = doc.lineAt(to);
            for (let number = first.number; number <= last.number; number++) codeLines.add(doc.line(number).from);
            ranges.push(lineClass("cm-md-codeblock-first").range(first.from));
            ranges.push(lineClass("cm-md-codeblock-last").range(last.from));
            if (name === "FencedCode") {
              for (const child of children(node)) {
                if (child.name === "CodeMark" || child.name === "CodeInfo") ranges.push(mark("cm-md-fence").range(child.from, child.to));
              }
            }
            return false;
          }

          case "Table":
            addLines(from, to, "cm-md-table");
            return;

          // HTML that renders nothing, such as a closing tag on its own line, stays as quiet source.
          case "HTMLBlock":
          case "CommentBlock":
            if (name === "CommentBlock" || !showsHtml(doc.sliceString(doc.lineAt(from).from, doc.lineAt(to).to))) addLines(from, to, "cm-md-html-raw");
            return false;

          default:
            return;
        }
      },
    });
  }

  // List layout: indentation becomes padding by nesting depth, with guides for each parent level.
  // The first line of an item hangs its marker, so wrapped text lines up with the text above it.
  for (const [from, { depth, first }] of listLines) {
    if (codeLines.has(from)) continue;
    const line = doc.lineAt(from);
    const indent = /^[ \t]*/.exec(line.text)[0].length;
    if (indent && indent < line.length) ranges.push(hide.range(line.from, line.from + indent));
    ranges.push(lineClass(`cm-md-li cm-md-li-${depth}${first ? "" : " cm-md-li-cont"}`).range(line.from));
  }
  return Decoration.set(ranges, true);
}

const inlinePreview = (options) => ViewPlugin.fromClass(class {
  constructor(view) { this.decorations = build(view, options); }
  update(update) {
    if (update.docChanged || update.viewportChanged || update.selectionSet || update.focusChanged
      || syntaxTree(update.startState) !== syntaxTree(update.state) || foldedRanges(update.startState) !== foldedRanges(update.state)
      || update.startState.facet(readingMode) !== update.state.facet(readingMode)) {
      this.decorations = build(update.view, options);
    }
  }
}, { decorations: (value) => value.decorations });

// What a position links to: { url }, { wiki } for "[[target]]", or { footnote, definition }.
function targetAt(state, pos) {
  for (let node = syntaxTree(state).resolveInner(pos, 1); node; node = node.parent) {
    switch (node.name) {
      case "Link": case "Image": case "Autolink": case "URL": {
        const urlNode = node.name === "URL" ? node : node.getChild("URL");
        return urlNode ? { url: state.sliceDoc(urlNode.from, urlNode.to) } : null;
      }
      case "WikiLink": case "WikiEmbed": {
        const target = node.getChild("WikiLinkTarget");
        return target ? { wiki: state.sliceDoc(target.from, target.to).trim() } : null;
      }
      case "FootnoteReference": return { footnote: state.sliceDoc(node.from + 2, node.to - 1), definition: true };
      case "FootnoteDefinition": {
        const label = node.getChild("FootnoteLabel");
        return label && pos <= label.to + 2 ? { footnote: state.sliceDoc(label.from, label.to), definition: false } : null;
      }
      default:
    }
  }
  return null;
}

// Web and mail links open in a new tab, "#heading" links scroll within the file,
// and anything else is a path the host resolves against the open file.
function follow(view, url, onOpenLink) {
  if (!url) return false;
  if (/^(https?:|mailto:)/i.test(url)) {
    window.open(url, "_blank", "noopener,noreferrer");
    return true;
  }
  if (url.startsWith("#")) return revealAnchor(view, url.slice(1));
  if (/^[a-z][a-z\d+.-]*:/i.test(url) || url.startsWith("//")) return false;
  onOpenLink?.(url);
  return Boolean(onOpenLink);
}

// "[[Note#Heading]]" opens Note at that heading. "[[#Heading]]" scrolls within the file. The host
// finds the note by name, so the editor passes the heading as an anchor.
function followWiki(view, target, onOpenLink) {
  const hash = target.indexOf("#");
  const file = (hash >= 0 ? target.slice(0, hash) : target).trim();
  const heading = hash >= 0 ? target.slice(hash + 1).split("#").pop().trim() : "";
  const fragment = heading && !heading.startsWith("^") ? slug(heading) : "";
  if (!file) return fragment ? revealAnchor(view, fragment) : false;
  onOpenLink?.(fragment ? `${file}#${fragment}` : file, { wiki: true });
  return Boolean(onOpenLink);
}

function go(view, target, onOpenLink) {
  if (!target) return false;
  if (target.url !== undefined) return follow(view, target.url, onOpenLink);
  if (target.wiki !== undefined) return followWiki(view, target.wiki, onOpenLink);
  const pos = findFootnote(view.state, target.footnote, target.definition);
  if (pos === null) return false;
  revealPosition(view, pos, "center");
  return true;
}

// onOpenLink(url, { wiki }) opens a relative link or a wiki link, resolveImage(url, { wiki }) returns
// a loadable address for a local image or null, and onTag(name) handles a click on a tag in the properties block.
export function livePreview({ onOpenLink, resolveImage, onTag } = {}) {
  const options = {
    resolveImage, onTag,
    follow: (view, url) => follow(view, url, onOpenLink),
    go: (view, target) => go(view, target, onOpenLink),
  };
  const openLinks = EditorView.domEventHandlers({
    mousedown(event, view) {
      if (event.button !== 0 || !followsClick(event)) return false;
      // A plain click in Reading mode follows only the link text itself.
      const linkText = event.metaKey || event.ctrlKey ? null : event.target.closest?.(".cm-md-link");
      if (!(event.metaKey || event.ctrlKey) && !linkText) return false;
      const pos = linkText ? view.posAtDOM(linkText) : view.posAtCoords({ x: event.clientX, y: event.clientY });
      if (pos == null) return false;
      const target = targetAt(view.state, pos);
      if (!target) return false;
      event.preventDefault();
      go(view, target, onOpenLink);
      return true;
    },
  });
  // Mod-Enter opens the link under the cursor, for keyboard users.
  const openKey = Prec.high(keymap.of([{
    key: "Mod-Enter",
    run: (view) => go(view, targetAt(view.state, view.state.selection.main.head), onOpenLink),
  }]));
  return [inlinePreview(options), blockPreview(options), openLinks, openKey, EditorView.editorAttributes.of({ class: "cm-live-preview" })];
}
