# medextract — Design (v1, pre-implementation)

Medical document extraction pipeline for Papaya AI Challenge 08: a medical document (PDF or image)
goes in, structured JSON comes out, with a classification, per-field values, per-field confidence
and validation errors.

This document was written before any code. The decision log is in §10.

## 1. Goals and non-goals

**Goals**

- Classify a document into one of `receipt`, `discharge_summary`, `lab_report` or `prescription`,
  with a confidence score.
- Extract the fields the brief requires for each type, using the exact brief names.
- No hallucination. A field that is not visible comes back `null` with low confidence.
- Confidence must be meaningful, and we prove it: the eval reports calibration, not just a claim.
- Validation: dates are valid, amounts are positive, item totals sum to the grand total (flag a
  mismatch above 5%), plus domain rules.
- Reusable. A new document type is one new module, and the core does not change.
- Measured. The eval harness reports accuracy, hallucination rate, calibration, $/doc and latency.

**Non-goals for this take-home**

- Local OCR.
- Multi-page chunking and merging beyond a page cap.
- A rule DSL.
- A second LLM provider (only a protocol and a fake implementation).
- Auth, a database, or a job queue.

## 2. Pipeline

```
bytes ──► Ingest ──► Classify ──► Extract ──► Normalize ──► Ground ──► Validate ──► Score ──► Assemble
         (pages,     (Haiku,      (Sonnet,     (raw→value  (evidence  (rules per  (confidence (public JSON
          text        small img)   per-type     in code)    vs text    doc type)   adjusters)  + verbose)
          layer)                   schema)                  layer)
```

- **Ingest.** File type is detected from magic bytes, not the extension.
  - PDF: rasterized at about 150–200 DPI with PyMuPDF, and the text layer is kept for grounding.
    Encrypted, corrupt and empty PDFs raise typed errors.
  - Images: EXIF auto-rotate, convert to RGB, and downscale so the long edge is at most 1568 px.
    No binarization.
  - A page cap (default 6) raises `TooManyPages` when exceeded.
- **Classify** is a cheap call. The output is `{document_type, scores per class, language, cues,
  is_medical_document}`. The class set is exactly the brief's 4 types (decision D1). An
  out-of-scope input still gets the closest type, but with capped low confidence and a
  `NOT_MEDICAL_DOCUMENT_SUSPECTED` warning.
- **Extract** is a call against the schema for that document type. The schema's first field is
  `observed_document_type`. If it disagrees with the classifier, the document confidence is
  capped and a warning is added.
- **Normalize.** The model returns `raw`, the text as printed, plus its interpretation `value`.
  Code re-parses `raw` with locale rules:
  - VN uses dd/mm/yyyy dates and `.` as the thousands separator.
  - VND amounts are integers.
  - Thai Buddhist Era years are year − 543 (parser support only).

  A disagreement between the model's `value` and the code's parse lowers confidence. This catches
  misreads such as `15.750.000 → 15.75`.
- **Ground.** The evidence quote is fuzzy-matched against the PDF text layer, only when a text
  layer exists. Scanned and PNG inputs have none, and we state that honestly.
- **Validate.** Rules are plain composable objects, parameterized by field paths, and each one
  emits `ValidationIssue{code, severity, fields, message}`.
- **Score.** A deterministic adjuster chain turns the model's confidence bucket into a final
  number. Every step is recorded in a `confidence_trace`.
- **Assemble.** `PipelineResult.to_public()` produces exactly the brief's shape. A verbose mode
  adds raw, evidence, status, trace and usage/cost.

**Call strategy.** `two_stage` is the default: Haiku classifies, then Sonnet extracts. It sits
behind a `CallStrategy` interface, and `single_call` (one call over a union schema) is
switchable. The choice is made from eval numbers covering accuracy, $/doc and latency, not from
argument alone. `cascade` (Haiku extracts, and only weak fields escalate to Sonnet) is an
interview-day lever and is not built now.

