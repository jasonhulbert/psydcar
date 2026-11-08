// Markdown live preview: the document text never changes, but syntax marks are hidden and
// rendered as formatting until the cursor reaches them. Revealing marks on the lines being
// edited keeps every character reachable without a separate source mode.

import { syntaxTree } from "@codemirror/language";
import { Decoration, EditorView, ViewPlugin, WidgetType } from "@codemirror/view";

const hide = Decoration.replace({});
const lineClass = (name) => Decoration.line({ class: name });
const mark = (name) => Decoration.mark({ class: name });
const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
const link = Decoration.mark({ class: "cm-md-link", attributes: { title: `${isMac ? "⌘" : "Ctrl"}-click to open web links` } });

class TaskWidget extends WidgetType {
  constructor(checked) { super(); this.checked = checked; }
  eq(other) { return other.checked === this.checked; }
  toDOM(view) {
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "cm-md-task";
    box.checked = this.checked;
    box.setAttribute("aria-label", this.checked ? "Mark task as open" : "Mark task as done");
    box.addEventListener("mousedown", (event) => event.preventDefault());
    box.addEventListener("click", (event) => {
      event.preventDefault();
      const at = view.posAtDOM(box);
      const marker = view.state.sliceDoc(at, at + 3);
      if (!/^\[[ xX]\]$/.test(marker)) return;
      view.dispatch({ changes: { from: at + 1, to: at + 2, insert: marker[1] === " " ? "x" : " " } });
    });
    return box;
  }
}

class BulletWidget extends WidgetType {
  eq() { return true; }
  toDOM() {
    const bullet = document.createElement("span");
    bullet.className = "cm-md-bullet";
    bullet.textContent = "•";
    return bullet;
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

const task = { open: Decoration.replace({ widget: new TaskWidget(false) }), done: Decoration.replace({ widget: new TaskWidget(true) }) };
const bullet = Decoration.replace({ widget: new BulletWidget() });
const rule = Decoration.replace({ widget: new RuleWidget() });

const HEADINGS = { ATXHeading1: 1, ATXHeading2: 2, ATXHeading3: 3, ATXHeading4: 4, ATXHeading5: 5, ATXHeading6: 6, SetextHeading1: 1, SetextHeading2: 2 };

function build(view) {
  const { state } = view;
  const doc = state.doc;
  const ranges = [];
  const lines = new Set();
  const focused = view.hasFocus;

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
          const textEnd = name.startsWith("Setext") ? doc.lineAt(from).to : to;
          addLines(from, textEnd, `cm-md-heading cm-md-h${level}`);
          if (active(from, to)) return;
          for (const child of children(node)) {
            if (child.name !== "HeaderMark") continue;
            if (name.startsWith("Setext")) ranges.push(hide.range(child.from, child.to));
            else if (child.from === from) ranges.push(hide.range(child.from, spaceAfter(child.to)));
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

          case "Link":
          case "Image": {
            const marks = children(node).filter((child) => child.name === "LinkMark");
            if (marks.length < 2) return;
            const label = [marks[0].to, marks[1].from];
            if (label[0] < label[1]) ranges.push((name === "Image" ? mark("cm-md-image") : link).range(label[0], label[1]));
            if (!active(from, to)) {
              ranges.push(hide.range(marks[0].from, marks[0].to), hide.range(marks[1].from, to));
            }
            return false;
          }

          case "Autolink": {
            const marks = children(node).filter((child) => child.name === "LinkMark");
            if (marks.length === 2 && marks[0].to < marks[1].from) ranges.push(link.range(marks[0].to, marks[1].from));
            if (marks.length === 2 && !active(from, to)) ranges.push(hide.range(marks[0].from, marks[0].to), hide.range(marks[1].from, marks[1].to));
            return false;
          }

          case "URL":
            ranges.push(link.range(from, to));
            return;

          case "Blockquote":
            addLines(from, to, "cm-md-quote");
            return;

          case "QuoteMark":
            if (!active(from, from)) ranges.push(hide.range(from, spaceAfter(to)));
            return;

          case "ListMark": {
            const item = node.parent;
            const isTask = item?.getChild("Task");
            if (item?.parent?.name === "BulletList" && !active(from, from)) {
              ranges.push(isTask ? hide.range(from, spaceAfter(to)) : bullet.range(from, to));
            } else if (item?.parent?.name === "OrderedList") {
              ranges.push(mark("cm-md-list-number").range(from, to));
            }
            return;
          }

          case "TaskMarker": {
            const checked = /x/i.test(doc.sliceString(from, to));
            if (!touching(from, to)) ranges.push((checked ? task.done : task.open).range(from, to));
            const item = node.parent;
            if (checked && item && spaceAfter(to) < item.to) ranges.push(mark("cm-md-task-done").range(spaceAfter(to), item.to));
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
  return Decoration.set(ranges, true);
}

const plugin = ViewPlugin.fromClass(class {
  constructor(view) { this.decorations = build(view); }
  update(update) {
    if (update.docChanged || update.viewportChanged || update.selectionSet || update.focusChanged
      || syntaxTree(update.startState) !== syntaxTree(update.state)) {
      this.decorations = build(update.view);
    }
  }
}, { decorations: (value) => value.decorations });

// Cmd/Ctrl-click opens web links in a new tab. Local relative links stay plain text.
const openLinks = EditorView.domEventHandlers({
  mousedown(event, view) {
    if (!(event.metaKey || event.ctrlKey) || event.button !== 0) return false;
    const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
    if (pos == null) return false;
    for (let node = syntaxTree(view.state).resolveInner(pos, 1); node; node = node.parent) {
      if (node.name !== "Link" && node.name !== "Autolink" && node.name !== "URL") continue;
      const urlNode = node.name === "URL" ? node : node.getChild("URL");
      const url = urlNode ? view.state.sliceDoc(urlNode.from, urlNode.to) : "";
      if (!/^https?:\/\//i.test(url)) return false;
      event.preventDefault();
      window.open(url, "_blank", "noopener,noreferrer");
      return true;
    }
    return false;
  },
});

export const livePreview = [plugin, openLinks, EditorView.editorAttributes.of({ class: "cm-live-preview" })];
