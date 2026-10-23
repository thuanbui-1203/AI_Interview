# Progress log

## Checkpoint 1 (2026-10-22): offline pipeline complete, no live API run yet

**Done**

- Phase 0–1: decisions confirmed (DESIGN.md §10) and a design panel of 3 lenses was synthesized into
  DESIGN.md.
- Phase 2: sample generator in `tools/samplegen/`.
  - 23 documents: 10 core (3 receipts, 3 discharge summaries, 2 lab reports, 2 prescriptions,
    EN+VN) and 13 stress cases.
  - Truth is built from the same data that renders each document.
  - `samples/contact_sheet.png` shows them all.
- Phase 3: core pipeline.
  - Built: fields/schema, registry and schema lint, 4 doc types, ingest, providers (Anthropic, Fake,
    record/replay cassettes), normalize, confidence, validation rules and parsers, CLI, FastAPI
    web UI, eval harness.
  - Each module got an independent adversarial review; 16 bugs were found and fixed, each with a
    regression test.
- Defaults changed after review:
  - Image resolution raised to 2048 px / 3 MP (~175 dpi A4). Sonnet/Opus 5.x support up to
    2576 px.
  - Pages above that are downscaled.
  - `REVIEW_THRESHOLD` (0.85) is shared by the UI and the eval.

**Since checkpoint 1**

- `GeminiProvider` added (google-genai 2.32):
  - JSON-schema structured output with `$defs` inlined.
  - Thinking mapped to a level (Gemini 3) or a budget (Gemini 2.5).
  - Free-tier handling: client-side RPM spacing, 429 retries that honour `retryDelay`, and no
    retry when the daily quota is exhausted.
  - 25 tests.
- `GEMINI_API_KEY` can be set in a gitignored `.env`, read as a `SecretStr`.
- `medextract models` lists the models a key can call.

**Live runs with Gemini (2026-10-22)**

Model probe:

- `gemini-2.5-flash(-lite)` return 404 "no longer available to new users". Defaults are now
  `gemini-3.5-flash-lite` (classify) and `gemini-3.5-flash` (extract).
- The free tier allows **20 requests/day/model**, resetting at 14:00 ICT.

Eval v1, core 10:

- Classification 100%, fields 97.1%, miss rate 0%.
- Extract p50 latency is about 35 s, with about 5k output tokens (mostly thinking).

What the live runs exposed, all fixed with regression tests:

1. **Sign on deduction lines.** "Less: AIA direct billing -10,000.00" was stored as -10000, which
   raised false AMOUNT_NOT_POSITIVE and PAYABLE_CHAIN errors. The model's reading (10000) was right.
   Fix: `DocTypeSpec.magnitude_fields`.
2. **Ambiguous field definitions** (mine, not the model's):
   - `dosage`: strength vs amount per administration.
   - `discount_total`: a summary line vs a discount item row.

   Schema descriptions were tightened.
3. **Evidence check on normalized values.** `cash` vs "Tiền mặt", `VND` vs "đồng", and long
   instructions vs an 80-character quote were penalized wrongly. Fix: check the printed wording and
   `inferred_fields`, and accept a quote that is a slice of a long value.
4. **Evaluation flaws.**
   - Calibration included correct nulls (low confidence by design). Now measured on extracted
     values, with null precision reported separately.
   - The subjective `category` cell was scored. It no longer is.
   - Quota failures were scored as misclassifications. Metrics now cover scored documents only.
   - Fail-fast on daily quota and `--resume` were added.
5. **Lab result misread** "1+" → "1 technique" (stress_s06). Row cells are now checked against the
   row's evidence quote, and row evidence is kept in the verbose output.
6. **Number tokenizer.** It glued "1 10 10" into 11010. Space-grouped thousands are now a separate
   pattern.

Eval v2 (`reports/gemini_all_v2`, 15 of 23 scored; the quota ran out on 2026-10-22):

- 10/10 core documents had every field correct.
- Classification 100%, required fields 98.9%, miss rate 0%.
- Hallucination 2.6%: one case. s01 printed a single "TOTAL DUE" line and the model also copied it
  into `patient_payable`. This was an ambiguous definition, now tightened.
- Validation recall 100%, ECE 0.043.
- Accuracy at confidence ≥ 0.9 is 99.2% at 93% coverage.
- Null precision 100% (38 nulls, all truly absent).

Free-tier quota lesson:

- Retries after 429 or 503 still count against the 20/day quota. 9 documents used 19 extract
  attempts.
- `gemini_rpm` was lowered to 4.
- `CallRecord.retry_reasons` now records why each retry happened.
- Changing a document type's prompt or schema invalidates its cassettes. This is by design, so a
  stale replay can never hide a regression. Receipt cassettes need re-recording after the
  `patient_payable` change.

**Next**

1. 2026-10-23, after the 14:00 ICT quota reset: run the 8 remaining stress documents (s05, s07–s13)
   and re-record the receipts whose prompt changed, with `--resume` and record mode.
2. Golden tests that replay the recorded cassettes offline.
3. README writeup: approach, prompt engineering, confidence design, results and limitations.
4. Export the conversation log into the repo, then push to GitHub (ask first).

## API spend log

| Date | Run | Cost |
|---|---|---|
| — | none yet | $0.00 |