## 3. LLM output schema vs public output

The LLM output schema is rich and shaped to help the model. Field order is deliberate, because
structured outputs generate properties in schema order: quote the source first, then decide, then
rate.

```python
class Status(StrEnum): present, partial, illegible, ambiguous, not_found
class Bucket(StrEnum):  certain, high, medium, low, none      # enum, not float: floats cluster at 0.9

class TextField(BaseModel):            # extra="forbid" → additionalProperties:false
    evidence: str | None               # shortest verbatim snippet incl. label, ≤ ~80 chars
    page: int | None
    status: Status
    value: str | None
    confidence: Bucket
# DateField / MoneyField add `raw` (as printed) and, for money, `currency`.
# List fields (items/tests/medications): one row model per line with typed cells +
# row-level evidence/page/confidence (per-cell wrappers would ~3x output tokens).
```

Every field is required but nullable. The model must commit to `null` explicitly instead of
dropping the key.

Constraints such as ranges, lengths and positivity are checked in code. The structured-output
JSON schema cannot express min/max or length limits.

Public output, as the brief requires. Optional extra keys never break the contract:

```json
{
  "document_type": "receipt",
  "confidence": 0.95,
  "fields": { "grand_total": { "value": 15750.0, "confidence": 0.97 } },
  "validation_errors": [ { "code": "TOTAL_MISMATCH", "severity": "error",
                           "fields": ["grand_total", "items"], "message": "..." } ]
}
```

List fields project to `{value: [rows], confidence: min(row confidences after adjustment)}`.

## 4. Confidence

| Bucket | Base | Rubric given to the model |
|---|---|---|
| certain | 0.97 | Clearly printed, read character for character, one candidate, label present |
| high | 0.88 | Clear, but light normalization was needed or the label is implicit |
| medium | 0.65 | Small or blurry text, handwriting, stamp overlap, inferred from layout, or two candidates |
| low | 0.35 | Partly guessed or heavily degraded |
| none | 0.10 | Value is null |

The adjusters run in code, in order. Each one records before/after and a reason.

1. Null cap: when `value is None`, confidence is at most 0.15.
2. Ungrounded: a non-null value with empty evidence is capped at 0.5.
3. Evidence not found in the PDF text layer: multiply by 0.6 (text-layer docs only).
4. Raw vs value: if the locale re-parse disagrees with the model's value, cap at 0.5 and add a
   warning.
5. Heavy normalization (ambiguous dd/mm, Buddhist Era year, 2-digit year): cap at 0.75.
6. Validation: a field named in an `error` is capped at 0.5; in a `warning`, at 0.75.
7. Status: `partial` caps at 0.7, `ambiguous` at 0.6.
8. Document confidence is the classifier's top score, scaled down when the margin is small, and
   capped at 0.5 when the extractor's `observed_document_type` disagrees.

**Proof.** The eval reports:

- accuracy per confidence bucket (reliability table, with counts),
- ECE and Brier score,
- accuracy at threshold, i.e. the share of fields that can be auto-accepted at ≥ 0.9.

Clean synthetic documents would score about 100% and leave nothing to calibrate, so degraded and
edge-case variants are part of the eval set.

## 5. Anti-hallucination

- "Null beats a guess." Absent fields are scored as correct when the prediction is null.
- **Transcribe, never compute.** Numbers are copied as printed, even when wrong. The model never
  fixes sums, because a fixed sum would blind the validator. Sums, flags and date parsing happen
  in code.
- No defaulting from typical values (for example, inventing "Cash"). Names keep their diacritics
  and are never transliterated.
- Disambiguation rules for each document type:
  - `hospital_name` is the issuing facility on the letterhead.
  - `patient_name` is never the doctor's or guarantor's name.
  - The receipt `date` is the issue date.
- **The document is untrusted data.** Text in the image is content, never instructions. An eval
  document contains an injected "SYSTEM: …" line to test this.
