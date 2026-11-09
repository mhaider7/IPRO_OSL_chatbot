/**
 * format.js — turns Hawk's markdown-style replies into DOM nodes.
 *
 * Safety: builds elements with createElement and plain text nodes only. Nothing is
 * ever parsed as HTML, so model output can't become live markup. Syntax this file
 * doesn't know (tables, quotes, ...) simply stays visible as plain text.
 *
 * Supports what Hawk's replies actually use (sampled 2026-11-08): paragraphs, line
 * breaks, nested `-` / `*` / `+` / `•` and `1.` / `1)` lists (tabs or spaces, blank
 * lines between items), **bold** / __bold__, *italic* / _italic_, `code`,
 * # headings (shown as bold lines), and [text](https://...) links.
 */

const ITEM = /^([-*+•]|(\d{1,9})[.)])[ \t]+(.*)$/;      // checked after the indent is removed
const HEADING = /^#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?$/;

// Replace el's contents with the formatted version of text.
export function formatInto(el, text) {
  el.replaceChildren(buildBlocks(String(text)));
}

// Indent width, counting a tab as reaching the next multiple of 4 columns.
function splitIndent(line) {
  let width = 0, i = 0;
  for (; i < line.length; i++) {
    if (line[i] === " ") width++;
    else if (line[i] === "\t") width += 4 - (width % 4);
    else break;
  }
  return { width, rest: line.slice(i).trimEnd() };
}

// Block rules, one line at a time:
// - A blank line ends a paragraph. Lines in the same paragraph are joined with <br>.
// - A line starting with a list marker is a list item. Its indent decides where it
//   goes: 2+ columns deeper than the current list = a nested list inside the last
//   item; about the same = the same list (a new one if it switches between numbered
//   and bulleted); shallower = back out to the matching outer list.
// - Blank lines between items don't end a list, so numbering continues.
// - Inside a list, indented text (or text right under an item) continues that item;
//   unindented text after a blank line ends all lists and starts a paragraph.
// - "# Heading" lines become a bold line.
function buildBlocks(text) {
  const root = document.createDocumentFragment();
  let lists = [];     // open lists, outermost first: { indent, el, ordered, li }
  let para = null;    // paragraph still accepting lines
  let blank = false;  // the previous line was blank

  const openList = (parent, indent, ordered, number) => {
    const el = document.createElement(ordered ? "ol" : "ul");
    if (ordered && Number(number) !== 1) el.start = Number(number);
    parent.append(el);
    const list = { indent, el, ordered, li: null };
    lists.push(list);
    return list;
  };

  for (const raw of text.replace(/\r\n?/g, "\n").split("\n")) {
    const { width, rest: line } = splitIndent(raw);
    if (!line) { blank = true; para = null; continue; }

    const item = ITEM.exec(line);
    if (item) {
      const ordered = item[2] !== undefined;
      // Indents within one column of a list count as the same level (models mix 3 and 4).
      while (lists.length && width < lists[lists.length - 1].indent - 1) lists.pop();
      const top = lists[lists.length - 1];
      let list;
      if (!top) list = openList(root, width, ordered, item[2]);
      else if (width >= top.indent + 2) list = openList(top.li, width, ordered, item[2]);
      else if (top.ordered === ordered) list = top;
      else {
        lists.pop();
        list = openList(lists.length ? lists[lists.length - 1].li : root, width, ordered, item[2]);
      }
      const li = document.createElement("li");
      appendInline(li, item[3]);
      list.el.append(li);
      list.li = li;
      para = null;
      blank = false;
      continue;
    }

    if (lists.length) {
      // Indented text, or text right under an item, continues that item.
      if (width > 0 || !blank) {
        const li = lists[lists.length - 1].li;
        li.append(document.createElement("br"));
        appendInline(li, line);
        blank = false;
        continue;
      }
      lists = [];   // unindented text after a blank line ends the lists
    }

    const heading = HEADING.exec(line);
    if (heading) {
      const p = document.createElement("p");
      p.className = "md-heading";
      const strong = document.createElement("strong");
      appendInline(strong, heading[1]);
      p.append(strong);
      root.append(p);
      para = null;
    } else {
      if (para) para.append(document.createElement("br"));
      else { para = document.createElement("p"); root.append(para); }
      appendInline(para, line);
    }
    blank = false;
  }
  return root;
}

// ── Inline formatting ─────────────────────────────────────────

const isWord = (c) => c !== undefined && /[\p{L}\p{N}_]/u.test(c);
// Single * or _ (and __) only count away from letters, so snake_case, 2*3*4 and the
// like stay as typed. (Checked by hand: lookbehind would break older Safari.)
const outsideWords = (text, m, mark) => {
  const before = text[m.index - 1], after = text[m.index + m[0].length];
  return !isWord(before) && !isWord(after) && before !== mark && after !== mark;
};

// Earlier rules win when two match at the same place.
const INLINE = [
  { type: "code",   re: /`([^`\n]+)`/g },
  { type: "link",   re: /\[([^\]\n]+)\]\(([^()\s]+)\)/g },
  { type: "strong", re: /\*\*(\S(?:[^\n]*?\S)?)\*\*/g },
  { type: "strong", re: /__(\S(?:[^\n]*?\S)?)__/g, ok: (t, m) => outsideWords(t, m, "_") },
  { type: "em",     re: /\*([^\s*](?:[^*\n]*?[^\s*])?)\*/g, ok: (t, m) => outsideWords(t, m, "*") },
  { type: "em",     re: /_([^\s_](?:[^_\n]*?[^\s_])?)_/g, ok: (t, m) => outsideWords(t, m, "_") },
];

// Finds the next inline element at or after `from`: every rule is tried and the
// earliest match wins (ties go to the rule listed first in INLINE). A match its
// rule's `ok` check rejects is skipped and that rule searches again one character on.
// appendInline then puts plain text before the match and recurses into its contents
// (except code, which is shown as is).
function nextMatch(text, from) {
  let best = null;
  for (const rule of INLINE) {
    rule.re.lastIndex = from;
    let m;
    while ((m = rule.re.exec(text)) && rule.ok && !rule.ok(text, m)) rule.re.lastIndex = m.index + 1;
    if (m && (!best || m.index < best.m.index)) best = { rule, m };
  }
  return best;
}

// Only absolute https links (same rule as the source links in ui.js).
function safeHref(raw) {
  try {
    const url = new URL(raw);
    return url.protocol === "https:" && url.hostname.includes(".") ? url.href : null;
  } catch { return null; }
}

function appendInline(parent, text) {
  let pos = 0;
  for (let hit; pos < text.length && (hit = nextMatch(text, pos)); ) {
    const { rule, m } = hit;
    if (m.index > pos) parent.append(text.slice(pos, m.index));   // append(string) = text node
    let node = null;
    if (rule.type === "code") {
      node = document.createElement("code");
      node.textContent = m[1];
    } else if (rule.type === "link") {
      const href = safeHref(m[2]);
      if (href) {
        node = document.createElement("a");
        node.href = href;
        node.target = "_blank";
        node.rel = "noopener noreferrer";
        appendInline(node, m[1]);
      }
    } else {
      node = document.createElement(rule.type);
      appendInline(node, m[1]);
    }
    parent.append(node ?? m[0]);   // a rejected link stays as typed
    pos = m.index + m[0].length;
  }
  if (pos < text.length) parent.append(text.slice(pos));
}
