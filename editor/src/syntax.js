// Markdown syntax beyond GFM that notes commonly use: footnotes and wiki links. Each is a Lezer
// Markdown extension, so Source mode highlights it and live preview can render it.

import { parser as baseParser } from "@lezer/markdown";
import { tags } from "@lezer/highlight";

const FOOTNOTE_START = /^\[\^([^\]\s]+)\]:[ \t]*/;
// The parser that reads link reference definitions, such as "[label]: https://example.com".
const linkReference = baseParser.leafBlockParsers[baseParser.blockNames.indexOf("LinkReference")];

// A footnote definition is a paragraph that starts with "[^label]:". It runs to the end of the
// paragraph, so continuation lines belong to it.
class FootnoteParser {
  nextLine() { return false; }
  finish(cx, leaf) {
    const match = FOOTNOTE_START.exec(leaf.content);
    if (!match) return false;
    const start = leaf.start;
    const labelEnd = start + 2 + match[1].length;
    cx.addLeafElement(leaf, cx.elt("FootnoteDefinition", start, start + leaf.content.length, [
      cx.elt("FootnoteMark", start, start + 2),
      cx.elt("FootnoteLabel", start + 2, labelEnd),
      cx.elt("FootnoteMark", labelEnd, labelEnd + 2),
      ...cx.parser.parseInline(leaf.content.slice(match[0].length), start + match[0].length),
    ]));
    return true;
  }
}

export const footnotes = {
  defineNodes: [
    { name: "FootnoteDefinition", block: true },
    { name: "FootnoteReference", style: tags.link },
    { name: "FootnoteMark", style: tags.processingInstruction },
    { name: "FootnoteLabel", style: tags.labelName },
  ],
  parseBlock: [{
    // Replaces the link reference parser, which would otherwise read "[^1]: text" as a reference definition.
    name: "LinkReference",
    leaf: (cx, leaf) => (FOOTNOTE_START.test(leaf.content) ? new FootnoteParser() : linkReference(cx, leaf)),
    endLeaf: (_cx, line) => FOOTNOTE_START.test(line.text.slice(line.pos)),
  }],
  parseInline: [{
    name: "FootnoteReference",
    before: "Link",
    parse(cx, next, pos) {
      if (next !== 91 || cx.char(pos + 1) !== 94) return -1;
      const match = /^\[\^([^\]\s]+)\]/.exec(cx.slice(pos, Math.min(cx.end, pos + 200)));
      if (!match) return -1;
      const end = pos + match[0].length;
      return cx.addElement(cx.elt("FootnoteReference", pos, end, [
        cx.elt("FootnoteMark", pos, pos + 2), cx.elt("FootnoteLabel", pos + 2, end - 1), cx.elt("FootnoteMark", end - 1, end),
      ]));
    },
  }],
};

// "[[target]]" and "[[target|label]]" link to another note. "![[image.png]]" embeds an image.
function wikiParser(embed) {
  const open = embed ? 3 : 2;
  return (cx, next, pos) => {
    if (embed ? next !== 33 || cx.char(pos + 1) !== 91 || cx.char(pos + 2) !== 91 : next !== 91 || cx.char(pos + 1) !== 91) return -1;
    const match = /^([^[\]|\n]+)(?:(\|)([^[\]\n]*))?\]\]/.exec(cx.slice(pos + open, Math.min(cx.end, pos + 1000)));
    if (!match || !match[1].trim()) return -1;
    const targetEnd = pos + open + match[1].length;
    const end = pos + open + match[0].length;
    const children = [cx.elt("WikiLinkMark", pos, pos + open), cx.elt("WikiLinkTarget", pos + open, targetEnd)];
    if (match[2]) {
      children.push(cx.elt("WikiLinkMark", targetEnd, targetEnd + 1));
      if (match[3]) children.push(cx.elt("WikiLinkLabel", targetEnd + 1, end - 2));
    }
    children.push(cx.elt("WikiLinkMark", end - 2, end));
    return cx.addElement(cx.elt(embed ? "WikiEmbed" : "WikiLink", pos, end, children));
  };
}

export const wikiLinks = {
  defineNodes: [
    "WikiLink",
    "WikiEmbed",
    { name: "WikiLinkMark", style: tags.processingInstruction },
    { name: "WikiLinkTarget", style: tags.link },
    { name: "WikiLinkLabel", style: tags.link },
  ],
  parseInline: [
    { name: "WikiEmbed", before: "Image", parse: wikiParser(true) },
    { name: "WikiLink", before: "Link", parse: wikiParser(false) },
  ],
};

// Callouts are blockquotes whose first line is "[!type]", optionally followed by a title.
// Each type maps to one of five looks.
export const CALLOUT = /^\[!([A-Za-z][\w-]*)\]([+-]?)[ \t]*/;
const CALLOUT_FAMILIES = {
  note: ["note", "info", "todo", "abstract", "summary", "tldr", "example", "quote", "cite"],
  tip: ["tip", "hint", "success", "check", "done"],
  important: ["important", "question", "help", "faq"],
  warning: ["warning", "attention"],
  danger: ["danger", "caution", "error", "failure", "fail", "missing", "bug"],
};
const familyOf = new Map(Object.entries(CALLOUT_FAMILIES).flatMap(([family, names]) => names.map((name) => [name, family])));
export const calloutFamily = (type) => familyOf.get(type.toLowerCase()) || "note";
export const CALLOUT_ICONS = { note: "info", tip: "lightbulb", important: "message-square-warning", warning: "triangle-alert", danger: "octagon-alert" };
