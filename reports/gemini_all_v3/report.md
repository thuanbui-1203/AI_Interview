# Evaluation report

Documents scored: **19 of 23** (errors: 1, skipped after quota exhaustion: 3, re-scored from saved results: 6). All metrics below cover scored documents only.

| Metric | Value |
|---|---|
| Classification accuracy | 100.0% |
| Field accuracy (all fields) | 98.4% |
| Field accuracy (brief-required fields) | 97.1% |
| Cell accuracy (table cells counted individually) | 99.4% |
| Hallucination rate (value where truth is null) | 0.0% |
| Miss rate (null where truth has a value) | 0.0% |
| Validation recall (seeded defects caught) | 100.0% |
| Error flag recall (wrong values with confidence < 0.85) | 66.7% |
| Degraded-field flag rate (blank/smudged/implicated fields flagged; informational) | 75.0% |
| ECE / Brier (on extracted values) | 0.0468 / 0.0217 |
| Null precision (null answers that were truly absent) | 100.0% of 45 |
| Accuracy at confidence >= 0.9 (coverage) | 99.3% (92.5%) |
| Cost per document | $0.0000 (total $0.0000) |
| Latency p50 / p95 | 28926 ms / 81027 ms |

## Calibration (reliability table)

| Confidence | n | Accuracy | Mean confidence |
|---|---|---|---|
| 0.0-0.2 | 0 | n/a | - |
| 0.2-0.4 | 1 | 0.0% | 0.35 |
| 0.4-0.6 | 5 | 100.0% | 0.5 |
| 0.6-0.8 | 2 | 50.0% | 0.715 |
| 0.8-0.9 | 3 | 100.0% | 0.827 |
| 0.9-1.0 | 136 | 99.3% | 0.97 |

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
| stress_s01_total_mismatch | receipt -> receipt | 1.0 | 14/14 | - | - |
| stress_s02_blank_fields | receipt -> receipt | 0.96 | 14/14 | - | - |
| stress_s03_vn_einvoice_words | receipt -> receipt | 1.0 | 14/14 | - | - |
| stress_s04_vn_discharge_heavy_scan | discharge_summary -> discharge_summary | 1.0 | 13/13 | - | - |
| stress_s05_handwritten_rx | prescription -> prescription | 1.0 | 5/6 | - | medications: wrong 'Salbutamol inhaler 100 mcg'.frequency: pred='every 6 hours' truth='every 6 hours as needed' |
| stress_s06_lab_wrong_flag | lab_report -> lab_report | 1.0 | 5/6 | - | tests: wrong 'Urine Glucose'.result: pred='1 technique' truth='1+' |
| stress_s07_receipt_photo | receipt -> receipt | 1.0 | 14/14 | - | - |
| stress_s08_restaurant | None -> receipt | 0.3 | 0/0 | - | - |
| stress_s09_rx_future_date | prescription -> prescription | 1.0 | 5/6 | - | medications: wrong 'Cetirizine 10 mg tablet'.frequency: pred='once daily' truth='once daily at night' |
| stress_s10_discharge_before_admission | discharge_summary -> None ❌ | None | 0/0 | DISCHARGE_BEFORE_ADMISSION | - |
| stress_s11_prompt_injection | receipt -> None ❌ | None | 0/0 | - | - |
| stress_s12_multipage_discharge | discharge_summary -> None ❌ | None | 0/0 | PROCEDURE_DATE_OUT_OF_STAY | - |
| stress_s13_lab_invalid_date | lab_report -> None ❌ | None | 0/0 | DATE_INVALID, REQUIRED_FIELD_MISSING | - |

## Per field

| Field | n | Accuracy | Hallucinated | Missed | Mean conf |
|---|---|---|---|---|---|
| admission_date | 4 | 100.0% | 0 | 0 | 0.97 |
| amount_in_words | 7 | 100.0% | 0 | 0 | 0.281 |
| attending_physician | 4 | 100.0% | 0 | 0 | 0.948 |
| collection_date | 3 | 100.0% | 0 | 0 | 0.97 |
| currency | 7 | 100.0% | 0 | 0 | 0.97 |
| date | 14 | 100.0% | 0 | 0 | 0.936 |
| department | 4 | 100.0% | 0 | 0 | 0.97 |
| diagnosis | 8 | 100.0% | 0 | 0 | 0.97 |
| discharge_date | 4 | 100.0% | 0 | 0 | 0.97 |
| discharge_instructions | 4 | 100.0% | 0 | 0 | 0.897 |
| discharge_status | 4 | 100.0% | 0 | 0 | 0.535 |
| discount_total | 7 | 100.0% | 0 | 0 | 0.1 |
| doctor_name | 4 | 100.0% | 0 | 0 | 0.97 |
| facility_name | 4 | 100.0% | 0 | 0 | 0.97 |
| follow_up_date | 4 | 100.0% | 0 | 0 | 0.318 |
| grand_total | 7 | 100.0% | 0 | 0 | 0.836 |
| hospital_name | 11 | 100.0% | 0 | 0 | 0.97 |
| insurance_covered_amount | 7 | 100.0% | 0 | 0 | 0.349 |
| invoice_number | 7 | 100.0% | 0 | 0 | 0.97 |
| items | 7 | 100.0% | 0 | 0 | 0.903 |
| lab_name | 3 | 100.0% | 0 | 0 | 0.97 |
| medications | 4 | 50.0% | 0 | 0 | 0.815 |
| ordering_physician | 3 | 100.0% | 0 | 0 | 0.97 |
| patient_name | 18 | 100.0% | 0 | 0 | 0.922 |
| patient_payable | 7 | 100.0% | 0 | 0 | 0.349 |
| payment_method | 7 | 100.0% | 0 | 0 | 0.846 |
| procedures_performed | 4 | 100.0% | 0 | 0 | 0.885 |
| sick_leave_from | 4 | 100.0% | 0 | 0 | 0.318 |
| sick_leave_to | 4 | 100.0% | 0 | 0 | 0.318 |
| subtotal | 7 | 100.0% | 0 | 0 | 0.349 |
| tax_amount | 7 | 100.0% | 0 | 0 | 0.349 |
| tests | 3 | 66.7% | 0 | 0 | 0.897 |

## Errors

- stress_s10_discharge_before_admission: provider_unavailable: Gemini daily quota exhausted: You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. 
* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-3.6-flash
Please retry in 23h51m30.409073952s.
