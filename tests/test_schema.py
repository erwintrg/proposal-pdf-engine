import pytest

from proposal_engine.schema import ProposalError, dump_proposal, parse_proposal, slugify


def problems_for(data):
    with pytest.raises(ProposalError) as info:
        parse_proposal(data)
    return info.value.problems


def paths(problems):
    return {p.path for p in problems}


def test_sample_is_valid(sample):
    assert sample.client.company == "Acme Freight GmbH"
    assert sample.effective_slug == "acme-freight"
    assert sample.effective_invoice_number == "D-20261001-ACME-FREIGHT"
    assert sample.full_title == "Delivery proof on autopilot for Acme Freight"


def test_missing_field_is_reported_with_its_path(sample_data):
    del sample_data["client"]["company"]
    assert "client.company" in paths(problems_for(sample_data))


def test_unknown_key_is_rejected(sample_data):
    sample_data["scop"] = {"items": ["typo"]}
    problems = problems_for(sample_data)
    assert "scop" in paths(problems)


def test_price_must_be_a_number(sample_data):
    sample_data["pricing"]["items"][0]["price"] = "4.800 EUR"
    assert "pricing.items.0.price" in paths(problems_for(sample_data))


def test_negative_price_and_zero_quantity_are_rejected(sample_data):
    sample_data["pricing"]["items"][0]["price"] = -10
    sample_data["pricing"]["items"][0]["qty"] = 0
    assert {"pricing.items.0.price", "pricing.items.0.qty"} <= paths(problems_for(sample_data))


@pytest.mark.parametrize("pct, ok", [(0, False), (-5, False), (150, False), (10, True), (33.5, True), (100, True)])
def test_deposit_percentage_bounds(sample_data, pct, ok):
    sample_data["payment_terms"]["deposit_pct"] = pct
    if ok:
        assert parse_proposal(sample_data).payment_terms.deposit_pct == pct
    else:
        assert "payment_terms.deposit_pct" in paths(problems_for(sample_data))


def test_unsupported_currency_is_rejected(sample_data):
    sample_data["currency"] = "JPY"
    assert "currency" in paths(problems_for(sample_data))


def test_vat_rate_is_a_fraction(sample_data):
    sample_data["vat"]["rate"] = 19
    assert "vat.rate" in paths(problems_for(sample_data))


def test_empty_sections_are_rejected(sample_data):
    sample_data["problem"]["items"] = []
    sample_data["scope"]["items"] = []
    assert {"problem.items", "scope.items"} <= paths(problems_for(sample_data))


def test_marketplace_mode_needs_the_marketplace_name(sample_data):
    sample_data["mode"] = "marketplace"
    problems = problems_for(sample_data)
    assert any("marketplace name" in p.message for p in problems)
    sample_data["marketplace"] = "Upwork"
    assert parse_proposal(sample_data).mode == "marketplace"


@pytest.mark.parametrize("dash", ["\u2014", "\u2013"])
def test_em_and_en_dashes_are_flagged_where_they_are(sample_data, dash):
    sample_data["problem"]["items"][1]["body"] += f" {dash} every single evening."
    problems = problems_for(sample_data)
    assert [p.path for p in problems] == ["problem.items.1.body"]
    assert "dash" in problems[0].message


def test_placeholders_are_flagged(sample_data):
    sample_data["subtitle"] = "Hi [CLIENT NAME], here is the plan."
    sample_data["scope"]["outro"] = "Hosting: TBD"
    assert {"subtitle", "scope.outro"} <= paths(problems_for(sample_data))


def test_ordinary_brackets_and_hyphens_are_fine(sample_data):
    sample_data["subtitle"] = "Same-day reports [see page 4], from 2-3 days to hours."
    parse_proposal(sample_data)


def test_client_email_and_country_format(sample_data):
    sample_data["client"]["email"] = "jane-at-acme"
    sample_data["client"]["country"] = "Germany"
    assert {"client.email", "client.country"} <= paths(problems_for(sample_data))


def test_deposit_link_rules(sample_data):
    sample_data["deposit_link"] = "javascript:alert(1)"
    assert "deposit_link" in paths(problems_for(sample_data))
    sample_data["deposit_link"] = "https://buy.stripe.com/test_example"
    sample_data["mode"], sample_data["marketplace"] = "marketplace", "Upwork"
    assert "deposit_link" in paths(problems_for(sample_data))


def test_dump_round_trips(sample, golden_data):
    import json

    assert json.loads(dump_proposal(sample)) == golden_data
    assert parse_proposal(json.loads(dump_proposal(sample))) == sample


@pytest.mark.parametrize("company, slug", [
    ("Acme Freight GmbH", "acme-freight"),
    ("Müller & Söhne AG", "muller-sohne"),
    ("Straßenbau Nord UG", "strassenbau-nord"),
    ("Co", "co"),
    ("!!!", "proposal"),
])
def test_slugify(company, slug):
    assert slugify(company) == slug
