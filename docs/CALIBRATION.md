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

## Phase D measurements (same trip)
- **Cameras, 130 pairs of clips of two cameras started within 60 s:** iPhone 17 vs Osmo Pocket 3 (95 pairs) R/G +0.054 +-0.008,
  B/G -0.080 +-0.009 (log ratios); iPhone 15 vs Osmo (15 pairs) R/G +0.066 +-0.029, B/G within the noise; iPhone 17 vs iPhone 15
  (20 pairs) B/G -0.028 +-0.009. Out of sample (estimated on day 1, applied with FFmpeg's `curves` to day 2 and measured again): mean
  camera difference 0.135 -> 0.102; Osmo vs iPhone 17 R +0.090 -> +0.049, B -0.104 -> -0.052. An earlier version that entered
  "within the noise" pairs as zero offsets and centred on the average camera moved the main camera and made the iPhone 15 worse;
  both were changed.
- **Stabilisation:** the shakiest clip (shake 2.59): jitter per second 2.41 -> 1.67 with `light`, 1.66 with `strong`.
- **Frame rates:** every clip was 25-30 fps; 468 of 486 clips are vertical, so reframing from 16:9 hardly matters for this trip.
- **Sound** was not judged: it needs listening.

## Phase E measurements (same trip, one Windows PC, no NVIDIA encoder)
- **Decoding is the cost.** 10 s of a 4K iPhone clip (H.264) decode in 1.7 s with all CPU threads and 5.3 s with two; the GPU
  decoders available on this PC (d3d11va, dxva2, CUDA) were slower (2.4-2.9 s: the frames are copied back), so software
  decoding stays the default.
- **Review proxies:** skipping the deblocking filter and the B-frames (proxies only) made a proxy 32 % faster on the iPhone
  (H.264) and 21 % on the Osmo (HEVC); with 14 s instead of 24 s per proxy the real 35-shot review page went from 291 s to 157 s,
  and a rebuild after feedback (proxies reused) takes 13 s. Scoring and `story refine` measure sharpness and motion, so they
  always decode every frame properly.
- **Per clip, roughly:** `score` 1 s, `vision run` (YuNet) 0.7 s, a review proxy 2.2 s (shown clips only), `story refine` 4 s
  (chosen clips only, cached). A 1000-clip library: about 17 minutes of scoring, 12 of vision, a few for review and refine.
  `score --min-duration 5` skips the clips the selection never uses (11 % of this trip).

## Phase H measurements (same trip): the picture
- **Colour through the chain.** Flat Rec.709 patches (red-orange, green, blue, skin) through a whole edit (cross-fade, concat, flare,
  fades) and back to RGB: the old 8-bit chain came out 1-2 levels darker on every channel; the precise chain comes out exact (0 levels).
  A first precise version was wrong by up to 16 levels: FFmpeg's `fade` has no 16-bit RGB path and its automatic conversion guessed
  the BT.601 matrix, and `blend` mixes up the green and blue planes of `gbrp16le`. Every conversion now goes through zscale with
  the Rec.709 matrix stated (`test_phase_h.Colour` checks it).
- **Loss against the same shot without any lossy step**, 7 real shots (Osmo 10-bit, iPhone 17, iPhone 15 HLG, night), final at
  `high`: the old 8-bit parts are the baseline; lossless FFV1 parts gave SSIM +0.12 dB, chroma PSNR +0.29 / +0.23 dB, at 9.7 times
  the disk (341 MB of parts for 8 s) and 8 % more time; x264 10-bit CRF 6 gave +0.10 dB, +0.19 / +0.14 dB at 1.9 times the disk;
  CRF 10 +0.08 / +0.11 / +0.06 at 1.15 times. Luma barely moves (+0.03 dB): the loss that remains is the delivery encode itself
  (CRF 16 on noisy footage). Adopted: x264 10-bit CRF 6 for `high`, lossless FFV1 for `max`; the delivery file is 2 % smaller.
- **Banding and noise do not need a filter on this trip.** All 35 storyboard shots at delivery size: CAMBI (libvmaf, under 5 barely
  visible) mean 0.85, only 2 shots reach 5 (5.2 and 5.7, iPhone skies); the HLG shot fell from 2.1 to 1.2 with the precise chain.
  Noise (Immerkaer, edges left out) median 1.1 levels; the highest values are sand texture in daylight action shots, not noise (the
  night shots are low: the phones denoise them). So no automatic deband or denoise was built.
- **A fade of 0 s faded in over 25 frames** (FFmpeg reads `fade d=0` as its default length; `afade d=0` as one second): found by these
  measurements, fixed.