- API failures:
  - `max_tokens`: retry once with double the limit.
  - Parse error: retry once.
  - Refusal: return a structured `extraction_failed` result.
  - 429 and 5xx: SDK retries.

## 6. Document types (field sets)

The brief's required fields use exact names. Extras are optional and nullable, and never count
as missing. The extras below are the core subset (decision D3).

- **receipt**: hospital_name, patient_name, date, items[{description, quantity, unit_price,
  total}], grand_total, payment_method.
  - Extras: currency, invoice_number, subtotal, discount_total, tax_amount,
    insurance_covered_amount (VN BHYT fund share), patient_payable, amount_in_words, and
    item.category.
  - `grand_total` is the gross billed total after discount and VAT, before any
    insurance deduction. `patient_payable` is separate (decision D2).
- **discharge_summary**: hospital_name, patient_name, admission_date, discharge_date,
  diagnosis{primary, secondary[]} with each entry as {description, icd10_code|null},
  procedures_performed[], attending_physician, discharge_instructions.
  - Extras: department, discharge_status, follow_up_date, sick_leave_from/to.
  - ICD codes are never inferred.
- **lab_report**: lab_name, patient_name, date, tests[{test_name, result (string), unit,
  reference_range, flag}].
  - A printed flag is transcribed. A flag that is not printed is derived in code from the result
    and range, marked `flag_source: derived`.
- **prescription**: doctor_name, patient_name, date, medications[{name, dosage, frequency,
  duration, quantity}].
  - Extras: facility_name, diagnosis.

## 7. Validation rules

| Code | Rule | Sev |
|---|---|---|
| DATE_INVALID | Not a real calendar date, or the year is outside 1900 to today+1 | error |
| DATE_IN_FUTURE | Any document date later than the processing date + 1 day | error |
| DATE_AMBIGUOUS_ORDER | Both day and month ≤ 12 and there is no locale cue | warning |
| AMOUNT_NOT_POSITIVE | grand_total, unit_price, quantity or item total ≤ 0. Discount lines are exempt. | error |
| TOTAL_MISMATCH | \|Σitems − discount + tax − grand_total\| / grand_total > 5% | error |
| LINE_MATH_MISMATCH | \|qty × unit_price − total\| > max(1 unit, 1%) | warning |
| PAYABLE_CHAIN_MISMATCH | grand_total − insurance_covered ≠ patient_payable, when both are printed | error |
| AMOUNT_IN_WORDS_MISMATCH | Amount in words ≠ numeric total (a tampering signal) | error |
| DISCHARGE_BEFORE_ADMISSION | discharge < admission | error |
| LAB_FLAG_INCONSISTENT | Printed flag contradicts the result vs the reference range | warning |
| ICD10_FORMAT_INVALID | Code does not match `^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$` | warning |
| REQUIRED_FIELD_MISSING | A required field from the brief is null (correct behaviour, but surfaced) | warning |
| CLASSIFICATION_LOW_CONFIDENCE / _DISAGREEMENT | Document confidence < 0.6, or classifier and extractor disagree | warning |
| NOT_MEDICAL_DOCUMENT_SUSPECTED | The classifier says the input is not a medical document; it is forced into the closest type at low confidence | warning |

## 8. Architecture (extension points)

```
src/medextract/
  config.py            Settings (pydantic-settings, MEDX_ env), pricing table
  errors.py            InputError / ProviderError / ConfigError taxonomy → HTTP status + exit code
  core/   models.py (FieldResult, ValidationIssue, PipelineResult, to_public), document.py,
          pipeline.py (Extractor facade), strategies.py, registry.py (auto-discovery), paths.py
  ingest/ pdf.py, image.py                 (Ingestor protocol)
  providers/ base.py (LLMProvider protocol), anthropic.py, fake.py, cassette.py (record/replay)
  prompts/   versioned prompt files; the prompt hash goes into result meta and cassette keys
  doctypes/  _common.py, receipt.py, discharge_summary.py, lab_report.py, prescription.py
  validation/ rules.py (generic, path-parameterized), parsing.py (dates, money, locales)
  confidence/ adjusters.py
  interfaces/ cli.py (typer: extract, eval), web/ (FastAPI + one Jinja page)
  integrations/ agent_tool.py  (Challenge 11 verifyDocument adapter; thin)
samples/   generator (Jinja HTML → Chromium PDF → OpenCV "scanify" PNG) + truth.json
eval/      harness, matchers, floors.yaml
tests/     unit / contract / golden (cassette replay) / api / live (marked, opt-in)
```

