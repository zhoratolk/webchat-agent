// Find the copy button of the WHOLE last assistant message (not a code block's copy button) and tag it
// data-wc-copy="1". Walks up from the message while the container holds only this assistant message; candidates
// must come after the message start and must not sit inside the answer body or a <pre>.
({message, answer, copy}) => {
  document.querySelectorAll('[data-wc-copy]').forEach(e => e.removeAttribute('data-wc-copy'));
  const msgs = [...document.querySelectorAll(message)];
  const last = msgs[msgs.length - 1];
  if (!last) return false;
  let e = last;
  for (let i = 0; i < 7 && e; i++, e = e.parentElement) {
    if (e !== last && e.querySelectorAll(message).length > 1) break;
    const c = [...e.querySelectorAll(copy)].filter(b =>
      !b.closest('pre') && !(answer && b.closest(answer)) &&
      (last.contains(b) || (last.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)));
    if (c.length) { c[c.length - 1].setAttribute('data-wc-copy', '1'); return true; }
  }
  return false;
}
