// Run by test_phase_i.py: loads the review page's script against a DOM stub that behaves like a browser where the node stub of
// review_dom_check.js does not (children is an HTMLCollection without forEach, videos have play/pause/src/currentTime), drives
// "Play the storyboard" by setting the music's clock and prints what the player showed.
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
class Coll { constructor(a) { this.a = a; this.length = a.length; a.forEach((x, i) => { this[i] = x; }); } item(i) { return this.a[i]; } }
class El {
  constructor(t) { this.t = t; this._kids = []; this.className = ''; this._text = ''; this.disabled = false; this.onclick = null;
    this.style = {}; this.dataset = {}; this.currentTime = 0; this._src = ''; this.paused = true; this.readyState = 0; }
  get children() { return new Coll(this._kids); }
  set textContent(v) { this._text = v; if (v === '') this._kids = []; }
  get textContent() { return this._text; }
  append(...c) { this._kids.push(...c); }
  click() { if (this.onclick) this.onclick(); }
  get src() { return this._src; }
  set src(v) { this._src = v; this.readyState = 0; }
  getAttribute(k) { return k === 'src' ? (this._src || null) : null; }
  removeAttribute(k) { if (k === 'src') this._src = ''; }
  play() { this.paused = false; return { catch() {} }; }
  pause() { this.paused = true; }
  load() {}
  scrollIntoView() {}
}
const store = {};
global.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
global.Image = function () { return new El('img'); };
global.URL = { createObjectURL: () => 'blob:x' };
global.Blob = function () {};
global.alert = () => {};
global.FileReader = function () {};
global.setTimeout = () => 0;                      // the test moves the clock and calls tick() itself
global.requestAnimationFrame = () => { throw new Error('requestAnimationFrame stops while the page is not drawn'); };
const els = {};
global.document = { createElement: t => new El(t), getElementById: id => els[id] || (els[id] = new El(id)) };
els.curve = new El('canvas'); els.curve.width = 960; els.curve.height = 90;
els.curve.getContext = () => new Proxy({}, { get: () => () => {}, set: () => true });
(0, eval)(src + ';globalThis.__page = {DATA, P, started, chosen};');   // const and let stay inside eval
const { DATA, P, started } = globalThis.__page;
const out = {};
const music = document.getElementById('music');
const loaded = v => { v.readyState = 1; if (v.onloadedmetadata) v.onloadedmetadata(); };
out.slotAt = [0, DATA.rows[1].t - 0.01, DATA.rows[1].t, DATA.rows[2].t + 0.5, 999].map(t => slotAt(t));
els.play.click();
loaded(els.pa); loaded(els.pb);
out.cued = ['pa', 'pb'].map(id => ({ src: els[id].src, t: +els[id].currentTime.toFixed(2), k: els[id].dataset.k }));
const frames = [];
for (const t of [0.1, DATA.rows[1].t + 0.05, DATA.rows[2].t + 0.05]) {
  music.currentTime = t; tick();
  ['pa', 'pb'].forEach(id => { if (els[id].readyState === 0) loaded(els[id]); });
  const v = ['pa', 'pb'].map(id => els[id]).find(x => x.style.visibility === 'visible');
  frames.push({ t, k: P.k, src: v && v.src, now: els.now.textContent, marked: els.timeline.children.length ? els.timeline.children[P.k].className : null });
}
out.frames = frames;
music.currentTime = DATA.duration + 0.1; tick();
out.stopped = { on: P.on, button: els.play.textContent };
// a choice made on the page plays at once: use the alternate of slot 1, then set a start on slot 2
const alt = DATA.rows[1].clips[1];
const slot1 = els.slots.children[1];
const findBtn = (node, label) => { for (const k of node._kids) { if (k.t === 'button' && k._text === label) return k; const f = findBtn(k, label); if (f) return f; } return null; };
findBtn(slot1, 'Use this one').click();
started[DATA.rows[2].slot] = 7.25;
out.choice = { slot1: shotOf(DATA.rows[1]).c.index, alt: alt.index, slot2at: shotOf(DATA.rows[2]).at };
console.log(JSON.stringify(out));
