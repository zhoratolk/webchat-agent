// Markdown of the last assistant message: ({message, answer, thinking}) => string.
// Fallback for sites without a usable copy button; tables, code, lists, headings, links, emphasis survive.
({message, answer, thinking}) => {
  const msgs = [...document.querySelectorAll(message)];
  const last = msgs[msgs.length - 1];
  if (!last) return '';
  let root = last;
  if (answer) {
    const parts = [...last.querySelectorAll(answer)];
    if (parts.length) {
      root = document.createElement('div');
      parts.filter(p => !parts.some(q => q !== p && q.contains(p))).forEach(p => root.appendChild(p.cloneNode(true)));
    } else {
      root = last.cloneNode(true);
    }
  } else {
    root = last.cloneNode(true);
  }
  if (thinking) root.querySelectorAll(thinking).forEach(e => e.remove());
  root.querySelectorAll('button, svg, style, script, [aria-hidden=true]').forEach(e => e.remove());

  const cell = c => conv(c).replace(/\n+/g, ' ').replace(/\|/g, '\\|').trim();
  const conv = node => {
    if (node.nodeType === 3) return node.textContent;
    if (node.nodeType !== 1) return '';
    const tag = node.tagName.toLowerCase();
    const inner = () => [...node.childNodes].map(conv).join('');
    switch (tag) {
      case 'h1': case 'h2': case 'h3': case 'h4': case 'h5': case 'h6':
        return '\n\n' + '#'.repeat(+tag[1]) + ' ' + inner().trim() + '\n\n';
      case 'p': return '\n\n' + inner().trim() + '\n\n';
      case 'br': return '\n';
      case 'hr': return '\n\n---\n\n';
      case 'strong': case 'b': return '**' + inner() + '**';
      case 'em': case 'i': return '*' + inner() + '*';
      case 'del': case 's': return '~~' + inner() + '~~';
      case 'a': return '[' + inner() + '](' + (node.getAttribute('href') || '') + ')';
      case 'code': return node.closest('pre') ? node.textContent : '`' + node.textContent + '`';
      case 'pre': {
        const code = node.querySelector('code');
        const lang = ((code && code.className) || '').match(/language-([\w+-]+)/);
        return '\n\n```' + (lang ? lang[1] : '') + '\n' + (code || node).textContent.replace(/\n$/, '') + '\n```\n\n';
      }
      case 'ul': case 'ol': {
        const items = [...node.children].filter(c => c.tagName === 'LI');
        return '\n\n' + items.map((li, i) => (tag === 'ol' ? (i + 1) + '. ' : '- ') +
          conv(li).trim().replace(/\n/g, '\n   ')).join('\n') + '\n\n';
      }
      case 'li': return inner();
      case 'blockquote': return '\n\n' + inner().trim().split('\n').map(l => '> ' + l).join('\n') + '\n\n';
      case 'table': {
        const rows = [...node.querySelectorAll('tr')].map(tr => [...tr.children].map(cell));
        if (!rows.length) return '';
        const n = Math.max(...rows.map(r => r.length));
        const line = r => '| ' + [...r, ...Array(n - r.length).fill('')].join(' | ') + ' |';
        return '\n\n' + [line(rows[0]), line(Array(n).fill('---')), ...rows.slice(1).map(line)].join('\n') + '\n\n';
      }
      case 'img': return node.getAttribute('alt') ? '![' + node.getAttribute('alt') + '](' + node.src + ')' : '';
      default: return inner();
    }
  };
  return conv(root).replace(/\n{3,}/g, '\n\n').trim();
}
