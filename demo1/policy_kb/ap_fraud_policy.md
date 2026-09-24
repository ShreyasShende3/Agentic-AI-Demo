# Accounts Payable Fraud & Anomaly Policy (fictional, for demo purposes only)

This policy governs how incoming vendor invoices are screened before payment.
It targets the most common real-world vendor-payment fraud pattern, Business
Email Compromise (BEC): an attacker impersonates a legitimate vendor and asks
Accounts Payable to redirect a payment to a new bank account.

## Bank account changes

A vendor's payment bank account is "recently changed" if `bank_account_age_days`
is 14 or fewer. A recent bank account change is not automatically fraud - new
vendors legitimately have young accounts too - but it must always be paired
with the invoice amount before a decision is made.

## Purchase order matching

Every invoice should reference a purchase order (PO) and match its amount.
A mismatch of more than 10% between the invoice amount and the referenced
PO amount is a red flag for inflated or altered invoices. Minor variance
(partial shipments, rounding) under 10% is normal and not a red flag.

## Duplicate invoices

The same invoice number should never be paid twice. A duplicate invoice
number appearing again is a red flag for resubmission fraud, whether
accidental or deliberate.

## Repeat vendor history

If a vendor has been flagged before (recorded in memory from a prior
invoice), treat any new invoice from that same vendor with extra scrutiny,
especially if the same recently-changed bank account is involved again.

## Guardrail invariants

These three rules are absolute - no justification overrides them:

- An invoice of $5,000 or more where `bank_account_age_days` is 14 or fewer
  can never be **Approve** - it must be **Hold for Review**.
- An invoice whose amount differs from its referenced PO amount by more
  than 10% can never be **Approve**.
- An invoice number that has already been paid before (a duplicate) can
  never be **Approve**.

Everything else defaults to **Approve** unless one of the above applies.
