# Evaluation report

Documents: 10 - errors: 0

| Metric | Value |
|---|---|
| Classification accuracy | 100.0% |
| Field accuracy (all fields) | 97.1% |
| Field accuracy (brief-required fields) | 96.5% |
| Cell accuracy (table cells counted individually) | 97.6% |
| Hallucination rate (value where truth is null) | 5.6% |
| Miss rate (null where truth has a value) | 0.0% |
| Validation recall (seeded defects caught) | n/a |
| Low-confidence recall (blank/smudged fields flagged) | n/a |
| ECE / Brier | 0.1709 / 0.1642 |
| Accuracy at confidence >= 0.9 (coverage) | 96.2% (76.2%) |
| Cost per document | $0.0000 (total $0.0000) |
| Latency p50 / p95 | 35417 ms / 52953 ms |

## Calibration (reliability table)

| Confidence | n | Accuracy | Mean confidence |
|---|---|---|---|
| 0.0-0.2 | 17 | 100.0% | 0.1 |
| 0.2-0.4 | 0 | n/a | - |
| 0.4-0.6 | 0 | n/a | - |
| 0.6-0.8 | 5 | 100.0% | 0.679 |
| 0.8-0.9 | 3 | 100.0% | 0.853 |
| 0.9-1.0 | 80 | 96.3% | 0.97 |

## Per document

| Doc | Type (truth -> pred) | Conf | Fields | Missing expected issues | Wrong fields |
|---|---|---|---|---|---|
| core_d01_en | discharge_summary -> discharge_summary | 1.0 | 13/13 | - | - |
| core_d02_vi_scan | discharge_summary -> discharge_summary | 0.99 | 13/13 | - | - |
| core_d03_en_conservative | discharge_summary -> discharge_summary | 0.98 | 13/13 | - | - |
| core_l01_en | lab_report -> lab_report | 1.0 | 6/6 | - | - |
| core_l02_vi | lab_report -> lab_report | 1.0 | 6/6 | - | - |
| core_p01_en | prescription -> prescription | 1.0 | 6/6 | - | - |
| core_p02_vi | prescription -> prescription | 1.0 | 5/6 | - | medications: wrong 'Cefuroxim 500mg (Zinnat)'.dosage: pred='500mg' truth='1 viên'; 'Cefuroxim 500mg (Zinnat)'.frequency: pred='Ngày uống 2 lần, mỗi lần 1 viên, sau ăn sáng và tối' truth='Ngày uống 2 lần'; 'Paracetamol 500mg (Panadol)'.dosage: pred='500mg' truth='1 viên'; 'Paracetamol 500mg (Panadol)'.frequency: pred='Ngày uống 3 lần, mỗi lần 1 viên, khi sốt trên 38.5 độ ho... truth='Ngày uống 3 lần'; 'Alpha chymotrypsin 4200 IU'.dosage: pred='4200 IU' truth='2 viên'; 'Alpha chymotrypsin 4200 IU'.frequency: pred='Ngày ngậm 2 lần, mỗi lần 2 viên' truth='Ngày ngậm 2 lần' |
| core_r01_en_thb | receipt -> receipt | 1.0 | 12/14 | - | items: wrong 'Nursing care fee, per day'.category: pred='room' truth='other'<br>discount_total: hallucinated pred=1332.0 |
| core_r02_vi_vnd | receipt -> receipt | 0.8 | 14/14 | - | - |
| core_r03_en_sgd_scan | receipt -> receipt | 1.0 | 14/14 | - | - |

## Per field

| Field | n | Accuracy | Hallucinated | Missed | Mean conf |
|---|---|---|---|---|---|
| admission_date | 3 | 100.0% | 0 | 0 | 0.97 |
| amount_in_words | 3 | 100.0% | 0 | 0 | 0.39 |
| attending_physician | 3 | 100.0% | 0 | 0 | 0.94 |
| collection_date | 2 | 100.0% | 0 | 0 | 0.97 |
| currency | 3 | 100.0% | 0 | 0 | 0.873 |
| date | 7 | 100.0% | 0 | 0 | 0.97 |
| department | 3 | 100.0% | 0 | 0 | 0.97 |
| diagnosis | 5 | 100.0% | 0 | 0 | 0.97 |
| discharge_date | 3 | 100.0% | 0 | 0 | 0.97 |
| discharge_instructions | 3 | 100.0% | 0 | 0 | 0.679 |
| discharge_status | 3 | 100.0% | 0 | 0 | 0.68 |
| discount_total | 3 | 66.7% | 1 | 0 | 0.39 |
| doctor_name | 2 | 100.0% | 0 | 0 | 0.97 |
| facility_name | 2 | 100.0% | 0 | 0 | 0.97 |
| follow_up_date | 3 | 100.0% | 0 | 0 | 0.39 |
| grand_total | 3 | 100.0% | 0 | 0 | 0.97 |
| hospital_name | 6 | 100.0% | 0 | 0 | 0.97 |
| insurance_covered_amount | 3 | 100.0% | 0 | 0 | 0.68 |
| invoice_number | 3 | 100.0% | 0 | 0 | 0.94 |
| items | 3 | 66.7% | 0 | 0 | 0.97 |
| lab_name | 2 | 100.0% | 0 | 0 | 0.97 |
| medications | 2 | 50.0% | 0 | 0 | 0.97 |
| ordering_physician | 2 | 100.0% | 0 | 0 | 0.97 |
| patient_name | 10 | 100.0% | 0 | 0 | 0.97 |
| patient_payable | 3 | 100.0% | 0 | 0 | 0.68 |
| payment_method | 3 | 100.0% | 0 | 0 | 0.873 |
| procedures_performed | 3 | 100.0% | 0 | 0 | 0.913 |
| sick_leave_from | 3 | 100.0% | 0 | 0 | 0.39 |
| sick_leave_to | 3 | 100.0% | 0 | 0 | 0.39 |
| subtotal | 3 | 100.0% | 0 | 0 | 0.39 |
| tax_amount | 3 | 100.0% | 0 | 0 | 0.39 |
| tests | 2 | 100.0% | 0 | 0 | 0.97 |