- `DocTypeSpec(name, description, schema, prompt_hints, rules, key_fields, version)` is a frozen
  dataclass in a module-level `SPEC`. The registry auto-discovers it and lints the schema against
  structured-output limits at import time.
- `LLMProvider.structured(req) → StructuredResponse(parsed, usage, stop_reason, latency,
  request_id)`. Cost is computed outside the provider from the pricing config.
- Cassettes let golden tests replay recorded API responses offline. The key is a hash of model,
  prompt version, schema and image. Changing a prompt invalidates its cassettes, so a stale
  replay cannot hide a regression.
- An empty `Inspector` slot is reserved for interview-day tamper and AI-generated checks.
  `RuleContext.claim` is reserved for Challenge 11 cross-document checks.

## 9. Evaluation

The sample set is generated from structured truth data, so ground truth comes for free.

- **Core 10**, as the brief specifies: 3 receipts, 3 discharge summaries, 2 lab reports and
  2 prescriptions. Mostly English, plus VN documents: a Giấy ra viện and a hóa đơn viện phí in
  VND.
- **Stress set**, about 10–12 documents. Each one carries its expected validation codes:
  - a total mismatch
  - blank labelled fields (the hallucination trap)
  - a VN invoice whose amount in words disagrees with the number
  - a scanned-looking Giấy ra viện with a stamp and skew
  - a multi-page discharge summary
  - a handwriting-style prescription
  - a lab report with a wrong printed flag
  - a phone photo of a receipt
  - a non-medical document
  - a future-dated or ambiguous-date prescription
  - discharge before admission
  - a prompt-injection line

**Metrics:**

- classification accuracy and confusion matrix
- per-field accuracy with typed matchers (exact, numeric tolerance, fuzzy name, token F1 for free
  text)
- hallucination rate
- null recall
- validation true and false positives
- calibration (bucket table, ECE)
- $/doc and latency p50/p95

The core 10 and the stress set are reported separately.

## 10. Decision log

| # | Decision | Chosen | Why |
|---|---|---|---|
| D1 | Add an `unknown` class? | **No.** Keep the brief's 4 types; flag out-of-scope input with low confidence and a warning. | Stay within the brief's contract. The warning still keeps junk documents from being auto-accepted. |
| D2 | Meaning of `grand_total` | **Gross billed total** after discount and VAT, before any insurance or deposit deduction. Extra fields: subtotal, discount_total, tax_amount, insurance_covered_amount, patient_payable. | Without these, VN BHYT invoices trip the 5% sum check falsely. |
| D3 | Fields beyond the brief | **Core subset** (§6) | Only the fields that validation or claims assessment actually uses. |
| D4 | API budget for development and eval | **≤ $5 total** | All real runs are tracked in a spend log. |
| D5 | LLM | Claude: Haiku 5.5 classifies, Sonnet 5.5 extracts; both configurable | Cheap classification, strong extraction. Model choice is measured in the eval. |
| D6 | SDK version | `anthropic>=1.17,<2` | 2.0.0 was released 2026-10-21 (one day before this work) with an undocumented API-version break. |
| D7 | Default input mode | Rasterize every page | One path for PDFs and scans, matching real scanned claims. The PDF document block is a switch. |
| D8 | Lab flag not printed | Derived in code, marked `flag_source: derived` | The model transcribes; code computes. |
