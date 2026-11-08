// Markdown live preview: the document text never changes, but syntax marks are hidden and
// rendered as formatting until the cursor reaches them. Revealing marks on the lines being
// edited keeps every character reachable without a separate source mode.

import { syntaxTree } from "@codemirror/language";
import { Prec } from "@codemirror/state";
import { Decoration, EditorView, ViewPlugin, WidgetType, keymap } from "@codemirror/view";
import { blockPreview, imageElement } from "./blocks.js";

const hide = Decoration.replace({});
const lineClass = (name) => Decoration.line({ class: name });
const mark = (name) => Decoration.mark({ class: name });
const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
const openHint = `${isMac ? "⌘" : "Ctrl"}-click to open`;
const link = (url) => Decoration.mark({ class: "cm-md-link", attributes: { title: url ? `${url}\n${openHint}` : openHint } });
const MAX_LIST_DEPTH = 8;

class TaskWidget extends WidgetType {
  constructor(checked) { super(); this.checked = checked; }
  eq(other) { return other.checked === this.checked; }
  toDOM(view) {
    const wrap = document.createElement("span");
    wrap.className = "cm-md-list-mark cm-md-task-mark";
    const box = wrap.appendChild(document.createElement("input"));
    box.type = "checkbox";
    box.className = "cm-md-task";
    box.checked = this.checked;
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
  constructor(url, alt, options) { super(); this.url = url; this.alt = alt; this.options = options; }
  eq(other) { return other.url === this.url && other.alt === this.alt; }
  toDOM(view) {
    const wrap = document.createElement("span");
    wrap.className = "cm-md-img-wrap";
    wrap.append(imageElement(this.url, this.alt, { ...this.options, onLoad: () => view.requestMeasure() }));
    // Cmd/Ctrl-click opens the image file. A plain click is left to the editor, which places the cursor.
    wrap.addEventListener("mousedown", (event) => {
      if (event.button !== 0 || !(event.metaKey || event.ctrlKey)) return;
      event.preventDefault();
      this.options.follow(view, this.url);
    });
    return wrap;
  }
  ignoreEvent(event) { return event.metaKey || event.ctrlKey; }
}

const task = { open: Decoration.replace({ widget: new TaskWidget(false) }), done: Decoration.replace({ widget: new TaskWidget(true) }) };
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

const HEADINGS = { ATXHeading1: 1, ATXHeading2: 2, ATXHeading3: 3, ATXHeading4: 4, ATXHeading5: 5, ATXHeading6: 6, SetextHeading1: 1, SetextHeading2: 2 };

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
  const focused = view.hasFocus;
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
            const marks = children(node).filter((child) => child.name === "LinkMark");
            if (marks.length < 2) return;
            const label = [marks[0].to, marks[1].from];
            const url = node.getChild("URL");
            const target = url ? doc.sliceString(url.from, url.to) : "";
            if (label[0] < label[1]) ranges.push(link(target).range(label[0], label[1]));
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

          case "Autolink": {
            const marks = children(node).filter((child) => child.name === "LinkMark");
            if (marks.length === 2 && marks[0].to < marks[1].from) ranges.push(link(doc.sliceString(marks[0].to, marks[1].from)).range(marks[0].to, marks[1].from));
            if (marks.length === 2 && !active(from, to)) ranges.push(hide.range(marks[0].from, marks[0].to), hide.range(marks[1].from, marks[1].to));
            return false;
          }

          case "URL":
            ranges.push(link(doc.sliceString(from, to)).range(from, to));
            return;

          case "Blockquote":
            addLines(from, to, "cm-md-quote");
            return;

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
                ranges.push((checked ? task.done : task.open).range(from, end));
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
            if (!inBullets && !touching(from, to)) ranges.push((checked ? task.done : task.open).range(from, to));
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
      || syntaxTree(update.startState) !== syntaxTree(update.state)) {
      this.decorations = build(update.view, options);
    }
  }
}, { decorations: (value) => value.decorations });

// Heading anchors follow GitHub's rules: lowercase, punctuation removed, spaces become hyphens.
export function slug(text) {
  return text.trim().toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, "").replace(/\s/g, "-");
}

function headingText(state, node) {
  const text = state.sliceDoc(node.from, node.name.startsWith("Setext") ? state.doc.lineAt(node.from).to : node.to);
  return text.replace(/^#{1,6}[ \t]*/, "").replace(/[ \t]+#+[ \t]*$/, "").replace(/[*_~`]/g, "");
}

// Scroll a heading into view by its anchor, without moving the cursor. Returns false when no heading matches.
export function revealAnchor(view, fragment) {
  let wanted;
  try { wanted = decodeURIComponent(fragment).toLowerCase(); } catch { wanted = fragment.toLowerCase(); }
  const seen = new Map();
  let found = null;
  syntaxTree(view.state).iterate({
    enter(ref) {
      if (found !== null) return false;
      if (!(ref.name in HEADINGS)) return;
      // Repeated headings get -1, -2 suffixes, as on GitHub.
      const base = slug(headingText(view.state, ref.node));
      const count = seen.get(base) || 0;
      seen.set(base, count + 1);
      if ((count ? `${base}-${count}` : base) === wanted) found = ref.from;
      return false;
    },
  });
  if (found === null) return false;
  view.dispatch({ effects: EditorView.scrollIntoView(found, { y: "start", yMargin: 24 }) });
  return true;
}

function linkAt(state, pos) {
  for (let node = syntaxTree(state).resolveInner(pos, 1); node; node = node.parent) {
    if (node.name !== "Link" && node.name !== "Image" && node.name !== "Autolink" && node.name !== "URL") continue;
    const urlNode = node.name === "URL" ? node : node.getChild("URL");
    return urlNode ? state.sliceDoc(urlNode.from, urlNode.to) : null;
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

// onOpenLink(url) opens a relative link, resolveImage(url) returns a loadable address for a local
// image or null, and onTag(name) handles a click on a tag in the properties block.
export function livePreview({ onOpenLink, resolveImage, onTag } = {}) {
  const options = { resolveImage, onTag, follow: (view, url) => follow(view, url, onOpenLink) };
  const openLinks = EditorView.domEventHandlers({
    mousedown(event, view) {
      if (!(event.metaKey || event.ctrlKey) || event.button !== 0) return false;
      const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
      if (pos == null) return false;
      const url = linkAt(view.state, pos);
      if (url === null) return false;
      event.preventDefault();
      follow(view, url, onOpenLink);
      return true;
    },
  });
  // Mod-Enter opens the link under the cursor, for keyboard users.
  const openKey = Prec.high(keymap.of([{
    key: "Mod-Enter",
    run: (view) => follow(view, linkAt(view.state, view.state.selection.main.head), onOpenLink),
  }]));
  return [inlinePreview(options), blockPreview(options), openLinks, openKey, EditorView.editorAttributes.of({ class: "cm-live-preview" })];
}
