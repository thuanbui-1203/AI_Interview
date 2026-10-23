"""Hospital invoice / receipt (VN: Hóa đơn viện phí, Bảng kê chi phí khám chữa bệnh, Phiếu thu)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from medextract.core.fields import Bucket, ChoiceField, DateField, ListField, MoneyField, Row, TextField
from medextract.core.registry import DocTypeSpec
from medextract.doctypes._common import CURRENCY_DESCRIPTION, ExtractionBase

ItemCategory = Literal[
    "room", "consultation", "drug", "lab", "imaging", "procedure", "surgery", "consumable", "discount", "tax", "other"
]  # fmt: skip
PaymentMethod = Literal["cash", "card", "bank_transfer", "e_wallet", "insurance_direct_billing", "other"]


class ReceiptItem(Row):
    description: str | None
    quantity: float | None = Field(description="As printed; null if the row has no quantity.")
    unit_price: float | None = Field(description="As printed; null if the row has no unit price.")
    total: float | None = Field(description="Line total exactly as printed. Discount/refund lines are negative.")
    category: ItemCategory | None
    confidence: Bucket


class ReceiptExtraction(ExtractionBase):
    currency: TextField = Field(description=CURRENCY_DESCRIPTION)
    hospital_name: TextField
    patient_name: TextField
    date: DateField = Field(description="Invoice / issue date (not the service or admission date).")
    items: ListField[ReceiptItem]
    grand_total: MoneyField = Field(
        description="Total charges after discounts and tax, BEFORE deducting insurance/BHYT payments or deposits (VN: 'Tổng cộng', 'Tổng chi phí'; EN: 'Total', 'Grand total', 'Total charges')."
    )
    payment_method: ChoiceField[PaymentMethod]
    invoice_number: TextField
    subtotal: MoneyField = Field(description="The amount on a line labelled Subtotal / Sub-total / Cộng tiền hàng (before tax), only if printed as its own line.")
    discount_total: MoneyField = Field(
        description="Total discount as a POSITIVE amount, only if printed as a separate summary line in the totals section. A discount printed only as an item row stays in items (null here)."
    )
    tax_amount: MoneyField = Field(description="VAT / tax amount, only if printed.")
    insurance_covered_amount: MoneyField = Field(
        description="Amount paid by insurance / BHYT fund / direct billing (VN: 'Quỹ BHYT trả', 'BHYT chi trả'), only if printed."
    )
    patient_payable: MoneyField = Field(
        description="What the patient owes / paid after insurance and deposits (VN: 'Người bệnh trả', 'Số tiền phải nộp'; EN: 'Amount due', 'Patient pays'), only if printed as its own line distinct from the grand total. If the document prints a single total line, that is grand_total and this stays null."
    )
    amount_in_words: TextField = Field(description="The total written in words (VN: 'Bằng chữ', 'Số tiền viết bằng chữ'), verbatim.")


HINTS = """\
RECEIPT / HOSPITAL INVOICE
- hospital_name: the issuing facility on the letterhead (not a department, not the insurer).
- patient_name: the patient (VN: 'Họ tên người bệnh', 'Tên khách hàng', 'Người mua'). Never the cashier, doctor or guarantor.
- items: every printed charge line, including discount lines (negative total, category 'discount') and tax lines if printed as rows.
  Do NOT include subtotal/total/payment/balance lines as items. If a row's line total is printed, copy it even if qty x unit price disagrees.
- Several totals may be printed (subtotal, total, BHYT/insurance paid, deposit 'Tạm ứng', amount due). Map each to its own field;
  grand_total is the gross total of charges before insurance and deposits.
- payment_method: only if printed (cash 'Tiền mặt', card/'Thẻ', transfer 'Chuyển khoản', e-wallet e.g. MoMo/ZaloPay). A blank line means not_found.
- Vietnamese amounts use '.' as thousands separator ('1.250.000' is one million two hundred fifty thousand)."""

CUES = "Lists billed services/drugs with prices and a total amount to pay; words like invoice, receipt, bill, hóa đơn, phiếu thu, bảng kê chi phí."

SPEC = DocTypeSpec(
    name="receipt",
    description="Hospital invoice, bill or payment receipt listing charged items with prices and a total.",
    schema=ReceiptExtraction,
    required_fields=("hospital_name", "patient_name", "date", "items", "grand_total", "payment_method"),
    prompt_hints=HINTS,
    classifier_cues=CUES,
    magnitude_fields=("discount_total", "insurance_covered_amount"),
    inferred_fields=("currency",),
)
