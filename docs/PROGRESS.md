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

**Next**

1. Live API smoke test (needs `ANTHROPIC_API_KEY`). Spike structured-output schema compilation on
   all 4 doc types plus the classifier. Budget: ≤ $5 total, tracked below.
2. First eval run on the core set, then the stress set. Iterate on prompts and confidence from the
   numbers.
3. Record cassettes, then golden tests that replay offline.
4. Eval matrix: image resolution 1568 vs 2048, Haiku vs Sonnet extract, effort levels. Gives $/doc
   vs accuracy.
5. Writeup (README: approach, prompt engineering, confidence design, results), export of the
   conversation log, push to GitHub (ask first).

**Open items noted by reviewers (minor, deliberately deferred)**

- `MEDX_*_EFFORT` cannot be set to `None` from the environment.
- Thinking "off" for an unknown model maps to `disabled`.
- Haiku long-prompt price tier is not modelled.
- Ingest: very-large-pixel images use a lot of memory before being downscaled.
- A blank page that has an invisible text layer is not marked blank.

## API spend log

| Date | Run | Cost |
|---|---|---|
| — | none yet | $0.00 |
