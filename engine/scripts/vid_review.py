"""A page to review a storyboard before anything is rendered: an HTML file that works offline, no server, no network.

Each slot shows the chosen clip and its alternates as a strip of three frames and (unless `video=False`) a small proxy video
of the clip that can be played and scrubbed, why the clip was chosen, and the decisions: keep it (pin it), use an alternate
instead, ban the clip, or set its start to the frame on screen. A timeline of the whole edit (each slot as wide as its shot) jumps
to a slot, and the music's energy is drawn against the energy of the chosen shots, so a flat or contradicting stretch is visible at
once. "Play the storyboard" plays the whole edit at its real timing over the music (the proxies, cut at the storyboard's slot
times, no render), with the user's own choices: a replaced clip or a start set with *Start here* plays at once. Decisions are saved in the browser as you go (so closing the page loses nothing) and 'Export decisions' downloads a
`decisions.json` that `story feedback` applies: the storyboard changes and the engine remembers the taste (see vid_profile.py).

The proxies live in a folder next to the page (`<page>_media/`, about 50-100 KB per clip: 240 p, no sound, at most 14 s around the
shot) and are reused when the page is made again (after `story feedback`, only new clips cost time); keep the page and the folder
together. With `video=False` the page is one self-contained file.
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
PROXY_HEIGHT = 240
PROXY_SPAN = 14.0               # seconds of a clip that the proxy holds (a shot is 1-3 s: room to move its start either way)
PROXY_LEAD = 5.0                # seconds before the shot that the proxy starts, so the start can be moved earlier
# decoding is all the cost of a proxy (4K sources): skipping the deblocking filter and the B-frames made it 21-32 % faster on
# real iPhone (H.264) and Osmo (HEVC) clips. A proxy is only for choosing (240 p, about 10-15 distinct frames a second); the
# measurements and the render always decode every frame properly.
PROXY_DECODE = ["-skip_loop_filter", "all", "-skip_frame", "bidir"]


def strip_for(rec: dict, workdir: Path, size: int = FRAME) -> list:
    """Three small JPEGs (base64) across the clip; a missing frame is skipped. The file's size and time are in the name, so a
    strip kept in the media folder is reused when the page is made again (the frames were 90 % of a warm rebuild)."""
    out = []
    for k, pos in enumerate(vid_sheets.STRIP_POSITIONS):
        p = workdir / f"{rec['index']}_{rec.get('size', 0)}_{int(rec.get('mtime') or 0)}_{size}_{k}.jpg"
        if (p.is_file() and p.stat().st_size > 100) or vid_sheets._thumb(rec, p, size, pos, None):      # noqa: SLF001
            out.append(base64.b64encode(p.read_bytes()).decode("ascii"))
    return out


def proxy_window(duration: float, start: float) -> tuple:
    """(offset, span) in seconds of the part of a clip that its proxy holds: from a little before the shot, at most PROXY_SPAN."""
    duration = max(float(duration or 0.0), 0.0)
    span = min(PROXY_SPAN, duration)
    offset = max(0.0, min(float(start or 0.0) - PROXY_LEAD, duration - span))
    return round(offset, 2), round(span, 2)


def make_proxy(rec: dict, dest: Path, offset: float, span: float) -> bool:
    """A small, silent H.264 copy of `span` seconds of the clip from `offset`; False if FFmpeg could not make it."""
    vf = (vid_sheets.TONEMAP_HLG if rec.get("hdr") else "") + f"scale=-2:{PROXY_HEIGHT},fps=15,format=yuv420p"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 200:
        return True                                   # made before with the same window (the name says so)
    r = vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-threads", "2", *PROXY_DECODE, "-ss", f"{offset:.2f}", "-t", f"{span:.2f}",
                "-i", rec["path"], "-an", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                "-movflags", "+faststart", str(dest)], check=False)
    return r.returncode == 0 and dest.is_file() and dest.stat().st_size > 200


def make_music(beats: Optional[dict], seconds: float, media_dir: Path) -> Optional[str]:
    """The first `seconds` of the music as a small AAC file next to the proxies (for playing the storyboard), or None."""
    src = (beats or {}).get("file")
    if not src or not os.path.isfile(src) or not seconds:
        return None
    st = os.stat(src)
    import hashlib
    name = "music_" + hashlib.sha1(json.dumps([os.path.abspath(src), st.st_size, int(st.st_mtime), round(seconds, 2)]).encode()).hexdigest()[:12] + ".m4a"
    dest = media_dir / name
    if dest.is_file() and dest.stat().st_size > 1000:
        return name
    media_dir.mkdir(parents=True, exist_ok=True)
    r = vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-i", src, "-t", f"{seconds + 0.5:.2f}", "-vn", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", str(dest)], check=False)
    return name if r.returncode == 0 and dest.is_file() and dest.stat().st_size > 1000 else None


def build_data(sb: dict, inventory: list, scores: dict, workdir: Path, beats: Optional[dict] = None, log=print,
               workers: int = 6, media_dir: Optional[Path] = None, media_url: str = "") -> dict:
    """The data the page shows. With `media_dir` a proxy video of every clip is made there (`media_url` is the path the page
    uses to reach the folder)."""
    by = {r["index"]: r for r in inventory}
    shown: dict = {}
    start_of: dict = {}
    for r in sb["rows"]:
        for i in [r["clip"]] + list(r.get("alternates") or []):
            if i in by and i not in shown:
                shown[i] = None
                start_of[i] = r["start"] if i == r["clip"] else (scores.get(i) or {}).get("best_start", 0.0)
    log(f"extracting frames of {len(shown)} clips ...")
    with ThreadPoolExecutor(max(1, workers)) as ex:
        for i, frames in zip(list(shown), ex.map(lambda i: strip_for(by[i], workdir), list(shown))):
            shown[i] = frames
    proxies: dict = {}
    if media_dir is not None:
        log(f"making {len(shown)} proxy videos ...")

        def one(i):
            offset, span = proxy_window(by[i].get("duration"), start_of[i])
            name = f"{i}_{int(round(offset * 100))}_{int(round(span * 100))}.mp4"       # the window is in the name: reuse is safe
            ok = make_proxy(by[i], media_dir / name, offset, span)
            return i, ({"file": f"{media_url}{name}", "offset": offset} if ok else None)

        with ThreadPoolExecutor(max(1, min(workers, 4))) as ex:
            proxies = dict(ex.map(one, list(shown)))
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
            c = {"index": i, "name": rec.get("name"), "taken": rec.get("taken"), "duration": round(rec.get("duration") or 0, 1),
                 "quality": sc.get("quality"), "frames": shown[i], "start": round(float(start_of[i]), 2)}
            if proxies.get(i):
                c["video"], c["voffset"] = proxies[i]["file"], proxies[i]["offset"]
            clips.append(c)
        entry["clips"] = clips
        rows.append(entry)
    ex = sb.get("exclusions") or {}
    music = make_music(beats, float(sb.get("duration") or 0.0), media_dir) if media_dir is not None else None
    return {"duration": sb.get("duration"), "variant": sb.get("variant"), "rows": rows,
            "music": f"{media_url}{music}" if music else None,
            "sections": [{"t": round(float(s.get("start", 0.0)), 2), "kind": s.get("kind") or s.get("label")}
                         for s in (beats or {}).get("sections") or []
                         if 0.0 < float(s.get("start", 0.0)) < float(sb.get("duration") or 0.0)],
            "left_out": {"by_reason": ex.get("by_reason") or {}, "clips": ex.get("clips"), "usable": ex.get("usable"),
                         "same_moment": sb.get("same_moment") or []}}


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
#timeline{display:flex;gap:2px;height:34px;margin:0 0 6px}
#timeline div{min-width:3px;border-radius:4px;cursor:pointer;font-size:10px;color:#fff;display:flex;align-items:center;justify-content:center;overflow:hidden}
.slot{border:1px solid var(--line);background:var(--card);border-radius:10px;padding:10px;margin:12px 0}
.head{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline;margin-bottom:6px}
.head b{font-size:16px}.tag{color:var(--mut);font-size:13px}
.why{color:var(--mut);font-size:13px;margin:4px 0 8px}
.clips{display:flex;flex-wrap:wrap;gap:10px}
.clip{border:2px solid transparent;border-radius:8px;padding:4px}
.clip.sel{border-color:var(--acc)}.clip.banned{opacity:.35}
.strip{display:flex;gap:2px}.strip img{height:110px;border-radius:3px}
video{display:block;height:240px;border-radius:4px;background:#000;margin:4px 0}
.meta{font-size:12px;color:var(--mut);margin:3px 0}
button{font:inherit;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:6px;padding:3px 9px;cursor:pointer;margin:2px 4px 2px 0}
button:hover{border-color:var(--acc)}button:disabled{opacity:.5;cursor:default}.pri{background:var(--acc);color:#fff;border-color:var(--acc)}
details{margin:14px 0}summary{cursor:pointer;color:var(--mut)}
@media (max-width:600px){.strip img{height:80px}video{height:180px}}
#player{display:flex;gap:14px;align-items:flex-start;margin:6px 0 14px}
.screen{position:relative;width:180px;height:320px;background:#000;border-radius:8px;overflow:hidden;flex:none}
.screen video{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;margin:0;border-radius:0;visibility:hidden}
#timeline div.now{outline:3px solid var(--fg);outline-offset:-3px}
</style></head><body><main>
<h1>Storyboard review</h1>
<div class="sub" id="sub"></div>
<div id="timeline"></div>
<canvas id="curve" width="960" height="90"></canvas>
<div class="sub">Line = energy of the chosen shots; bars = energy the music asks for; dashed lines = where the music changes (red: a drop). Click a block above to jump to that slot.</div>
<div id="player"><div class="screen"><video id="pa" muted playsinline preload="auto"></video><video id="pb" muted playsinline preload="auto"></video></div>
<div><button class="pri" id="play">Play the storyboard</button><div class="sub" id="now"></div>
<div class="sub" id="playnote">The whole edit at its real timing, with your choices, over the music. A rough cut: no transitions or colour, 240 p.</div></div>
<audio id="music" preload="auto"></audio></div>
<div id="slots"></div>
<details id="leftout"><summary>What was left out, and why</summary><div id="leftouttext" class="sub"></div></details>
<p><button class="pri" id="export">Export decisions</button> <button id="import">Load decisions</button>
<button id="reset">Start over</button> <span class="sub" id="count"></span>
<input type="file" id="file" accept="application/json" style="display:none"></p>
<script>
const DATA = __DATA__;
const decisions = [];
const chosen = {};               // slot -> clip index now shown as chosen
const kept = {};                 // slot -> true while its clip is kept
const started = {};              // slot -> the start (seconds) the user set
function el(t, c, x){const e=document.createElement(t); if(c) e.className=c; if(x!==undefined) e.textContent=x; return e;}
function drop(f){ for(let i=decisions.length-1;i>=0;i--) if(f(decisions[i])) decisions.splice(i,1); }
const KEY = 'mh-review:' + DATA.rows.map(r=>r.clip).join(',');
function save(){ try{ localStorage.setItem(KEY, JSON.stringify({decisions, chosen, kept, started})); }catch(e){} }
function load(){
  try{
    const s = JSON.parse(localStorage.getItem(KEY)||'null'); if(!s) return;
    restore(s);
  }catch(e){}
}
function restore(s){
  decisions.length = 0; (s.decisions||[]).forEach(d=>decisions.push(d));
  [chosen,kept,started].forEach(o=>{ for(const k in o) delete o[k]; });
  Object.assign(chosen, s.chosen||{}); Object.assign(kept, s.kept||{}); Object.assign(started, s.started||{});
  (decisions.filter(d=>d.action==='ban')).forEach(d=>markBanned(d.clip));
  document.getElementById('count').textContent = decisions.length+' decision(s)';
}
function rebuild(list){                      // state from a plain list of decisions (an exported file)
  const s={decisions:[],chosen:{},kept:{},started:{}};
  list.forEach(d=>{ s.decisions.push(d);
    if(d.action==='replace'){ s.chosen[d.slot]=d['with']; s.kept[d.slot]=true; }
    if(d.action==='keep') s.kept[d.slot]=true;
    if(d.action==='start') s.started[d.slot]=d.start; });
  restore(s); save(); render();
}
function record(d){
  // the last word on a slot wins: choosing another clip cancels an earlier keep, replace or start of that slot
  if(d.action==='replace'||d.action==='keep') drop(x=>x.slot===d.slot&&(x.action==='replace'||x.action==='keep'));
  if(d.action==='replace') { drop(x=>x.slot===d.slot&&x.action==='start'); delete started[d.slot]; }
  if(d.action==='start') drop(x=>x.slot===d.slot&&x.action==='start');
  if(d.action==='ban') drop(x=>x.action==='ban'&&x.clip===d.clip);
  decisions.push(d);
  document.getElementById('count').textContent = decisions.length+' decision(s)';
  save();
}
function markBanned(i){ DATA.rows.forEach(r=>r.clips.forEach(c=>{ if(c.index===i) c.banned=true; })); }
function clipCard(r, c, cur){
  const w = el('div','clip'+(c.index===cur?' sel':'')+(c.banned?' banned':''));
  const s = el('div','strip'); c.frames.forEach(b=>{const i=new Image(); i.src='data:image/jpeg;base64,'+b; s.append(i);}); w.append(s);
  let vid = null;
  if(c.video){
    vid = el('video'); vid.src = c.video; vid.preload = 'metadata'; vid.controls = true; vid.muted = true; vid.loop = true;
    if(c.frames.length) vid.poster = 'data:image/jpeg;base64,'+c.frames[0];
    const at = (c.index===cur && started[r.slot]!==undefined) ? started[r.slot] : c.start;
    vid.onloadedmetadata = ()=>{ vid.currentTime = Math.max(0, at - c.voffset); };
    w.append(vid);
  }
  const begin = (c.index===cur && started[r.slot]!==undefined) ? started[r.slot] : c.start;
  w.append(el('div','meta','clip '+c.index+' · '+(c.name||'')+' · '+c.duration+'s'+(c.quality!=null?' · quality '+Math.round(c.quality):'')+' · starts at '+Number(begin).toFixed(1)+'s'));
  if(c.index===cur){
    const k=el('button',null,kept[r.slot]?'Kept':'Keep'); k.disabled=!!kept[r.slot];
    k.onclick=()=>{record({action:'keep',slot:r.slot}); kept[r.slot]=true; save(); render();}; w.append(k);
    if(vid){
      const b=el('button',null,'Start here'); b.title='use the frame on screen as the start of this shot';
      b.onclick=()=>{ const t=Math.round((c.voffset+vid.currentTime)*100)/100; record({action:'start',slot:r.slot,clip:c.index,start:t}); started[r.slot]=t; save(); render(); };
      w.append(b);
    }
  } else {
    const u=el('button',null,c.banned?'Banned':'Use this one'); u.disabled=!!c.banned;
    u.onclick=()=>{record({action:'replace',slot:r.slot,with:c.index}); chosen[r.slot]=c.index; kept[r.slot]=true; save(); render();}; w.append(u);
  }
  if(!c.banned){ const b=el('button',null,'Ban'); b.onclick=()=>{record({action:'ban',clip:c.index}); markBanned(c.index); render();}; w.append(b); }
  return w;
}
function render(){
  const root = document.getElementById('slots'); root.textContent='';
  DATA.rows.forEach(r=>{
    const cur = chosen[r.slot] ?? r.clip;
    const box = el('div','slot'); box.id = 'slot-'+r.slot;
    const head = el('div','head'); head.append(el('b',null,'Slot '+r.slot), el('span','tag',(r.t??0).toFixed(1)+'s · '+(r.len??0).toFixed(1)+'s · '+(r.chapter||'')));
    box.append(head);
    box.append(el('div','why', r.why||''));
    if(r.lookalike && r.lookalike.length) box.append(el('div','why','Looks like the shot in slot '+r.lookalike.join(', ')+': keep only one if they show the same thing.'));
    const row = el('div','clips');
    r.clips.forEach(c=>row.append(clipCard(r, c, cur)));
    box.append(row);
    root.append(box);
  });
}
function timeline(){
  const tl=document.getElementById('timeline'); tl.textContent='';
  const names=[]; DATA.rows.forEach(r=>{ if(!names.includes(r.chapter)) names.push(r.chapter); });
  DATA.rows.forEach(r=>{
    const d=el('div',null,String(r.slot)); d.style.flex=String(Math.max(r.len||0.5,0.2));
    d.style.background='hsl('+(names.indexOf(r.chapter)*47%360)+',45%,42%)'; d.title='slot '+r.slot+' · '+(r.chapter||'')+' · '+(r.len||0).toFixed(1)+'s';
    d.onclick=()=>{ const t=document.getElementById('slot-'+r.slot); if(t&&t.scrollIntoView) t.scrollIntoView({behavior:'smooth',block:'start'}); };
    tl.append(d);
  });
}
function curve(){
  const cv=document.getElementById('curve'), g=cv.getContext('2d'), W=cv.width, H=cv.height, n=DATA.rows.length||1;
  g.clearRect(0,0,W,H);
  DATA.rows.forEach((r,k)=>{ const x=k*W/n, w=W/n-2; const h=(r.music||0)*(H-10); g.fillStyle='rgba(120,140,200,.35)'; g.fillRect(x+1,H-h,w,h); });
  g.strokeStyle='#e0651a'; g.lineWidth=2; g.beginPath();
  DATA.rows.forEach((r,k)=>{ const x=(k+.5)*W/n, y=H-5-(r.energy||0)*(H-10); k?g.lineTo(x,y):g.moveTo(x,y); }); g.stroke();
  (DATA.sections||[]).forEach(s=>{ const k=slotAt(s.t), r=DATA.rows[k]||{}, f=Math.min(1,Math.max(0,(s.t-(r.t||0))/(r.len||1)));
    const x=(k+f)*W/n; g.strokeStyle=(s.kind==='drop')?'#c62828':'#888'; g.lineWidth=1; g.setLineDash&&g.setLineDash([4,3]);
    g.beginPath(); g.moveTo(x,0); g.lineTo(x,H); g.stroke(); g.setLineDash&&g.setLineDash([]); });
}
function leftOut(){
  const lo=DATA.left_out||{}, t=document.getElementById('leftouttext'); t.textContent='';
  const br=lo.by_reason||{};
  const parts=Object.keys(br).map(k=>br[k]+' '+k);
  t.append(el('div',null,(lo.usable!=null?lo.usable+' of '+lo.clips+' clips were usable. ':'')+(parts.length?'Left out: '+parts.join(', ')+'.':'')));
  (lo.same_moment||[]).forEach(g=>t.append(el('div',null,'The same moment filmed more than once: clip '+g.keep+' kept, '+(g.drop||[]).join(', ')+' dropped.')));
  if(!parts.length && !(lo.same_moment||[]).length) document.getElementById('leftout').style.display='none';
}
// ---- play the storyboard: two video elements take turns (one plays, the other is cued on the next slot), the music is the clock;
// a 25 ms timer, not requestAnimationFrame, which stops while the page is not drawn
function shotOf(r){
  const cur = chosen[r.slot] ?? r.clip;
  const c = r.clips.find(x=>x.index===cur) || r.clips[0];
  return {c, at: (started[r.slot]!==undefined ? started[r.slot] : c.start)};
}
function slotAt(t){ for(let k=DATA.rows.length-1;k>0;k--) if(t >= (DATA.rows[k].t||0)) return k; return 0; }
const P = {on:false, k:-1, t0:0, cur:null, next:null};
function clock(){ const a=document.getElementById('music'); return DATA.music ? a.currentTime : (Date.now()-P.t0)/1000; }
function cue(v, k){
  const s = shotOf(DATA.rows[k]); v.dataset = v.dataset || {}; v.dataset.k = String(k);
  if(!s.c.video){ v.removeAttribute && v.removeAttribute('src'); v.src=''; return; }
  const go = ()=>{ v.currentTime = Math.max(0, s.at - s.c.voffset); };
  if(v.src !== s.c.video && (!v.getAttribute || v.getAttribute('src') !== s.c.video)){ v.src = s.c.video; v.onloadedmetadata = go; } else go();
}
function mark(k){
  const tl=document.getElementById('timeline'); Array.from(tl.children||[]).forEach((d,i)=>{ d.className = (i===k?'now':''); });
  const r = DATA.rows[k]; document.getElementById('now').textContent = k<0 ? '' : 'Slot '+r.slot+' · '+(r.chapter||'')+' · clip '+shotOf(r).c.index;
}
function tick(){
  if(!P.on) return;
  const t = clock(), k = slotAt(t);
  if(k !== P.k){
    let v = (P.next && P.next.dataset && +P.next.dataset.k === k) ? P.next : P.cur;
    if(!v.dataset || +v.dataset.k !== k) cue(v, k);
    const other = (v === P.cur) ? P.next : P.cur;
    v.style.visibility='visible'; const pr = v.play && v.play(); if(pr && pr.catch) pr.catch(()=>{});
    if(other){ other.pause && other.pause(); other.style.visibility='hidden'; }
    P.cur = v; P.next = other; P.k = k;
    if(k+1 < DATA.rows.length) cue(P.next, k+1);
    mark(k);
  }
  if(t >= (DATA.duration||0)) { stopPlay(); return; }
  setTimeout(tick, 25);
}
function startPlay(){
  const a=document.getElementById('music'), pa=document.getElementById('pa'), pb=document.getElementById('pb');
  P.on=true; P.k=-1; P.cur=pa; P.next=pb; cue(pa,0); if(DATA.rows.length>1) cue(pb,1);
  P.t0 = Date.now();
  if(DATA.music){ if(a.getAttribute && a.getAttribute('src')!==DATA.music) a.src=DATA.music; a.currentTime=0; const pr=a.play&&a.play(); if(pr&&pr.catch) pr.catch(()=>{}); }
  document.getElementById('play').textContent='Stop';
  setTimeout(tick, 25);
}
function stopPlay(){
  P.on=false; const a=document.getElementById('music'); a.pause && a.pause();
  ['pa','pb'].forEach(id=>{ const v=document.getElementById(id); v.pause && v.pause(); v.style.visibility='hidden'; });
  document.getElementById('play').textContent='Play the storyboard'; mark(-1);
}
document.getElementById('play').onclick=()=>{ P.on ? stopPlay() : startPlay(); };
if(!DATA.rows.some(r=>r.clips.some(c=>c.video))){ document.getElementById('player').style.display='none'; }
else if(!DATA.music){ document.getElementById('playnote').textContent='No music file was found (give --beats with the beats.json of the music): it plays silent at the real timing.'; }
document.getElementById('sub').textContent = (DATA.variant||'')+' · '+(DATA.duration||'')+' s · '+DATA.rows.length+' shots';
document.getElementById('export').onclick=()=>{
  const out={decisions:decisions.map(d=>Object.assign({},d))};
  const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{type:'application/json'}));
  a.download='decisions.json'; a.click();
};
document.getElementById('import').onclick=()=>document.getElementById('file').click();
document.getElementById('file').onchange=(e)=>{
  const f=e.target.files&&e.target.files[0]; if(!f) return;
  const rd=new FileReader(); rd.onload=()=>{ try{ rebuild((JSON.parse(rd.result).decisions)||[]); }catch(err){ alert('That file is not a decisions file.'); } }; rd.readAsText(f);
};
document.getElementById('reset').onclick=()=>{ rebuild([]); DATA.rows.forEach(r=>r.clips.forEach(c=>{c.banned=false;})); render(); };
load(); timeline(); render(); curve(); leftOut();
</script></main></body></html>
"""


def write(out: str | os.PathLike, sb: dict, inventory: list, scores: dict, work: str | os.PathLike,
          beats: Optional[dict] = None, notes: Optional[dict] = None, log=print, video: bool = True, workers: int = 6) -> Path:
    out = Path(out)
    media = out.with_name(out.stem + "_media") if video else None
    with tempfile.TemporaryDirectory(prefix="mhvid_review_") as d:
        strips = media / "strips" if media else Path(d)
        strips.mkdir(parents=True, exist_ok=True)
        data = build_data(sb, inventory, scores, strips, beats, log, workers, media, (media.name + "/") if media else "")
    text = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    out.write_text(PAGE.replace("__DATA__", text), encoding="utf-8")
    return out
