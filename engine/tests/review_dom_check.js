// Run by test_deep_review.py: loads the review page's script against a tiny DOM stub, presses a few buttons and prints
// {"slots": N, "decisions": [...]} as the page would export them.
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
class El {
  set textContent(v) { this._text = v; if (v === '') this.children = []; }   // like the DOM: setting the text replaces the children
  get textContent() { return this._text; }
  constructor(t) { this.t = t; this.children = []; this.className = ''; this._text = ''; this.disabled = false; this.onclick = null; this.href = ''; this.download = ''; }
  append(...c) { this.children.push(...c); }
  click() { if (this.onclick) this.onclick(); }
}
const els = {};
global.document = { createElement: t => new El(t), getElementById: id => els[id] || (els[id] = new El(id)) };
global.Image = function () { return new El('img'); };
global.getComputedStyle = () => ({});
global.URL = { createObjectURL: b => { global.blob = b; return 'blob:x'; } };
global.Blob = function (parts) { this.parts = parts; };
els.curve = new El('canvas'); els.curve.width = 960; els.curve.height = 90;
els.curve.getContext = () => new Proxy({}, { get: () => () => {}, set: () => true });
eval(src);
function all(e, out = []) { out.push(e); e.children.forEach(c => all(c, out)); return out; }
function buttons() { return all(els.slots).filter(x => x.t === 'button'); }
function find(txt, n = 0) { return buttons().filter(b => b.textContent === txt)[n]; }
const slots = els.slots.children.length;
find('Use this one').click();     // replace a slot with an alternate
find('Ban').click();              // ban the first listed clip
find('Ban').click();              // ban the next one that is still allowed
els.export.onclick();
const out = JSON.parse(global.blob.parts[0]);
console.log(JSON.stringify({ slots, decisions: out.decisions }));
