// Entry point for the dashboard editor bundle. Exposes window.PsydcarEditor.create().

import { closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { bracketMatching, foldGutter, foldKeymap, indentOnInput, syntaxHighlighting } from "@codemirror/language";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import { Compartment, EditorState } from "@codemirror/state";
import {
  EditorView, crosshairCursor, drawSelection, dropCursor, highlightActiveLine, highlightActiveLineGutter,
  highlightSpecialChars, keymap, lineNumbers, placeholder, rectangularSelection,
} from "@codemirror/view";
import { classHighlighter, tagHighlighter, tags } from "@lezer/highlight";
import { fenceLanguages, languageForPath } from "./languages.js";
import { livePreview } from "./live-preview.js";

const PROSE = new Set(["md", "markdown", "txt", "rst"]);
const MARKDOWN = new Set(["md", "markdown"]);

function create({ parent, doc = "", path, preview = true, label, nonce, onChange, onSave }) {
  const extension = path.split(".").pop().toLowerCase();
  const isMarkdown = MARKDOWN.has(extension);
  const isProse = PROSE.has(extension);
  const code = isMarkdown ? null : languageForPath(path);
  const previewSlot = new Compartment();
  const themeSlot = new Compartment();
  const scheme = window.matchMedia("(prefers-color-scheme: dark)");
  let previewOn = isMarkdown && preview;

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
    closeBrackets(),
    rectangularSelection(),
    crosshairCursor(),
    highlightSelectionMatches(),
    search({ top: true }),
    keymap.of([
      { key: "Mod-s", preventDefault: true, run: () => { onSave?.(); return true; } },
      ...closeBracketsKeymap, ...defaultKeymap, ...searchKeymap, ...historyKeymap, ...foldKeymap, indentWithTab,
    ]),
    EditorView.contentAttributes.of({ "aria-label": label || `Edit ${path}`, spellcheck: isProse ? "true" : "false" }),
    EditorView.editorAttributes.of({ class: `${isProse ? "cm-prose" : "cm-code"}${isMarkdown ? " cm-markdown" : ""}` }),
    themeSlot.of(EditorView.darkTheme.of(scheme.matches)),
    EditorView.updateListener.of((update) => { if (update.docChanged) onChange?.(); }),
  ];
  if (isProse) {
    extensions.push(EditorView.lineWrapping, placeholder("Start writing…"));
  } else {
    extensions.push(lineNumbers(), highlightActiveLineGutter(), foldGutter(), highlightActiveLine());
  }
  if (isMarkdown) {
    extensions.push(markdown({ base: markdownLanguage, codeLanguages: fenceLanguages }), previewSlot.of(previewOn ? livePreview : []));
  } else if (code) {
    extensions.push(code.support);
  }

  const makeState = (text) => EditorState.create({ doc: text, extensions });
  const view = new EditorView({ parent, state: makeState(doc) });
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
        ...(isMarkdown ? [previewSlot.reconfigure(previewOn ? livePreview : [])] : [])] });
      saved = view.state.doc;
    },
    get preview() { return previewOn; },
    setPreview(on) {
      if (!isMarkdown) return;
      previewOn = Boolean(on);
      view.dispatch({ effects: previewSlot.reconfigure(previewOn ? livePreview : []) });
    },
    focus: () => view.focus(),
    destroy: () => { scheme.removeEventListener("change", followScheme); view.destroy(); },
  };
}

window.PsydcarEditor = { create };
