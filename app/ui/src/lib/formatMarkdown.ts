/**
 * Lightweight safe inline markdown → HTML formatter.
 *
 * Supports:
 *  - **bold** text
 *  - *italic* text
 *  - Newlines → <br>
 *  - Bullet lists (lines starting with *, -, or numbered 1.)
 *  - ✅/❌ emoji pass-through
 *
 * Does NOT allow arbitrary HTML — input is escaped first.
 */

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function formatMarkdown(raw: string): string {
  if (!raw) return '';

  let text = escapeHtml(raw);

  // Bold: **text** or __text__
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/__(.+?)__/g, '<strong>$1</strong>');

  // Italic: *text* (but not inside <strong>)
  text = text.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');

  // Bullet points: lines starting with * or -
  text = text.replace(/^[\s]*[*\-]\s+(.+)$/gm, '<li>$1</li>');

  // Numbered bullets: lines starting with 1. 2. etc.
  text = text.replace(/^[\s]*\d+\.\s+(.+)$/gm, '<li>$1</li>');

  // Wrap consecutive <li> in <ul>
  text = text.replace(/((?:<li>.+?<\/li>\n?)+)/g, '<ul>$1</ul>');

  // Newlines → <br> (but not right after block elements)
  text = text.replace(/\n(?!<)/g, '<br>');

  // Clean up double <br>
  text = text.replace(/(<br>){3,}/g, '<br><br>');

  return text;
}
