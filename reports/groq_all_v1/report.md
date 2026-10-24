# Evaluation report

Documents scored: **16 of 23** (errors: 2, skipped after quota exhaustion: 5, re-scored from saved results: 0). All metrics below cover scored documents only.

| Metric | Value |
|---|---|
| Classification accuracy | 100.0% |
| Field accuracy (all fields) | 87.2% |
| Field accuracy (brief-required fields) | 87.0% |
| Cell accuracy (table cells counted individually) | 95.2% |
| Hallucination rate (value where truth is null) | 15.4% |
| Miss rate (null where truth has a value) | 2.9% |
| Validation recall (seeded defects caught) | 100.0% |
| Error flag recall (wrong values with confidence < 0.85) | 11.8% |
| Degraded-field flag rate (blank/smudged/implicated fields flagged; informational) | 81.8% |
| ECE / Brier (on extracted values) | 0.1056 / 0.1225 |
| Null precision (null answers that were truly absent) | 86.8% of 38 |
| Accuracy at confidence >= 0.9 (coverage) | 87.9% (86.6%) |
| Cost per document | $0.0000 (total $0.0000) |
| Latency p50 / p95 | 169417 ms / 201652 ms |

## Calibration (reliability table)

| Confidence | n | Accuracy | Mean confidence |
|---|---|---|---|
| 0.0-0.2 | 0 | n/a | - |
| 0.2-0.4 | 0 | n/a | - |
| 0.4-0.6 | 7 | 71.4% | 0.474 |
| 0.6-0.8 | 5 | 100.0% | 0.666 |
| 0.8-0.9 | 6 | 83.3% | 0.88 |
| 0.9-1.0 | 116 | 87.9% | 0.97 |

## Per document

