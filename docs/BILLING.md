# Billing: per cab-day

**Model:** the organization (cab admin / company) is charged **₹50 (configurable) per cab-day + GST**. A *cab-day* is one vehicle that has at least one CONFIRMED, IN_PROGRESS or COMPLETED trip on a service date. Trips cancelled before confirmation do not count. Drivers are not billed (see below).

## How it works
1. Platform admin → **Billing & invoices** → *Manage* an organization: tick *Charge this organization*, set rupees per cab-day, GST %, optional **free until** date (pilot), days until due, grace days.
2. The organization admin sees **Billing**: this month's cab-days and estimate, invoices, and your payment instructions (`BILLING_PAYMENT_INSTRUCTIONS`, `BILLING_SELLER_NAME`, `BILLING_SELLER_GSTIN` on the API).
3. The **worker** (hourly) issues last month's invoice for every organization with billing on (idempotent: one invoice per organization per month), notifies the org admins, and numbers it `RP/<financial year>/<6 digits>`.
4. The customer pays by UPI / bank transfer. You press **Mark paid** and type the reference.
5. If an invoice is still unpaid **due days + grace days** after issue, the worker **pauses the organization** (everyone is refused except the org admin's *Billing* page and sign-in, and a critical notification goes to org admins). Marking the invoice paid (or voiding it) lifts a billing pause immediately. A pause set manually by the platform admin is **never** lifted by a payment.
6. You can also issue an invoice yourself for any past or current month from the *Manage* dialog.

## Money rules
Integer paise throughout; GST rounded half-up per invoice; free-pilot cab-days are counted on the invoice but not charged; no invoice is created when nothing is billable.

## Charging drivers too?
Not built, deliberately. Drivers are paid per trip and are usually many, small payers; collecting from them means hundreds of payments, refunds and support. The simpler option is to let the cab admin recover the cost from drivers in their own settlement. If you still want it later, it needs a payment gateway and a separate per-driver ledger.

## What is NOT done
- No online payment gateway (Razorpay etc.): payment is recorded by you after you see the money.
- Invoice format and numbering have not been reviewed by an accountant. A GST tax invoice has legal fields (HSN/SAC code, place of supply, your registration details, CGST/SGST vs IGST split) that this simple invoice does not fully cover. **Have a chartered accountant approve the layout before sending real invoices.** Invoice numbers come from a database sequence, so a failed issue can leave a gap.
- Billing days use the UTC calendar day for "this month so far" on the Billing page and the organization time zone for the worker's month boundaries; trips near midnight on month ends should be spot-checked.
- Pausing an organization stops drivers and SOS for that organization. Decide your policy carefully and warn customers in your agreement; the default grace is 7 days after a 7-day due period.
