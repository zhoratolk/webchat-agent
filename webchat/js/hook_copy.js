// Remember what the page puts on the clipboard (copy buttons give the original markdown) in window.__wcCopied,
// without needing clipboard permissions: wraps clipboard.writeText/write and DataTransfer.setData.
() => {
  window.__wcCopied = null;
  if (window.__wcHooked) return;
  window.__wcHooked = true;
  const save = t => { if (typeof t === 'string' && t.length) window.__wcCopied = t; };
  const cb = navigator.clipboard;
  if (cb) {
    const writeText = cb.writeText ? cb.writeText.bind(cb) : null;
    cb.writeText = async t => { save(t); try { return writeText && await writeText(t); } catch (e) { /* not focused */ } };
    const write = cb.write ? cb.write.bind(cb) : null;
    cb.write = async items => {
      try {
        for (const it of items) if (it.types.includes('text/plain')) save(await (await it.getType('text/plain')).text());
      } catch (e) { /* ignore */ }
      try { return write && await write(items); } catch (e) { /* not focused */ }
    };
  }
  // copy-to-clipboard style: select a hidden node, execCommand('copy')
  const exec = document.execCommand.bind(document);
  document.execCommand = function (cmd, ...rest) {
    if (String(cmd).toLowerCase() === 'copy') {
      const sel = window.getSelection && window.getSelection().toString();
      const ta = document.activeElement;
      save(ta && (ta.tagName === 'TEXTAREA' || ta.tagName === 'INPUT') ? ta.value.slice(ta.selectionStart, ta.selectionEnd) || sel : sel);
    }
    return exec(cmd, ...rest);
  };
  const setData = DataTransfer.prototype.setData;
  DataTransfer.prototype.setData = function (type, val) {
    if (type === 'text/plain' || type === 'text') save(val);
    return setData.call(this, type, val);
  };
}
