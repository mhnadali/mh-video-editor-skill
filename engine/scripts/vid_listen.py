"""The sound of the footage: measured for every clip (`audio scan`) and checked by ear (`audio review`).

`scan` measures one window per clip (its best window, 3 s) with vid_audio and keeps the numbers in `<work>/audio.json`, keyed by
the file's size and modification time so a second scan only measures new clips. `review_page` writes an offline page with short
sound excerpts of a sample of clips, spread over what the measurement calls them (voices, wind, rumble, other, quiet), and
buttons for what the listener hears and whether it would be good under the music. Its export (`audio_labels.json`) is what
`engine/tools/calibrate_audio.py` checks the thresholds against: the engine cannot hear, so its labels stay provisional until a
person has listened.
"""
from __future__ import annotations

import json
import os
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import vid_audio
import vidlib as vl

WINDOW = 3.0
HEARD = ("voices", "wind", "engine", "music", "quiet", "other")


def scan(work: str | os.PathLike, inventory: list, scores: dict, workers: int = 6, log=print) -> dict:
    """{str(index): {'start', 'm', 'label'}} for every clip with sound, cached in <work>/audio.json."""
    path = Path(work) / "audio.json"
    old = vl.load_json(path) if path.is_file() else {}
    out, todo = {}, []
    for r in inventory:
        if not r.get("has_audio") or r.get("dup_of") is not None or r.get("error"):
            continue
        start = float((scores.get(r["index"]) or {}).get("best_start", 0.0))
        key = [r.get("size"), r.get("mtime"), round(start, 2)]
        prev = old.get(str(r["index"]))
        if prev and prev.get("key") == key and prev.get("path") == r["path"]:
            out[str(r["index"])] = prev
        else:
            todo.append((r, start, key))
    if todo:
        log(f"measuring the sound of {len(todo)} clips ...")

        def one(item):
            r, start, key = item
            m = vid_audio.measure(r["path"], start, min(WINDOW, max(0.5, (r.get("duration") or WINDOW) - start)))
            return str(r["index"]), {"path": r["path"], "key": key, "start": round(start, 2), "m": m, "label": vid_audio.label(m)}

        with ThreadPoolExecutor(max(1, workers)) as ex:
            for k, v in ex.map(one, todo):
                out[k] = v
    vl.save_json(path, out)
    return out


def summary(sounds: dict) -> dict:
    counts: dict = {}
    for v in sounds.values():
        counts[v["label"]] = counts.get(v["label"], 0) + 1
    return counts


def sample(sounds: dict, count: int = 30, seed: int = 7) -> list:
    """Clip indexes to listen to: as even as possible over the measured labels (so every label can be checked), then random."""
    rng = random.Random(seed)
    by: dict = {}
    for k, v in sounds.items():
        by.setdefault(v["label"], []).append(int(k))
    for v in by.values():
        rng.shuffle(v)
    out: list = []
    while len(out) < count and any(by.values()):
        for lab in sorted(by):
            if by[lab] and len(out) < count:
                out.append(by[lab].pop())
    return out


