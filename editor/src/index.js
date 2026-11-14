// Entry point for the dashboard editor bundle. Exposes window.PsydcarEditor.create().

import { closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { bracketMatching, codeFolding, foldGutter, foldKeymap, indentOnInput, syntaxHighlighting } from "@codemirror/language";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import { Compartment, EditorState } from "@codemirror/state";
import {
  EditorView, crosshairCursor, drawSelection, dropCursor, highlightActiveLine, highlightActiveLineGutter,
  highlightSpecialChars, keymap, lineNumbers, placeholder, rectangularSelection,
} from "@codemirror/view";
import { classHighlighter, tagHighlighter, tags } from "@lezer/highlight";
import { fenceLanguages, languageForPath } from "./languages.js";
import { readingMode } from "./blocks.js";
import { frontMatter } from "./front-matter.js";
import { formattedEditing } from "./formatted-editing.js";
import { livePreview } from "./live-preview.js";
import { outline, revealAnchor, revealPosition } from "./outline.js";
import { footnotes, wikiLinks } from "./syntax.js";

const PROSE = new Set(["md", "markdown", "txt", "rst"]);
const MARKDOWN = new Set(["md", "markdown"]);
const MODES = new Set(["live", "reading", "source"]);

// mode is "live", "reading", or "source", and applies to Markdown files. onOutline({ headings, current })
// reports a Markdown file's headings and the one at the top of the editor whenever either changes.
function create({ parent, doc = "", path, mode = "live", label, nonce, onChange, onSave, onOpenLink, resolveImage, onTag, onOutline, onStatus }) {
  const extension = path.split(".").pop().toLowerCase();
  const isMarkdown = MARKDOWN.has(extension);
  const isProse = PROSE.has(extension);
  const code = isMarkdown ? null : languageForPath(path);
  const modeSlot = new Compartment();
  const themeSlot = new Compartment();
  const scheme = window.matchMedia("(prefers-color-scheme: dark)");
  let currentMode = MODES.has(mode) ? mode : "live";
  const previewExtension = isMarkdown ? livePreview({ onOpenLink, resolveImage, onTag }) : [];
  // Reading mode renders the document without editable replacements. The content
  // stays focusable, so the keyboard can scroll it and open links with Mod-Enter.
  const readingExtension = [
    previewExtension, readingMode.of(true), EditorState.readOnly.of(true), EditorView.editable.of(false),
    EditorView.contentAttributes.of({ tabindex: "0" }), EditorView.editorAttributes.of({ class: "cm-reading" }),
  ];
  const modeExtension = () => (!isMarkdown || currentMode === "source" ? [] : currentMode === "reading" ? readingExtension : formattedEditing({ onOpenLink }));
  const reportStatus = (state) => {
    const selection = state.selection.main;
    const line = state.doc.lineAt(selection.head);
    onStatus?.({ line: line.number, column: selection.head - line.from + 1, selected: selection.to - selection.from });
  };

  const extensions = [
    // CodeMirror adds its base styles in a <style> element; the page's CSP admits it by nonce.
    nonce ? EditorView.cspNonce.of(nonce) : [],
    highlightSpecialChars(),
    history(),
    drawSelection(),
    dropCursor(),
    EditorState.allowMultipleSelections.of(true),
    indentOnInput(),
    syntaxHighlighting(classHighlighter),
    syntaxHighlighting(tagHighlighter([{ tag: tags.strikethrough, class: "tok-strikethrough" }])),
    bracketMatching(),
    // Prose follows text-editor typing conventions. Automatic bracket insertion is for code.
    isProse ? [] : closeBrackets(),
    rectangularSelection(),
    crosshairCursor(),
    highlightSelectionMatches(),
    search({ top: true }),
    keymap.of([
      { key: "Mod-s", preventDefault: true, run: () => { onSave?.(); return true; } },
      ...(isProse ? [] : closeBracketsKeymap), ...defaultKeymap, ...searchKeymap, ...historyKeymap, ...foldKeymap,
      ...(isProse ? [] : [indentWithTab]),
    ]),
    EditorView.contentAttributes.of({ "aria-label": label || `Edit ${path}`, spellcheck: isProse ? "true" : "false" }),
    EditorView.editorAttributes.of({ class: `${isProse ? "cm-prose" : "cm-code"}${isMarkdown ? " cm-markdown" : ""}` }),
    themeSlot.of(EditorView.darkTheme.of(scheme.matches)),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) onChange?.();
      if (update.docChanged || update.selectionSet) reportStatus(update.state);
    }),
  ];
  if (isProse) {
    extensions.push(EditorView.lineWrapping, placeholder("Start writing…"));
  } else {
    extensions.push(lineNumbers(), highlightActiveLineGutter(), foldGutter(), highlightActiveLine());
  }
  if (isMarkdown) {
    extensions.push(
      markdown({ base: markdownLanguage, codeLanguages: fenceLanguages, extensions: [frontMatter, footnotes, wikiLinks] }),
      lineNumbers(), codeFolding(), onOutline ? outline(onOutline) : [], modeSlot.of(modeExtension()),
    );
  } else if (code) {
    extensions.push(code.support);
  }

  const makeState = (text) => EditorState.create({ doc: text, extensions });
  const view = new EditorView({ parent, state: makeState(doc) });
  reportStatus(view.state);
  let saved = view.state.doc;
  const followScheme = () => view.dispatch({ effects: themeSlot.reconfigure(EditorView.darkTheme.of(scheme.matches)) });
  scheme.addEventListener("change", followScheme);

  return {
    view,
    isMarkdown,
    language: isMarkdown ? "Markdown" : code?.name || "Plain text",
    getText: () => view.state.doc.toString(),
    // Snapshot the document before a save starts, then mark that snapshot saved, so typing
    // during the request still counts as unsaved.
    snapshot: () => view.state.doc,
    isDirty: () => !view.state.doc.eq(saved),
    markSaved: (doc = view.state.doc) => { saved = doc; },
    // Replace the whole document, for example after reloading the file from disk. Clears undo history.
    load(text) {
      view.setState(makeState(text));
      view.dispatch({ effects: [themeSlot.reconfigure(EditorView.darkTheme.of(scheme.matches)),
        ...(isMarkdown ? [modeSlot.reconfigure(modeExtension())] : [])] });
      saved = view.state.doc;
    },
    get mode() { return isMarkdown ? currentMode : "source"; },
    setMode(next) {
      if (!isMarkdown || !MODES.has(next)) return;
      currentMode = next;
      view.dispatch({ effects: modeSlot.reconfigure(modeExtension()) });
    },
    // Scroll to the heading a "#fragment" names. Returns false when there's no such heading.
    revealAnchor: (fragment) => isMarkdown && revealAnchor(view, fragment),
    // Scroll to a position from the outline, opening a folded section if it hides it.
    revealPosition: (pos) => revealPosition(view, Math.min(pos, view.state.doc.length)),
    focus: () => view.focus(),
    destroy: () => { scheme.removeEventListener("change", followScheme); view.destroy(); },
  };
}

window.PsydcarEditor = { create };
