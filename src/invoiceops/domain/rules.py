from invoiceops.domain.models import Decision, Invoice

RULE_VERSION = "invoice-rules-v1"
AUTO_PROCESS_LIMIT_CENTS = 500_000


def decide_invoice(invoice: Invoice) -> Decision:
    if (
        invoice.invoice_amount_cents <= AUTO_PROCESS_LIMIT_CENTS
        and invoice.has_purchase_order
        and invoice.three_way_match
    ):
        return Decision.AUTO_PROCESS
    return Decision.MANUAL_REVIEW