def excerpt(rec: dict, start: float, dest: Path) -> bool:
    if dest.is_file() and dest.stat().st_size > 500:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = vl.run([vl.find_ffmpeg()["ffmpeg"], "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{WINDOW:.2f}", "-i", rec["path"], "-vn",
                "-ac", "1", "-c:a", "aac", "-b:a", "96k", vl.ffp(dest)], check=False)
    return r.returncode == 0 and dest.is_file() and dest.stat().st_size > 500


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Listen to the footage</title>
<style>
:root{--bg:#fff;--fg:#1c1c1e;--mut:#6b6b70;--line:#d9d9de;--card:#f6f6f8;--acc:#2255cc}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececf0;--mut:#9a9aa2;--line:#34343a;--card:#202024;--acc:#7aa2ff}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mut);margin-bottom:12px}
.item{border:1px solid var(--line);background:var(--card);border-radius:10px;padding:10px;margin:10px 0}
audio{width:100%;margin:6px 0}
button{font:inherit;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:6px;padding:3px 9px;cursor:pointer;margin:2px 4px 2px 0}
button.on{background:var(--acc);color:#fff;border-color:var(--acc)}.pri{background:var(--acc);color:#fff;border-color:var(--acc)}
</style></head><body><main>
<h1>Listen to the footage</h1>
<div class="sub">Play each excerpt (3 s), press what you hear and whether it would be good under the music. Then press Export:
<code>calibrate_audio.py</code> checks the engine's guesses against your ears. Your answers are kept in the browser as you go.</div>
<div id="items"></div>
<p><button class="pri" id="export">Export labels</button> <span class="sub" id="count"></span></p>
<script>
const DATA = __DATA__;
const KEY = 'mh-listen:' + DATA.items.map(i=>i.clip).join(',');
let ans = {};
try{ ans = JSON.parse(localStorage.getItem(KEY)||'{}') || {}; }catch(e){ ans = {}; }
function save(){ try{ localStorage.setItem(KEY, JSON.stringify(ans)); }catch(e){} count(); }
function count(){ document.getElementById('count').textContent = Object.values(ans).filter(a=>a.heard).length+' of '+DATA.items.length+' labelled'; }
function el(t,c,x){ const e=document.createElement(t); if(c) e.className=c; if(x!==undefined) e.textContent=x; return e; }
function render(){
  const root=document.getElementById('items'); root.textContent='';
  DATA.items.forEach((it,k)=>{
    const a = ans[it.clip] || {};
    const box=el('div','item'); box.append(el('b',null,(k+1)+'. clip '+it.clip), el('span','sub',' · '+(it.name||'')));
    const au=el('audio'); au.controls=true; au.preload='none'; au.src=it.audio; box.append(au);
    const row=el('div'); DATA.heard.forEach(h=>{ const b=el('button',a.heard===h?'on':'',h); b.onclick=()=>{ ans[it.clip]=Object.assign({},a,{heard:h}); save(); render(); }; row.append(b); });
    box.append(row);
    const g=el('div'); ['good under the music','not good'].forEach((lab,i)=>{ const v=(i===0); const b=el('button',a.good===v?'on':'',lab); b.onclick=()=>{ ans[it.clip]=Object.assign({},ans[it.clip]||{},{good:v}); save(); render(); }; g.append(b); });
    box.append(g); root.append(box);
  });
  count();
}
document.getElementById('export').onclick=()=>{
  const labels = DATA.items.filter(it=>ans[it.clip]&&ans[it.clip].heard).map(it=>({clip:it.clip, start:it.start, heard:ans[it.clip].heard, good:ans[it.clip].good}));
  const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([JSON.stringify({labels},null,2)],{type:'application/json'}));
  a.download='audio_labels.json'; a.click();
};
render();
</script></main></body></html>
"""


def review_page(out: str | os.PathLike, sounds: dict, inventory: list, count: int = 30, workers: int = 4, log=print) -> Path:
    """The listening page and its `<page>_media/` folder of excerpts. The measured labels are not shown (no anchoring)."""
    out = Path(out)
    media = out.with_name(out.stem + "_media")
    by = {r["index"]: r for r in inventory}
    picks = [i for i in sample(sounds, count) if i in by]
    log(f"cutting {len(picks)} sound excerpts ...")

    def one(i):
        s = sounds[str(i)]
        name = f"{i}_{int(round(s['start'] * 100))}.m4a"
        ok = excerpt(by[i], s["start"], media / name)
        return {"clip": i, "name": by[i].get("name"), "start": s["start"], "audio": f"{media.name}/{name}"} if ok else None

    with ThreadPoolExecutor(max(1, workers)) as ex:
        items = [x for x in ex.map(one, picks) if x]
    text = json.dumps({"items": items, "heard": list(HEARD)}, ensure_ascii=False).replace("</", "<\\/")
    out.write_text(PAGE.replace("__DATA__", text), encoding="utf-8")
    return out


# what a heard label means for the measured ones (the engine has no "engine" or "music" word: steady low sound is "rumble")
HEARD_AS = {"voices": "voices", "wind": "wind", "engine": "rumble", "quiet": "quiet", "music": "other", "other": "other"}


def agreement(sounds: dict, labels: list, th: dict | None = None) -> dict:
    """How the measured labels agree with the heard ones: the confusion table, and the skip decision (wind) as precision/recall."""
    table: dict = {}
    tp = fp = fn = 0
    n = hit = 0
    for lb in labels:
        s = sounds.get(str(lb["clip"]))
        if not s or lb.get("heard") not in HEARD_AS:
            continue
        guess = vid_audio.label(s.get("m"), th)
        want = HEARD_AS[lb["heard"]]
        table.setdefault(want, {}).setdefault(guess, 0)
        table[want][guess] += 1
        n += 1
        hit += guess == want
        if guess == "wind" and want == "wind":
            tp += 1
        elif guess == "wind":
            fp += 1
        elif want == "wind":
            fn += 1
    return {"n": n, "agree": round(hit / n, 2) if n else None, "table": table,
            "wind_precision": round(tp / (tp + fp), 2) if tp + fp else None, "wind_recall": round(tp / (tp + fn), 2) if tp + fn else None}
