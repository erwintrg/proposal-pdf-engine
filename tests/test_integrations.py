"""Real-mode integrations, tested without a network: the Stripe request is a pure function, and the
Drive upload runs against a small in-memory stand-in for the Drive API."""

import re

import pytest

from proposal_engine.integrations.stripe_deposit import StripeError, create_deposit_link, deposit_link_request
from proposal_engine.money import totals_for
from proposal_engine.schema import parse_proposal


def test_stripe_request_uses_the_deposit_in_minor_units(sample, ycat):
    request = deposit_link_request(sample, totals_for(sample), ycat.agency)
    assert request["price"] == {"currency": "eur", "unit_amount": 285600}
    assert request["product"]["name"] == "Delivery proof on autopilot for Acme Freight - 50% deposit"
    assert request["product"]["metadata"] == {"proposal": "acme-freight", "invoice": "D-20261001-ACME-FREIGHT"}
    assert request["payment_link"]["invoice_creation"]["enabled"] is True
    assert "Thank you, Jane." in request["payment_link"]["after_completion"]["hosted_confirmation"]["custom_message"]


def test_stripe_idempotency_key_is_stable_and_tracks_the_amount(sample_data, ycat):
    first = parse_proposal(sample_data)
    key = deposit_link_request(first, totals_for(first), ycat.agency)["idempotency_key"]
    assert key == deposit_link_request(first, totals_for(first), ycat.agency)["idempotency_key"]
    sample_data["pricing"]["items"][0]["price"] = 5000
    changed = parse_proposal(sample_data)
    assert deposit_link_request(changed, totals_for(changed), ycat.agency)["idempotency_key"] != key


def test_stripe_is_refused_in_marketplace_mode(sample_data, ycat):
    sample_data["mode"], sample_data["marketplace"] = "marketplace", "Upwork"
    proposal = parse_proposal(sample_data)
    with pytest.raises(StripeError, match="marketplace"):
        deposit_link_request(proposal, totals_for(proposal), ycat.agency)


def test_stripe_without_a_key_fails_before_any_request(sample, ycat):
    with pytest.raises(StripeError):
        create_deposit_link(deposit_link_request(sample, totals_for(sample), ycat.agency), api_key="")


class _Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeDrive:
    """Just enough of the Drive v3 client for upload_pdf."""

    FOLDER = "application/vnd.google-apps.folder"

    def __init__(self):
        self.items, self.updated, self.shared = [], [], []

    def files(self):
        return self

    def permissions(self):
        return self

    def list(self, q, fields=None, spaces=None):
        name = re.search(r"name = '((?:[^'\\]|\\.)*)'", q).group(1).replace("\\'", "'")
        parent = re.search(r"'([^']+)' in parents", q).group(1)
        hits = [i for i in self.items if i["name"] == name and parent in i["parents"]
                and (self.FOLDER not in q or i["mimeType"] == self.FOLDER)]
        return _Call({"files": [{"id": i["id"]} for i in hits]})

    def create(self, body=None, fields=None, media_body=None, fileId=None):
        if fileId:  # permissions().create
            self.shared.append((fileId, body))
            return _Call({"id": fileId})
        item = {"id": f"id{len(self.items) + 1}", "name": body["name"],
                "mimeType": body.get("mimeType", "application/pdf"), "parents": body.get("parents", ["root"])}
        self.items.append(item)
        return _Call({"id": item["id"], "webViewLink": f"https://drive.example/{item['id']}"})

    def update(self, fileId, media_body=None, fields=None):
        self.updated.append(fileId)
        return _Call({"id": fileId, "webViewLink": f"https://drive.example/{fileId}"})


def test_drive_upload_creates_folders_once_and_replaces_the_file(tmp_path):
    pytest.importorskip("googleapiclient")
    from proposal_engine.integrations.drive_upload import upload_pdf

    pdf = tmp_path / "acme-freight.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    drive = FakeDrive()
    first = upload_pdf(pdf, "acme-freight", service=drive)
    second = upload_pdf(pdf, "acme-freight", share=True, service=drive)
    assert [i["name"] for i in drive.items] == ["Proposals", "acme-freight", "acme-freight.pdf"]
    assert first == second == "https://drive.example/id3"
    assert drive.updated == ["id3"]
    assert drive.shared == [("id3", {"type": "anyone", "role": "reader"})]


def test_drive_needs_credentials(monkeypatch):
    from proposal_engine.integrations.drive_upload import DriveError, credentials_from_env

    with pytest.raises(DriveError, match="no Google credentials"):
        credentials_from_env({})
