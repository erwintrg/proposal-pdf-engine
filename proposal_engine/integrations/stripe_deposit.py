"""Stripe deposit link (real mode only: render.py --stripe).

Creates one Product, one Price and one Payment Link for the deposit amount, with Stripe invoice
creation switched on. Needs a restricted key with write access to Products, Prices and Payment Links
in STRIPE_API_KEY.

The request is built by a pure function, so the amounts can be tested without a network. Idempotency
keys are derived from the proposal, so a retry after a network error does not create a second link.
render.py writes the link back into proposal.json, so the next render reuses it.
"""

from __future__ import annotations

from ..money import Totals, format_pct, to_minor_units
from ..schema import Proposal
from ..theme import Agency


class StripeError(Exception):
    pass


def deposit_link_request(proposal: Proposal, totals: Totals, agency: Agency) -> dict:
    if proposal.mode != "direct":
        raise StripeError("marketplace mode never gets an off-platform payment link")
    amount = to_minor_units(totals.deposit_gross, proposal.currency)
    name = f"{proposal.full_title} - {format_pct(totals.deposit_pct)} deposit"
    slug = proposal.effective_slug
    metadata = {"proposal": slug, "invoice": proposal.effective_invoice_number}
    footer = agency.name + (f" · VAT ID {agency.vat_id}" if agency.vat_id else "")
    return {
        "idempotency_key": f"proposal-{slug}-{proposal.date:%Y%m%d}-{proposal.currency}-{amount}",
        "product": {"name": name[:250], "metadata": metadata},
        "price": {"currency": proposal.currency.lower(), "unit_amount": amount},
        "payment_link": {
            "metadata": metadata,
            "invoice_creation": {"enabled": True, "invoice_data": {"description": name[:500], "footer": footer}},
            "after_completion": {
                "type": "hosted_confirmation",
                "hosted_confirmation": {
                    "custom_message": f"Thank you, {proposal.client.first_name}. "
                    "I will be in touch within one business day to kick off."
                },
            },
        },
    }


def create_deposit_link(request: dict, api_key: str) -> str:
    """Runs the three API calls. Returns the payment link URL."""
    try:
        import stripe
    except ImportError:
        raise StripeError("the stripe package is missing: pip install -r requirements-real.txt") from None
    if not api_key:
        raise StripeError("STRIPE_API_KEY is not set (restricted key: write on Products, Prices, Payment Links)")
    client = stripe.StripeClient(api_key)
    key = request["idempotency_key"]
    try:
        product = client.v1.products.create(params=request["product"], options={"idempotency_key": f"{key}-product"})
        price = client.v1.prices.create(
            params={**request["price"], "product": product.id}, options={"idempotency_key": f"{key}-price"}
        )
        link = client.v1.payment_links.create(
            params={**request["payment_link"], "line_items": [{"price": price.id, "quantity": 1}]},
            options={"idempotency_key": f"{key}-link"},
        )
    except stripe.StripeError as err:
        raise StripeError(f"Stripe: {err.user_message or err}") from None
    return link.url
