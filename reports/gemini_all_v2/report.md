# Evaluation report

Documents scored: **15 of 23** (errors: 2, skipped after quota exhaustion: 6, re-scored from saved results: 6). All metrics below cover scored documents only.

| Metric | Value |
|---|---|
| Classification accuracy | 100.0% |
| Field accuracy (all fields) | 98.8% |
| Field accuracy (brief-required fields) | 98.9% |
| Cell accuracy (table cells counted individually) | 99.6% |
| Hallucination rate (value where truth is null) | 2.6% |
| Miss rate (null where truth has a value) | 0.0% |
| Validation recall (seeded defects caught) | 100.0% |
| Error flag recall (wrong values with confidence < 0.85) | 50.0% |
| Degraded-field flag rate (blank/smudged/implicated fields flagged; informational) | 70.0% |
| ECE / Brier (on extracted values) | 0.0431 / 0.0219 |
| Null precision (null answers that were truly absent) | 100.0% of 38 |
| Accuracy at confidence >= 0.9 (coverage) | 99.2% (93.0%) |
| Cost per document | $0.0000 (total $0.0000) |
| Latency p50 / p95 | 41331 ms / 129984 ms |

## Calibration (reliability table)

| Confidence | n | Accuracy | Mean confidence |
|---|---|---|---|
| 0.0-0.2 | 0 | n/a | - |
| 0.2-0.4 | 0 | n/a | - |
| 0.4-0.6 | 4 | 100.0% | 0.5 |
| 0.6-0.8 | 2 | 50.0% | 0.715 |
| 0.8-0.9 | 3 | 100.0% | 0.827 |
| 0.9-1.0 | 119 | 99.2% | 0.97 |

## Per document

| Doc | Type (truth -> pred) | Conf | Fields | Missing expected issues | Wrong fields |
|---|---|---|---|---|---|
| core_d01_en | discharge_summary -> discharge_summary | 1.0 | 13/13 | - | - |
| core_d02_vi_scan | discharge_summary -> discharge_summary | 0.98 | 13/13 | - | - |
| core_d03_en_conservative | discharge_summary -> discharge_summary | 1.0 | 13/13 | - | - |
| core_l01_en | lab_report -> lab_report | 1.0 | 6/6 | - | - |
| core_l02_vi | lab_report -> lab_report | 1.0 | 6/6 | - | - |
| core_p01_en | prescription -> prescription | 1.0 | 6/6 | - | - |
| core_p02_vi | prescription -> prescription | 1.0 | 6/6 | - | - |
| core_r01_en_thb | receipt -> receipt | 1.0 | 14/14 | - | - |
| core_r02_vi_vnd | receipt -> receipt | 0.8 | 14/14 | - | - |
| core_r03_en_sgd_scan | receipt -> receipt | 0.95 | 14/14 | - | - |
| stress_s01_total_mismatch | receipt -> receipt | 1.0 | 13/14 | - | patient_payable: hallucinated pred=2580.0 |
| stress_s02_blank_fields | receipt -> receipt | 0.96 | 14/14 | - | - |
| stress_s03_vn_einvoice_words | receipt -> receipt | 1.0 | 14/14 | - | - |
| stress_s04_vn_discharge_heavy_scan | discharge_summary -> discharge_summary | 1.0 | 13/13 | - | - |
| stress_s05_handwritten_rx | prescription -> None ❌ | None | 0/0 | - | - |
| stress_s06_lab_wrong_flag | lab_report -> lab_report | 1.0 | 5/6 | - | tests: wrong 'Urine Glucose'.result: pred='1 technique' truth='1+' |
| stress_s07_receipt_photo | receipt -> None ❌ | None | 0/0 | - | - |
| stress_s08_restaurant | None -> None ❌ | None | 0/0 | NOT_MEDICAL_DOCUMENT_SUSPECTED | - |
| stress_s09_rx_future_date | prescription -> None ❌ | None | 0/0 | DATE_IN_FUTURE, DATE_AMBIGUOUS_ORDER | - |
| stress_s10_discharge_before_admission | discharge_summary -> None ❌ | None | 0/0 | DISCHARGE_BEFORE_ADMISSION | - |
| stress_s11_prompt_injection | receipt -> None ❌ | None | 0/0 | - | - |
| stress_s12_multipage_discharge | discharge_summary -> None ❌ | None | 0/0 | PROCEDURE_DATE_OUT_OF_STAY | - |
| stress_s13_lab_invalid_date | lab_report -> None ❌ | None | 0/0 | DATE_INVALID, REQUIRED_FIELD_MISSING | - |

## Per field

| Field | n | Accuracy | Hallucinated | Missed | Mean conf |
|---|---|---|---|---|---|
| admission_date | 4 | 100.0% | 0 | 0 | 0.97 |
| amount_in_words | 6 | 100.0% | 0 | 0 | 0.312 |
| attending_physician | 4 | 100.0% | 0 | 0 | 0.948 |
| collection_date | 3 | 100.0% | 0 | 0 | 0.97 |
| currency | 6 | 100.0% | 0 | 0 | 0.97 |
| date | 11 | 100.0% | 0 | 0 | 0.97 |
| department | 4 | 100.0% | 0 | 0 | 0.97 |
| diagnosis | 6 | 100.0% | 0 | 0 | 0.97 |
| discharge_date | 4 | 100.0% | 0 | 0 | 0.97 |
| discharge_instructions | 4 | 100.0% | 0 | 0 | 0.897 |
| discharge_status | 4 | 100.0% | 0 | 0 | 0.535 |
| discount_total | 6 | 100.0% | 0 | 0 | 0.1 |
| doctor_name | 2 | 100.0% | 0 | 0 | 0.97 |
| facility_name | 2 | 100.0% | 0 | 0 | 0.97 |
| follow_up_date | 4 | 100.0% | 0 | 0 | 0.318 |
| grand_total | 6 | 100.0% | 0 | 0 | 0.813 |
| hospital_name | 10 | 100.0% | 0 | 0 | 0.97 |
| insurance_covered_amount | 6 | 100.0% | 0 | 0 | 0.39 |
| invoice_number | 6 | 100.0% | 0 | 0 | 0.97 |
| items | 6 | 100.0% | 0 | 0 | 0.892 |
| lab_name | 3 | 100.0% | 0 | 0 | 0.97 |
| medications | 2 | 100.0% | 0 | 0 | 0.97 |
| ordering_physician | 3 | 100.0% | 0 | 0 | 0.97 |
| patient_name | 15 | 100.0% | 0 | 0 | 0.912 |
| patient_payable | 6 | 83.3% | 1 | 0 | 0.535 |
| payment_method | 6 | 100.0% | 0 | 0 | 0.825 |
| procedures_performed | 4 | 100.0% | 0 | 0 | 0.885 |
| sick_leave_from | 4 | 100.0% | 0 | 0 | 0.318 |
| sick_leave_to | 4 | 100.0% | 0 | 0 | 0.318 |
| subtotal | 6 | 100.0% | 0 | 0 | 0.39 |
| tax_amount | 6 | 100.0% | 0 | 0 | 0.39 |
| tests | 3 | 66.7% | 0 | 0 | 0.897 |

## Errors

- stress_s05_handwritten_rx: provider_unavailable: Gemini daily quota exhausted: You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. 
* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-3.6-flash
Please retry in 23h52m41.630493574s.
- stress_s07_receipt_photo: provider_unavailable: Gemini daily quota exhausted: You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. 
* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-3.6-flash
Please retry in 23h52m11.709682257s.
