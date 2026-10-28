"""A page to review a storyboard before anything is rendered: one self-contained HTML file, no network.

Each slot shows the chosen clip and its alternates as a strip of three frames, why it was chosen, and three decisions:
keep (pin it), use an alternate instead, or ban the clip. 'Export decisions' downloads a `decisions.json` that
`story feedback` applies: the storyboard changes and the engine remembers the taste (see vid_profile.py).
The page draws the music energy against the energy of the chosen shots, so a flat or contradicting stretch is visible at once.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import vid_sheets
import vidlib as vl

FRAME = 140


def strip_for(rec: dict, workdir: Path, size: int = FRAME) -> list:
    """Three small JPEGs (base64) across the clip; a missing frame is skipped."""
    out = []
    for k, pos in enumerate(vid_sheets.STRIP_POSITIONS):
        p = workdir / f"{rec['index']}_{k}.jpg"
        if vid_sheets._thumb(rec, p, size, pos, None):                    # noqa: SLF001
            out.append(base64.b64encode(p.read_bytes()).decode("ascii"))
    return out


def build_data(sb: dict, inventory: list, scores: dict, workdir: Path, beats: Optional[dict] = None, log=print,
               workers: int = 6) -> dict:
    by = {r["index"]: r for r in inventory}
    shown = {}
    for r in sb["rows"]:
        for i in [r["clip"]] + list(r.get("alternates") or []):
            if i in by and i not in shown:
                shown[i] = None
    log(f"extracting frames of {len(shown)} clips ...")
    with ThreadPoolExecutor(max(1, workers)) as ex:
        for i, frames in zip(list(shown), ex.map(lambda i: strip_for(by[i], workdir), list(shown))):
            shown[i] = frames
    rows = []
    for r in sb["rows"]:
        entry = {k: r.get(k) for k in ("slot", "t", "len", "chapter", "clip", "name", "device", "role", "people", "topic",
                                       "energy", "target", "music", "start", "why", "score", "lookalike")}
        clips = []
        for i in [r["clip"]] + list(r.get("alternates") or []):
            rec = by.get(i)
            if rec is None:
                continue
            sc = scores.get(i) or {}
            clips.append({"index": i, "name": rec.get("name"), "taken": rec.get("taken"), "duration": round(rec.get("duration") or 0, 1),
                          "quality": sc.get("quality"), "frames": shown[i]})
        entry["clips"] = clips
        rows.append(entry)
    return {"duration": sb.get("duration"), "variant": sb.get("variant"), "rows": rows}


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Storyboard review</title>
<style>
:root{--bg:#fff;--fg:#1c1c1e;--mut:#6b6b70;--line:#d9d9de;--card:#f6f6f8;--acc:#2255cc;--ban:#b3261e;--ok:#1b7a43}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececf0;--mut:#9a9aa2;--line:#34343a;--card:#202024;--acc:#7aa2ff;--ban:#ff8a80;--ok:#6fd49a}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:1000px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mut);margin-bottom:12px}
canvas{width:100%;height:90px;background:var(--card);border:1px solid var(--line);border-radius:8px}
.slot{border:1px solid var(--line);background:var(--card);border-radius:10px;padding:10px;margin:12px 0}
.head{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline;margin-bottom:6px}
.head b{font-size:16px}.tag{color:var(--mut);font-size:13px}
.why{color:var(--mut);font-size:13px;margin:4px 0 8px}
.clips{display:flex;flex-wrap:wrap;gap:10px}
.clip{border:2px solid transparent;border-radius:8px;padding:4px}
.clip.sel{border-color:var(--acc)}.clip.banned{opacity:.35}
.strip{display:flex;gap:2px}.strip img{height:110px;border-radius:3px}
.meta{font-size:12px;color:var(--mut);margin:3px 0}
button{font:inherit;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:6px;padding:3px 9px;cursor:pointer;margin-right:4px}
button:hover{border-color:var(--acc)}.pri{background:var(--acc);color:#fff;border-color:var(--acc)}
.state{font-size:13px;margin-top:6px}.state.keep{color:var(--ok)}.state.ban{color:var(--ban)}
@media (max-width:600px){.strip img{height:80px}}
</style></head><body><main>
<h1>Storyboard review</h1>
<div class="sub" id="sub"></div>
<canvas id="curve" width="960" height="90"></canvas>
<div class="sub">Line = energy of the chosen shots; bars = energy the music asks for.</div>
<div id="slots"></div>
<p><button class="pri" id="export">Export decisions</button> <span class="sub" id="count"></span></p>
<script>
const DATA = __DATA__;
const decisions = [];
const chosen = {};               // slot -> clip index now shown as chosen
function el(t, c, x){const e=document.createElement(t); if(c) e.className=c; if(x!==undefined) e.textContent=x; return e;}
function drop(f){ for(let i=decisions.length-1;i>=0;i--) if(f(decisions[i])) decisions.splice(i,1); }
function record(d){
  // the last word on a slot wins: choosing another clip cancels an earlier keep or replace of that slot
  if(d.action==='replace'||d.action==='keep') drop(x=>x.slot===d.slot&&(x.action==='replace'||x.action==='keep'));
  if(d.action==='ban') drop(x=>x.action==='ban'&&x.clip===d.clip);
  decisions.push(d);
  document.getElementById('count').textContent = decisions.length+' decision(s)'; }
function markBanned(i){ DATA.rows.forEach(r=>r.clips.forEach(c=>{ if(c.index===i) c.banned=true; })); }
const kept = {};                 // slot -> true while its clip is kept
function render(){
  const root = document.getElementById('slots'); root.textContent='';
  DATA.rows.forEach(r=>{
    const cur = chosen[r.slot] ?? r.clip;
    const box = el('div','slot');
    const head = el('div','head'); head.append(el('b',null,'Slot '+r.slot), el('span','tag',(r.t??0).toFixed(1)+'s · '+(r.len??0).toFixed(1)+'s · '+(r.chapter||'')));
    box.append(head);
    box.append(el('div','why', r.why||''));
    if(r.lookalike && r.lookalike.length) box.append(el('div','why','Looks like the shot in slot '+r.lookalike.join(', ')+': keep only one if they show the same thing.'));
    const row = el('div','clips');
    r.clips.forEach(c=>{
      const w = el('div','clip'+(c.index===cur?' sel':'')+(c.banned?' banned':''));
      const s = el('div','strip'); c.frames.forEach(b=>{const i=new Image(); i.src='data:image/jpeg;base64,'+b; s.append(i);}); w.append(s);
      w.append(el('div','meta','clip '+c.index+' · '+(c.name||'')+' · '+c.duration+'s'+(c.quality!=null?' · quality '+Math.round(c.quality):'')));
      if(c.index===cur){
        const k=el('button',null,kept[r.slot]?'Kept':'Keep'); k.disabled=!!kept[r.slot];
        k.onclick=()=>{record({action:'keep',slot:r.slot}); kept[r.slot]=true; render();}; w.append(k);
      } else {
        const u=el('button',null,c.banned?'Banned':'Use this one'); u.disabled=!!c.banned;
        u.onclick=()=>{record({action:'replace',slot:r.slot,with:c.index}); chosen[r.slot]=c.index; kept[r.slot]=true; render();}; w.append(u);
      }
      if(!c.banned){ const b=el('button',null,'Ban'); b.onclick=()=>{record({action:'ban',clip:c.index}); markBanned(c.index); render();}; w.append(b); }
      row.append(w);
    });
    box.append(row);
    root.append(box);
  });
}
function curve(){
  const cv=document.getElementById('curve'), g=cv.getContext('2d'), W=cv.width, H=cv.height, n=DATA.rows.length||1;
  const css=getComputedStyle(document.body);
  g.clearRect(0,0,W,H);
  DATA.rows.forEach((r,k)=>{ const x=k*W/n, w=W/n-2; const h=(r.music||0)*(H-10); g.fillStyle='rgba(120,140,200,.35)'; g.fillRect(x+1,H-h,w,h); });
  g.strokeStyle='#e0651a'; g.lineWidth=2; g.beginPath();
  DATA.rows.forEach((r,k)=>{ const x=(k+.5)*W/n, y=H-5-(r.energy||0)*(H-10); k?g.lineTo(x,y):g.moveTo(x,y); }); g.stroke();
}
document.getElementById('sub').textContent = (DATA.variant||'')+' · '+(DATA.duration||'')+' s · '+DATA.rows.length+' shots';
document.getElementById('export').onclick=()=>{
  const out={decisions:decisions.map(d=>Object.assign({},d))};
  const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{type:'application/json'}));
  a.download='decisions.json'; a.click();
};
render(); curve();
</script></main></body></html>
"""


def write(out: str | os.PathLike, sb: dict, inventory: list, scores: dict, work: str | os.PathLike,
          beats: Optional[dict] = None, notes: Optional[dict] = None, log=print) -> Path:
    with tempfile.TemporaryDirectory(prefix="mhvid_review_") as d:
        data = build_data(sb, inventory, scores, Path(d), beats, log)
    text = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    out = Path(out)
    out.write_text(PAGE.replace("__DATA__", text), encoding="utf-8")
    return out
