# Calibrating the selection on your own footage

The selection has thresholds (when are two clips "the same moment", when is a person "in" a clip). They were first set on
synthetic clips; on a real trip they must be checked against **examples you labelled yourself**. `engine/tools/calibrate_selection.py`
does the arithmetic.

## 1. Collect examples
- **Pairs:** clips of one place and time that might be duplicates. Write down for each pair whether they show the same moment
  (nothing differs: keep only the best) or not. A page with three frames of each clip side by side makes this quick
  (`story review` shows the same strips for a storyboard).
- **People:** a sample of 50-60 clips with a share of clips the detector calls "people" and a share it does not (look at three
  frames of each).

```json
{"pairs":  [{"a": 264, "b": 265, "same": true}, {"a": 204, "b": 206, "same": false}],
 "people": [{"clip": 329, "people": true}, {"clip": 350, "people": false}]}
```

## 2. Run it
```
python engine/tools/calibrate_selection.py --work WORK --labels labels.json
```
It prints the precision and recall of the strict reading of "same moment" (the one that drops a clip) and of the looser
"look-alike" reading (which only flags), a sweep of the strict settings that never call a different pair "same", and the
confusion matrix of `person` and `person_sure`. Precision comes first: wrongly dropping a good clip costs more than keeping a
duplicate. Put the settings you adopt in `chapters.json` under `dedupe` (`hash_bits`, `frames_matching`, `colour`, `brightness`).

## What was measured on the first real trip (Kavir, 2 days, 3 cameras, 462 usable clips)
- **Picture hashes were broken.** All 462 clips had three identical hashes (an FFmpeg `select` filter repeated one frame), so the
  rule "2 of 3 frames must match" was a one-frame test. Fixed in 0.17.0; existing scores are repaired by `score` with a small
  decode (about 4 minutes for 462 clips) instead of a full measurement.
- **Same moment, 20 labelled pairs (14 same, 6 different):** with the old brightness tolerance (30) the strict rule had precision
  0.89 (it joined two cameras of one drive) and recall 0.57. With 10: precision 1.0, recall 0.43. Desert footage is self-similar:
  hash distances of 14-18 bits occurred for both duplicates and different shots of one drive, and a pixel-correlation test did not
  separate them either. So only the strict reading drops anything; the looser reading (hash 16, colour 8, brightness 6) only
  **flags** look-alikes among the chosen shots (a warning and a note on the review page).
- **People, 60 labelled clips (stratified):** `person` precision 0.34 / recall 0.79 on the sample (about 0.40 / 0.82 over the trip),
  `person_sure` (face in 3+ frames) precision 0.63 / recall 0.36. The classic detector is a hint, not a fact (see
  `plugins/vision/README.md`).

## Phase B measurements (same trip)
- **YuNet vs cascades, 60 labelled clips:** cascades `person` 0.34 precision / 0.79 recall, `person_sure` 0.63 / 0.36. YuNet: a face in
  any frame 0.83 / 0.71; a face in 2+ frames and larger than 0.5 % of the frame 1.0 / 0.71. Whole-library run: people in 118 of 413
  clips (the cascades said 226).
- **Shot size, 174 hand-labelled shots:** old rule 21 % agreement, new rule (texture below 25 means wide, else medium) 63 %; a
  decision tree of depth 3 reached only 54 % in cross-validation (it overfits 174 noisy labels), so the single threshold was kept.
  The centre-versus-edge ratio, the old rule's basis, was anti-correlated with `close` on desert footage.
- **Not predictable from the measurements:** the agent's role (establishing / portrait / action / detail) 52 % and mood 49 % in
  5-fold cross-validation (baselines 35 % and 48 %). People from the YuNet certainty: 58 % of the noted people found, 10 % of the
  others flagged.
