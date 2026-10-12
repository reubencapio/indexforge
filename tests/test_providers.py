"""The Financial Modeling Prep and Finnhub connectors, against stand-in answers."""

import io
import json
import urllib.error

import pytest

from indexforge import DataSourceError, FinnhubConnector, FMPConnector
from indexforge.data.connectors import _http, finnhub


@pytest.fixture
def web(monkeypatch):
    """Answers requests from a table of path -> answer, and records the requests."""
    answers: dict[str, object] = {}
    requests: list[tuple[str, dict]] = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, timeout=None):
        from urllib.parse import parse_qs, urlparse

        url = urlparse(request.full_url)
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        requests.append((url.path, params))
        for path, answer in answers.items():
            if url.path.endswith(path):
                if callable(answer):
                    answer = answer(params)
                if isinstance(answer, int):
                    raise urllib.error.HTTPError(request.full_url, answer, "refused", {}, None)
                return Response(json.dumps(answer).encode())
        return Response(b"[]")

    monkeypatch.setattr(_http.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(finnhub.time, "sleep", lambda seconds: None)
    return answers, requests


FMP_PROFILE = {
    "symbol": "SAP.DE",
    "companyName": "SAP SE",
    "marketCap": 250e9,
    "currency": "EUR",
    "country": "DE",
    "sector": "Technology",
    "industry": "Software",
    "exchange": "XETRA",
    "averageVolume": 1_500_000,
    "price": 210.5,
    "description": "Enterprise software.",
}


class TestFMP:
    def test_needs_a_key(self, monkeypatch):
        monkeypatch.delenv("FMP_API_KEY", raising=False)
        with pytest.raises(ValueError, match="needs an API key"):
            FMPConnector()

    def test_profile_becomes_a_company(self, web):
        answers, requests = web
        answers["/profile"] = [FMP_PROFILE]

        [sap] = FMPConnector(api_key="k").get_constituent_data(["SAP.DE"])

        assert (sap.name, sap.country, sap.sector) == ("SAP SE", "Germany", "Technology")
        assert sap.market_cap == 250e9 and sap.price == 210.5
        assert str(sap.currency) == "EUR"
        assert sap.business_description == "Enterprise software."
        assert requests[0][1] == {"symbol": "SAP.DE", "apikey": "k"}

    def test_older_field_names_are_read_too(self, web):
        answers, _ = web
        answers["/profile"] = [
            {"companyName": "Old Co", "mktCap": 7e9, "volAvg": 12, "country": "US"}
        ]

        [old] = FMPConnector(api_key="k").get_constituent_data(["OLD"])

        assert old.market_cap == 7e9 and old.average_daily_volume == 12
        assert old.country == "United States"

    def test_unknown_ticker_is_a_bare_record(self, web):
        [gone] = FMPConnector(api_key="k").get_constituent_data(["GONE"])
        assert gone.market_cap == 0 and gone.country == "Unknown"

    @pytest.mark.parametrize(
        ("status", "message"),
        [(401, "rejected the API key"), (402, "not included in the plan"), (429, "limiting")],
    )
    def test_refusals_fail_with_the_reason_and_never_the_key(self, web, status, message):
        answers, _ = web
        answers["/profile"] = status

        with pytest.raises(DataSourceError, match=message) as error:
            FMPConnector(api_key="very-secret").get_constituent_data(["SAP.DE"])
        assert "very-secret" not in str(error.value)

    def test_a_refusal_sent_as_a_normal_answer_fails_too(self, web):
        answers, _ = web
        answers["/profile"] = {"Error Message": "Limit Reach."}
        with pytest.raises(DataSourceError, match="Limit Reach"):
            FMPConnector(api_key="k").get_constituent_data(["SAP.DE"])

    def test_screen_asks_by_country_and_keeps_sizes(self, web):
        answers, requests = web
        answers["/quote"] = [{"price": 0.9}]
        answers["/company-screener"] = [
            {
                "symbol": "SAP.DE",
                "companyName": "SAP SE",
                "marketCap": 250e9,
                "volume": 9,
                "price": 210,
            },
            {"symbol": "SAP.F", "companyName": "SAP SE", "marketCap": 250e9, "volume": 1},
            {"symbol": "FUND.DE", "companyName": "A Fund", "isEtf": True},
        ]
        connector = FMPConnector(api_key="k")

        tickers = connector.screen(
            ["Germany", "Atlantis"], sectors=["Information Technology"], min_market_cap=1e9
        )

        assert tickers == ["SAP.DE"]
        assert connector.screened_quotes() == {"SAP.DE": {"market_cap": 250e9, "price": 210.0}}
        assert connector.unscreenable_countries(["Germany", "Atlantis"]) == ["Atlantis"]
        [(_, params)] = [r for r in requests if r[0].endswith("/company-screener")]
        assert params["country"] == "DE" and params["sector"] == "Technology"
        assert params["marketCapMoreThan"] == str(int(1e9 * 0.9 * 0.5))

    def test_exchange_rate_outside_the_plan_reads_as_missing(self, web):
        answers, _ = web
        answers["/quote"] = 402
        assert FMPConnector(api_key="k").get_fx_rate("EUR", "USD") is None


class TestFinnhub:
    def test_needs_a_key(self, monkeypatch):
        monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
        with pytest.raises(ValueError, match="needs an API key"):
            FinnhubConnector()

    def test_profile_becomes_a_company_with_size_in_whole_units(self, web):
        answers, requests = web
        answers["/stock/profile2"] = {
            "name": "Apple Inc",
            "country": "US",
            "currency": "USD",
            "finnhubIndustry": "Technology",
            "marketCapitalization": 3_500_000,
            "exchange": "NASDAQ",
        }

        [apple] = FinnhubConnector(api_key="k").get_constituent_data(["AAPL"])

        assert apple.market_cap == 3.5e12
        assert (apple.country, apple.industry) == ("United States", "Technology")
        assert requests[0][1] == {"symbol": "AAPL", "token": "k"}

    def test_unknown_ticker_is_a_bare_record(self, web):
        answers, _ = web
        answers["/stock/profile2"] = {}
        [gone] = FinnhubConnector(api_key="k").get_constituent_data(["GONE"])
        assert gone.market_cap == 0

    def test_requests_are_spaced_out(self, web, monkeypatch):
        answers, _ = web
        answers["/stock/profile2"] = {"name": "X", "marketCapitalization": 1}
        waits = []
        monkeypatch.setattr(finnhub.time, "sleep", waits.append)

        FinnhubConnector(api_key="k").get_constituent_data(["A", "B", "C"])

        assert len(waits) == 2 and all(0 < w <= finnhub.REQUEST_INTERVAL for w in waits)

    def test_cannot_search_by_rule(self, web):
        with pytest.raises(NotImplementedError):
            FinnhubConnector(api_key="k").screen(["United States"], min_market_cap=1e9)

    def test_a_rejected_key_fails_clearly(self, web):
        answers, _ = web
        answers["/stock/profile2"] = 401
        with pytest.raises(DataSourceError, match="rejected the API key"):
            FinnhubConnector(api_key="k").get_constituent_data(["AAPL"])
