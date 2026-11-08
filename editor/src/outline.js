// Headings: anchors for "#fragment" links, the outline the dashboard shows, and moving to a heading.

import { foldedRanges, syntaxTree, unfoldEffect } from "@codemirror/language";
import { EditorView, ViewPlugin } from "@codemirror/view";

export const HEADINGS = { ATXHeading1: 1, ATXHeading2: 2, ATXHeading3: 3, ATXHeading4: 4, ATXHeading5: 5, ATXHeading6: 6, SetextHeading1: 1, SetextHeading2: 2 };

// Heading anchors follow GitHub's rules: lowercase, punctuation removed, spaces become hyphens.
export function slug(text) {
  return text.trim().toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, "").replace(/\s/g, "-");
}

// A heading's text without its marks and inline syntax, as a reader sees it.
export function headingText(state, node) {
  const text = state.sliceDoc(node.from, node.name.startsWith("Setext") ? state.doc.lineAt(node.from).to : node.to);
  return text.replace(/^#{1,6}[ \t]*/, "").replace(/[ \t]+#+[ \t]*$/, "")
    .replace(/!?\[\[([^\]|]*)\|([^\]]*)\]\]/g, "$2").replace(/!?\[\[([^\]]*)\]\]/g, "$1")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/\[\^[^\]]*\]/g, "")
    .replace(/[*_~`]/g, "").trim();
}

// Top-level headings in document order, with their anchors. Repeated headings get -1, -2 suffixes, as on GitHub.
export function headings(state) {
  const list = [];
  const seen = new Map();
  for (let node = syntaxTree(state).topNode.firstChild; node; node = node.nextSibling) {
    if (!(node.name in HEADINGS)) continue;
    const text = headingText(state, node);
    const base = slug(text);
    const count = seen.get(base) || 0;
    seen.set(base, count + 1);
    list.push({ level: HEADINGS[node.name], text, from: node.from, anchor: count ? `${base}-${count}` : base });
  }
  return list;
}

// Scroll to a position without moving the cursor, opening any folded section that hides it.
export function revealPosition(view, pos, y = "start") {
  const effects = [];
  foldedRanges(view.state).between(pos, pos + 1, (from, to) => { if (from <= pos && to >= pos) effects.push(unfoldEffect.of({ from, to })); });
  effects.push(EditorView.scrollIntoView(pos, { y, yMargin: 24 }));
  view.dispatch({ effects });
}

// Scroll a heading into view by its anchor. Returns false when no heading matches.
export function revealAnchor(view, fragment) {
  let wanted;
  try { wanted = decodeURIComponent(fragment).toLowerCase(); } catch { wanted = fragment.toLowerCase(); }
  const heading = headings(view.state).find((item) => item.anchor === wanted);
  if (!heading) return false;
  revealPosition(view, heading.from);
  return true;
}

// Reports the headings and the one at the top of the editor whenever either changes. The list is
// reported right away. Finding the current heading reads layout, so it waits for CodeMirror's measure pass.
export function outline(onChange) {
  return ViewPlugin.fromClass(class {
    constructor(view) {
      this.view = view;
      this.list = [];
      this.key = "";
      this.current = -1;
      this.measure = {
        key: this,
        read: (measured) => this.currentHeading(measured),
        write: (current) => { if (current !== this.current) { this.current = current; this.report(); } },
      };
      this.onScroll = () => view.requestMeasure(this.measure);
      view.scrollDOM.addEventListener("scroll", this.onScroll, { passive: true });
      this.refresh(view.state);
    }
    update(update) {
      if (update.docChanged || syntaxTree(update.startState) !== syntaxTree(update.state)) this.refresh(update.state);
      else if (update.geometryChanged || update.viewportChanged) this.onScroll();
    }
    refresh(state) {
      this.list = headings(state);
      const key = this.list.map((item) => `${item.level}:${item.from}:${item.text}`).join("\n");
      if (key !== this.key) { this.key = key; this.report(); }
      this.onScroll();
    }
    // The heading whose section is at the top of the editor, a little way down so a heading just scrolled to counts.
    currentHeading(view) {
      const top = view.scrollDOM.getBoundingClientRect().top - view.documentTop + 32;
      const at = top <= 0 ? -1 : view.lineBlockAtHeight(top).from;
      let current = -1;
      for (let index = 0; index < this.list.length && this.list[index].from <= at; index++) current = index;
      return current;
    }
    report() { onChange({ headings: this.list, current: this.current }); }
    destroy() { this.view.scrollDOM.removeEventListener("scroll", this.onScroll); }
  });
}
