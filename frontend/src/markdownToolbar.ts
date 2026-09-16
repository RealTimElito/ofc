/** Insert/wrap Markdown around a textarea selection (air-gap friendly, no CDN). */

export type MdAction =
  | "bold"
  | "italic"
  | "h1"
  | "h2"
  | "h3"
  | "ul"
  | "ol"
  | "link"
  | "code";

function wrapInline(
  value: string,
  start: number,
  end: number,
  before: string,
  after: string,
  placeholder: string,
): { value: string; start: number; end: number } {
  const selected = value.slice(start, end);
  const inner = selected || placeholder;
  const next = value.slice(0, start) + before + inner + after + value.slice(end);
  if (selected) {
    return {
      value: next,
      start: start + before.length,
      end: start + before.length + inner.length,
    };
  }
  return {
    value: next,
    start: start + before.length,
    end: start + before.length + inner.length,
  };
}

function prefixLines(
  value: string,
  start: number,
  end: number,
  prefixForIndex: (i: number) => string,
): { value: string; start: number; end: number } {
  const lineStart = value.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
  let lineEnd = value.indexOf("\n", end);
  if (lineEnd < 0) lineEnd = value.length;
  const block = value.slice(lineStart, lineEnd);
  const lines = block.split("\n");
  const rewritten = lines
    .map((line, i) => {
      const trimmed = line.replace(/^\s+/, "");
      if (!trimmed) return line;
      // Avoid double-prefixing
      if (/^#{1,6}\s/.test(trimmed) && prefixForIndex(i).startsWith("#")) {
        return line.replace(/^(\s*)#{1,6}\s+/, `$1${prefixForIndex(i)}`);
      }
      if (/^[-*+]\s/.test(trimmed) && prefixForIndex(i).startsWith("- ")) {
        return line;
      }
      if (/^\d+[.)]\s/.test(trimmed) && /^\d+\. /.test(prefixForIndex(i))) {
        return line;
      }
      const lead = line.match(/^\s*/)?.[0] ?? "";
      return lead + prefixForIndex(i) + trimmed;
    })
    .join("\n");
  const next = value.slice(0, lineStart) + rewritten + value.slice(lineEnd);
  return { value: next, start: lineStart, end: lineStart + rewritten.length };
}

export function applyMarkdownAction(
  value: string,
  start: number,
  end: number,
  action: MdAction,
): { value: string; start: number; end: number } {
  switch (action) {
    case "bold":
      return wrapInline(value, start, end, "**", "**", "bold text");
    case "italic":
      return wrapInline(value, start, end, "*", "*", "italic text");
    case "code":
      return wrapInline(value, start, end, "`", "`", "code");
    case "link": {
      const selected = value.slice(start, end) || "link text";
      const before = "[";
      const after = "](url)";
      const next = value.slice(0, start) + before + selected + after + value.slice(end);
      const urlStart = start + before.length + selected.length + 2;
      return { value: next, start: urlStart, end: urlStart + 3 };
    }
    case "h1":
      return prefixLines(value, start, end, () => "# ");
    case "h2":
      return prefixLines(value, start, end, () => "## ");
    case "h3":
      return prefixLines(value, start, end, () => "### ");
    case "ul":
      return prefixLines(value, start, end, () => "- ");
    case "ol":
      return prefixLines(value, start, end, (i) => `${i + 1}. `);
    default:
      return { value, start, end };
  }
}
