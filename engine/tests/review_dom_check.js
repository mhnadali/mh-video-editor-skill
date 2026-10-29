// Run by test_deep_review.py / test_phase_c.py: loads the review page's script against a tiny DOM stub, presses a few buttons and
// prints {"slots": N, "videos": N, "decisions": [...], "restored": N} as the page would export them.
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
class El {
  set textContent(v) { this._text = v; if (v === '') this.children = []; }   // like the DOM: setting the text replaces the children
  get textContent() { return this._text; }
  constructor(t) { this.t = t; this.children = []; this.className = ''; this._text = ''; this.disabled = false; this.onclick = null; this.href = ''; this.download = ''; this.style = {}; this.currentTime = 0; }
  append(...c) { c.forEach(x => { x.parent = this; }); this.children.push(...c); }
  click() { if (this.onclick) this.onclick(); }
}
const store = {};
global.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
global.Image = function () { return new El('img'); };
global.getComputedStyle = () => ({});
global.URL = { createObjectURL: b => { global.blob = b; return 'blob:x'; } };
global.Blob = function (parts) { this.parts = parts; };
global.alert = () => {};
global.FileReader = function () {};

function page() {
  const els = {};
  global.document = { createElement: t => new El(t), getElementById: id => els[id] || (els[id] = new El(id)) };
  els.curve = new El('canvas'); els.curve.width = 960; els.curve.height = 90;
  els.curve.getContext = () => new Proxy({}, { get: () => () => {}, set: () => true });
  (0, eval)(src);
  return els;
}
function all(e, out = []) { out.push(e); e.children.forEach(c => all(c, out)); return out; }
const buttons = els => all(els.slots).filter(x => x.t === 'button');
const find = (els, txt, n = 0) => buttons(els).filter(b => b.textContent === txt)[n];

let els = page();
const slots = els.slots.children.length;
const videos = all(els.slots).filter(x => x.t === 'video').length;
find(els, 'Use this one').click();     // replace a slot with an alternate
find(els, 'Ban').click();              // ban the first listed clip
find(els, 'Ban').click();              // ban the next one that is still allowed
const startBtn = find(els, 'Start here');
if (startBtn) {                         // move the start of a chosen clip that has a video
  const vid = startBtn.parent.children.find(x => x.t === 'video');   // the video in the same card as the button
  vid.currentTime = 4;
  startBtn.click();
}
els.export.onclick();
const out = JSON.parse(global.blob.parts[0]);
els = page();                           // "reload" the page: the decisions come back from the browser's storage
const restored = Number((els.count.textContent.match(/^(\d+)/) || [0, 0])[1]);
console.log(JSON.stringify({ slots, videos, decisions: out.decisions, restored }));
