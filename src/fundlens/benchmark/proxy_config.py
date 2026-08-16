"""Maps each BenchmarkId to the real AMFI-listed index fund/ETF whose NAV
stands in for that benchmark's TRI (PRD §10 risk mitigation, data-model.md
`benchmark_history`).

**Why a proxy at all**: true TRI index series (Nifty 50 TRI, BSE 100 TRI,
etc.) require either a paid data vendor or scraping NSE/niftyindices.com,
which live-verified during P1.1 return 403/503 to unauthenticated requests —
the same class of fragile, bot-hostile dependency the PRD explicitly avoids
elsewhere (its own note against scraping Groww/MFCentral). The PRD's own
§10 risk register names "index-fund NAV as TRI proxy" as the documented
fallback; there is currently no working free alternative, so this is the
primary (not merely a last-resort) data source for every benchmark.

**Why these specific schemes**: for each benchmark, multiple direct-plan
index funds exist in the AMFI feed; the one with the longest available NAV
history via mfapi.in was chosen empirically (live-checked 2026-08), since a
short-history proxy would silently truncate every long-horizon benchmark
comparison:

    NIFTY50_TRI            UTI Nifty 50 Index Fund - Direct Growth
                            (amfi_code 120716) — history from 2013-01-02,
                            the earliest possible for any direct-plan
                            scheme (SEBI's direct-plan mandate took effect
                            2013-01-01).
    NIFTY_MIDCAP150_TRI     Motilal Oswal Nifty Midcap 150 Index Fund -
                            Direct Plan (amfi_code 147622) — history from
                            2019-09-11, longer than Nippon India's or
                            Aditya Birla's same-index funds.
    NIFTY_SMALLCAP250_TRI   Motilal Oswal Nifty Smallcap 250 Index Fund -
                            Direct Plan (amfi_code 147623) — history from
                            2019-09-11, same reasoning.
    BSE100_TRI              SBI BSE 100 ETF (amfi_code 134014) — history
                            from 2015-03-25. No open-ended index fund
                            tracking BSE 100 specifically was found in the
                            live AMFI feed (only BSE Sensex trackers exist
                            as open-ended funds); this ETF was the only
                            real BSE 100 tracker available.

Revisit this mapping if a longer-history or lower-tracking-error fund
appears later — it's a config table, not a permanent commitment.
"""

from fundlens.db.models import BenchmarkId

PROXY_SCHEME_BY_BENCHMARK: dict[BenchmarkId, str] = {
    BenchmarkId.NIFTY50_TRI: "120716",
    BenchmarkId.NIFTY_MIDCAP150_TRI: "147622",
    BenchmarkId.NIFTY_SMALLCAP250_TRI: "147623",
    BenchmarkId.BSE100_TRI: "134014",
}

PROXY_NOTE_BY_BENCHMARK: dict[BenchmarkId, str] = {
    BenchmarkId.NIFTY50_TRI: (
        "Proxy: UTI Nifty 50 Index Fund (Direct, Growth) NAV — no free TRI source available"
    ),
    BenchmarkId.NIFTY_MIDCAP150_TRI: (
        "Proxy: Motilal Oswal Nifty Midcap 150 Index Fund (Direct) NAV — "
        "no free TRI source available"
    ),
    BenchmarkId.NIFTY_SMALLCAP250_TRI: (
        "Proxy: Motilal Oswal Nifty Smallcap 250 Index Fund (Direct) NAV — "
        "no free TRI source available"
    ),
    BenchmarkId.BSE100_TRI: (
        "Proxy: SBI BSE 100 ETF NAV — no free TRI source or open-ended "
        "BSE 100 index fund available"
    ),
}