| Doc | Type (truth -> pred) | Conf | Fields | Missing expected issues | Wrong fields |
|---|---|---|---|---|---|
| core_d01_en | discharge_summary -> discharge_summary | 0.98 | 13/13 | - | - |
| core_d02_vi_scan | discharge_summary -> discharge_summary | 0.5 | 4/13 | - | hospital_name: wrong pred='B\x00nh vi\x00n \x01a khoa Ph\x00c H\x00ng Long' truth='Bệnh viện Đa khoa Phước Hưng Long'<br>patient_name: wrong pred='Tr\x00n V\x00n H\x00ng' truth='Trần Văn Hùng'<br>diagnosis: wrong pred={'primary': {'description': 'Vi\x00m ru\x00t th\x00a c\x0...<br>procedures_performed: wrong 'Phẫu thuật nội soi cắt ruột thừa'.name: pred='Ph\x01u thu\x01t n\x00i soi c\x01t ru\x00t th\x00a' truth='Phẫu thuật nội soi cắt ruột thừa'<br>attending_physician: missed truth='Lê Minh Khoa' |
| core_d03_en_conservative | discharge_summary -> discharge_summary | 0.5 | 12/13 | - | procedures_performed: missed truth=[] |
| core_l01_en | lab_report -> lab_report | 0.95 | 6/6 | - | - |
| core_l02_vi | lab_report -> lab_report | 0.98 | 6/6 | - | - |
| core_p01_en | prescription -> prescription | 0.92 | 5/6 | - | diagnosis: missed truth={'primary': {'description': 'Acute bacterial sinusitis', ... |
| core_p02_vi | prescription -> prescription | 0.95 | 5/6 | - | medications: wrong 'Cefuroxim 500mg (Zinnat)'.duration: pred='Dùng 7 ngày' truth='7 ngày'; 'Alpha chymotrypsin 4200 IU'.duration: pred='Dùng 5 ngày' truth='5 ngày' |
| core_r01_en_thb | receipt -> receipt | 1.0 | 12/14 | - | items: wrong 'Corporate discount 10% (Siam Northstar Trading Co., Ltd.)'.total: pred=None truth=-1332.0<br>discount_total: hallucinated pred=1332.0 |
| core_r02_vi_vnd | receipt -> receipt | 0.8 | 13/14 | - | subtotal: hallucinated pred=1226000.0 |
| core_r03_en_sgd_scan | receipt -> receipt | 0.95 | 14/14 | - | - |
| stress_s01_total_mismatch | receipt -> receipt | 0.97 | 12/14 | - | currency: wrong pred='US Dollar (USD)' truth='USD'<br>patient_payable: hallucinated pred=2580.0 |
| stress_s02_blank_fields | receipt -> receipt | 0.92 | 14/14 | - | - |
| stress_s03_vn_einvoice_words | receipt -> receipt | 0.95 | 13/14 | - | payment_method: wrong pred='other' truth='bank_transfer' |
| stress_s04_vn_discharge_heavy_scan | discharge_summary -> discharge_summary | 0.98 | 10/13 | - | diagnosis: wrong pred={'primary': {'description': 'Viêm phổi', 'icd10_code': 'J...<br>procedures_performed: missed truth=[]<br>discharge_status: hallucinated pred='deceased' |
| stress_s05_handwritten_rx | prescription -> prescription | 0.98 | 5/6 | - | diagnosis: missed truth={'primary': {'description': 'Acute bronchitis', 'icd10_co... |
| stress_s06_lab_wrong_flag | lab_report -> lab_report | 0.96 | 6/6 | - | - |
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
| admission_date | 4 | 100.0% | 0 | 0 | 0.833 |
| amount_in_words | 6 | 100.0% | 0 | 0 | 0.312 |
| attending_physician | 4 | 75.0% | 0 | 1 | 0.672 |
| collection_date | 3 | 100.0% | 0 | 0 | 0.97 |
| currency | 6 | 83.3% | 0 | 0 | 0.925 |
| date | 12 | 100.0% | 0 | 0 | 0.963 |
| department | 4 | 75.0% | 0 | 0 | 0.97 |
| diagnosis | 7 | 42.9% | 0 | 2 | 0.723 |
| discharge_date | 4 | 100.0% | 0 | 0 | 0.877 |
| discharge_instructions | 4 | 75.0% | 0 | 0 | 0.948 |
| discharge_status | 4 | 50.0% | 2 | 0 | 0.97 |
| discount_total | 6 | 83.3% | 1 | 0 | 0.245 |
| doctor_name | 3 | 100.0% | 0 | 0 | 0.97 |
| facility_name | 3 | 100.0% | 0 | 0 | 0.97 |
| follow_up_date | 4 | 75.0% | 1 | 0 | 0.397 |
| grand_total | 6 | 100.0% | 0 | 0 | 0.813 |
| hospital_name | 10 | 90.0% | 0 | 0 | 0.97 |
| insurance_covered_amount | 6 | 100.0% | 0 | 0 | 0.39 |
| invoice_number | 6 | 100.0% | 0 | 0 | 0.97 |
| items | 6 | 83.3% | 0 | 0 | 0.809 |
| lab_name | 3 | 100.0% | 0 | 0 | 0.97 |
| medications | 3 | 66.7% | 0 | 0 | 0.863 |
| ordering_physician | 3 | 100.0% | 0 | 0 | 0.97 |
| patient_name | 16 | 93.8% | 0 | 0 | 0.916 |
| patient_payable | 6 | 83.3% | 1 | 0 | 0.535 |
| payment_method | 6 | 83.3% | 0 | 0 | 0.825 |
| procedures_performed | 4 | 25.0% | 0 | 2 | 0.56 |
| sick_leave_from | 4 | 100.0% | 0 | 0 | 0.318 |
| sick_leave_to | 4 | 100.0% | 0 | 0 | 0.318 |
| subtotal | 6 | 83.3% | 1 | 0 | 0.535 |
| tax_amount | 6 | 100.0% | 0 | 0 | 0.39 |
| tests | 3 | 100.0% | 0 | 0 | 0.8 |

## Errors

- stress_s07_receipt_photo: provider_unavailable: Groq rate limited after retries: Error code: 429 - {'error': {'message': "Request too large for model `qwen/qwen3.8-27b` in organization `org_01m5nkt7k9et5v14vpdmdsyrjn` service tier `on_demand` on output tokens per minute (OTPM): Limit 1000, Requested 1582. The request's expected output tokens exceed the enforced limit; reduce max_tokens (or the request's expected output) and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing", 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
- stress_s08_restaurant: provider_unavailable: Groq daily quota exhausted: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01m5nkt7k9et5v14vpdmdsyrjn` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199677, Requested 3482. Please try again in 22m44.688s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
