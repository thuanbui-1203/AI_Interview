# Output value contract

This contract is shared by three parties:

- the normalizer, which produces `FieldResult.value`;
- the validation rules, which read those values;
- the sample generator, which writes `truth.json`.

All three must agree exactly on the shapes defined here.

## Value shape for each field kind

| Field kind | Public `value` |
|---|---|
| `TextField` | `str` as printed, with diacritics kept and whitespace collapsed. `null` if not on the document. |
| `DateField` | ISO `"YYYY-MM-DD"`. `null` if not on the document **or** if the printed date is not a real date. In the second case `raw` keeps the printed text, and the `DATE_INVALID` rule fires on `raw != null and value == null`. |
| `MoneyField` | `float`. `null` if not printed. |
| `ChoiceField` | One of the enum strings. `null` if not printed. |
| `ListField[Row]` | A list of row dicts, or `null` if the table is not on the document at all. Use `[]` when the section exists but has no entries (e.g. "Phương pháp điều trị: Điều trị nội khoa"). Row dicts contain only the cells listed below, never `evidence`, `page` or `confidence`. |
| `DiagnosisField` | `{"primary": {"description": str\|null, "icd10_code": str\|null} \| null, "secondary": [{...}, ...]}`. `null` if no diagnosis is printed. |

## Row cells

| Type | Field | Cells |
|---|---|---|
| receipt | items | `description` (str), `quantity` (float\|null), `unit_price` (float\|null), `total` (float\|null), `category` (enum\|null) |
| discharge_summary | procedures_performed | `name` (str), `date` (ISO str if parseable, else the raw text, else null), `code` (str\|null) |
| lab_report | tests | `test_name`, `result` (str, as printed), `unit` (str\|null), `reference_range` (str\|null), `flag` (`"normal"\|"high"\|"low"\|null`), `flag_source` (`"printed"\|"derived"\|null`) |
| prescription | medications | `name`, `dosage`, `frequency`, `duration` (str\|null), `quantity` (float\|null) |

## Field list (exact names; ★ = required by the brief)

- **receipt**:
  - Brief fields: hospital_name★, patient_name★, date★, items★, grand_total★, payment_method★.
  - Extras: currency, invoice_number, subtotal, discount_total, tax_amount,
    insurance_covered_amount, patient_payable, amount_in_words.
  - `payment_method` enum: cash, card, bank_transfer, e_wallet, insurance_direct_billing, other.
  - `item.category` enum: room, consultation, drug, lab, imaging, procedure, surgery, consumable,
    discount, tax, other.
  - `grand_total` is the gross total of charges **after** discount and tax, **before** any
    insurance/BHYT payment or deposit.
  - `discount_total` is a positive number.
  - A discount row is an item with `category: "discount"` and a **negative** `total`.
- **discharge_summary**:
  - Brief fields: hospital_name★, patient_name★, admission_date★, discharge_date★, diagnosis★,
    procedures_performed★, attending_physician★, discharge_instructions★.
  - Extras: department, discharge_status, follow_up_date, sick_leave_from, sick_leave_to.
  - `discharge_status` enum: recovered, improved, unchanged, worse, transferred,
    against_medical_advice, deceased.
- **lab_report**:
  - Brief fields: lab_name★, patient_name★, date★, tests★.
  - Extras: ordering_physician, collection_date.
  - `date` is the report/result date.
- **prescription**:
  - Brief fields: doctor_name★, patient_name★, date★, medications★.
  - Extras: facility_name, diagnosis.

## Validation issue codes

| Code | Severity |
|---|---|
| DATE_INVALID | error |
| DATE_IN_FUTURE | error |
| DATE_AMBIGUOUS_ORDER | warning |
| AMOUNT_NOT_POSITIVE | error |
| TOTAL_MISMATCH | error (> 5 %) |
| TOTAL_MINOR_DIFF | warning |
| LINE_MATH_MISMATCH | warning |
| PAYABLE_CHAIN_MISMATCH | warning |
| AMOUNT_IN_WORDS_MISMATCH | error |
| AMOUNT_IN_WORDS_UNPARSEABLE | info |
| DISCHARGE_BEFORE_ADMISSION | error |
| PROCEDURE_DATE_OUT_OF_STAY | warning |
| LAB_FLAG_INCONSISTENT | warning |
| ICD10_FORMAT_INVALID | warning |
| REQUIRED_FIELD_MISSING | warning |
| CLASSIFICATION_LOW_CONFIDENCE | warning |
| CLASSIFICATION_DISAGREEMENT | warning |
| NOT_MEDICAL_DOCUMENT_SUSPECTED | warning |
| VALUE_RAW_MISMATCH | warning |

## Rules for `fields` paths in issues

- A top-level field is written as its name: `"grand_total"`.
- A row cell is written as `"items[2].total"`, with a 0-based index.
- A nested value uses a dot: `"diagnosis.primary.icd10_code"`.

## `truth.json` (one per sample document)

```json
{
  "doc_id": "core_r01_en_thb",
  "set": "core",                       // "core" | "stress"
  "document_type": "receipt",          // truth label; null for out-of-scope (non-medical) docs
  "language": "en",                    // en | vi | mixed
  "file": "document.pdf",              // or document.png
  "variant": "clean",                  // clean | scan | photo | handwriting
  "pages": 1,
  "fields": { "<every output field of the type, nulls explicit>": "..." },
  "expected_issues": ["TOTAL_MISMATCH"],          // codes that MUST be raised
  "low_confidence_fields": ["payment_method"],    // fields that are blank/smudged/cut: model should give null or low conf
  "notes": "what this document tests"
}
```
