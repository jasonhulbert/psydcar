// Editing keeps every source character in the same place. Formatting adds marks and
// line styles without replacing text or changing layout when the selection moves.
import { syntaxTree } from "@codemirror/language";
import { Decoration, EditorView, ViewPlugin } from "@codemirror/view";
import { markdownLinks } from "./live-preview.js";
import { HEADINGS } from "./outline.js";

function decorations(view) {
  const ranges = [];
  const seen = new Set();
  const line = (from, to, className) => {
    const first = view.state.doc.lineAt(from).number;
    const last = view.state.doc.lineAt(to).number;
    for (let number = first; number <= last; number++) {
      const at = view.state.doc.line(number).from;
      const key = `${at}:${className}`;
      if (!seen.has(key)) {
        seen.add(key);
        ranges.push(Decoration.line({ class: className }).range(at));
      }
    }
  };
  for (const visible of view.visibleRanges) {
    syntaxTree(view.state).iterate({
      from: visible.from, to: visible.to,
      enter(node) {
        if (node.name in HEADINGS) line(node.from, node.to, `cm-format-heading cm-format-h${HEADINGS[node.name]}`);
        else if (["FencedCode", "CodeBlock", "FrontMatter", "Table", "HTMLBlock"].includes(node.name)) {
          line(Math.max(node.from, visible.from), Math.min(node.to, visible.to), "cm-format-source-block");
          return false;
        } else if (node.name === "Blockquote") line(node.from, node.to, "cm-format-quote");
        return undefined;
      },
    });
  }
  return Decoration.set(ranges, true);
}

export function formattedEditing({ onOpenLink } = {}) {
  return [
    markdownLinks(onOpenLink),
    EditorView.editorAttributes.of({ class: "cm-formatted-editing" }),
    ViewPlugin.fromClass(class {
      constructor(view) { this.decorations = decorations(view); }
      update(update) {
        if (update.docChanged || update.viewportChanged || syntaxTree(update.startState) !== syntaxTree(update.state)) {
          this.decorations = decorations(update.view);
        }
      }
    }, { decorations: value => value.decorations }),
  ];
}
