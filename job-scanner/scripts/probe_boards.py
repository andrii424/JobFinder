"""Verify company slugs against the public ATS endpoints, then rebuild the registry.

    python scripts/probe_boards.py            # probe CANDIDATES, rewrite the registry
    python scripts/probe_boards.py --dry-run  # probe and report, change nothing

Every slug shipped in `scrapers/registry/eu_boards.json` must be verified by a
real request: an invented slug is not an error anyone notices, it is a source
that silently returns nothing forever. Add companies to CANDIDATES and re-run -
only the ones that actually answer with jobs are kept.

Each company is tried against every ATS and the FIRST hit wins, because a
company has one board, not seven.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import pathlib
import sys

import httpx

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept": "application/json, text/xml, */*"}

# (ats, url template, how to count jobs in the response)
ENDPOINTS = {
    "greenhouse":     "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
    "lever":          "https://api.lever.co/v0/postings/{s}?mode=json",
    "ashby":          "https://api.ashbyhq.com/posting-api/job-board/{s}",
    "recruitee":      "https://{s}.recruitee.com/api/offers/",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{s}/postings",
    "personio":       "https://{s}.jobs.personio.de/xml",
    "teamtailor":     "https://{s}.teamtailor.com/jobs.rss",
}


def count_jobs(ats: str, r: httpx.Response) -> int:
    try:
        if ats == "greenhouse":
            return len(r.json().get("jobs", []))
        if ats == "lever":
            return len(r.json())
        if ats == "ashby":
            return len(r.json().get("jobs", []))
        if ats == "recruitee":
            return len(r.json().get("offers", []))
        if ats == "smartrecruiters":
            return int(r.json().get("totalFound", 0))
        if ats == "personio":
            return r.text.count("<position>")
        if ats == "teamtailor":
            return r.text.count("<item>")
    except Exception:
        return 0
    return 0


CANDIDATES: dict[str, list[str]] = {
    "EE": ["bolt", "wise", "pipedrive", "veriff", "starship", "skeletontech", "glia",
           "monese", "salv", "montonio", "comodule", "paxful", "malwarebytes", "wolt",
           "swappie", "katanamrp", "fractory", "sportsbet", "nortal", "clevon"],
    "DE": ["personio", "celonis", "n26", "traderepublic", "gorillas", "flink", "sennder",
           "forto", "taxfix", "raisin", "tier", "flixbus", "zalando", "hellofresh",
           "deliveryhero", "getyourguide", "contentful", "staffbase", "mambu", "solarisbank",
           "wefox", "gitlab", "researchgate", "sumup", "grover", "kry", "urbansportsclub",
           "moia", "adjust", "signavio", "jimdo", "aboutyou", "commercetools", "mister-spex"],
    "SE": ["klarna", "spotify", "voiscooters", "einride", "northvolt", "tink", "truecaller",
           "mentimeter", "kahoot", "budbee", "epidemicsound", "kry", "instabee", "detectify",
           "funnel", "quinyx", "tobii", "sinch", "storytel", "anyfin"],
    "NL": ["adyen", "mollie", "backbase", "messagebird", "picnic", "bunq", "catawiki",
           "framer", "bitvavo", "channable", "coolblue", "takeaway", "elastic", "optiver",
           "databricks", "miro", "creativefabrica", "sendcloud", "trengo"],
    "FR": ["doctolib", "alan", "qonto", "payfit", "ledger", "backmarket", "blablacar",
           "contentsquare", "dataiku", "mirakl", "swile", "sorare", "spendesk", "algolia",
           "aircall", "younited", "shift-technology", "malt", "lydia", "pennylane"],
    "ES": ["glovo", "cabify", "jobandtalent", "factorialhr", "typeform", "travelperk",
           "redpoints", "holded", "paack", "seedtag", "jeff", "clarity", "cobee",
           "wallbox", "fintonic", "idealista", "flywire", "adevinta"],
    "UK": ["monzo", "revolut", "deliveroo", "gousto", "checkout", "gocardless", "tide",
           "curve", "marshmallow", "zego", "cleo", "primer", "form3", "thoughtmachine",
           "yapily", "truelayer", "moneybox", "starlingbank", "onfido", "improbable",
           "babylonhealth", "peakon", "bulb", "octopusenergy", "darktrace"],
    "PL": ["docplanner", "brainly", "booksy", "packhelp", "ramp", "callpage", "zowie",
           "infermedica", "vercel", "spotandtango"],
    "PT": ["unbabel", "talkdesk", "feedzai", "outsystems", "sword-health", "remote",
           "farfetch", "veniam"],
    "DK/NO/FI": ["pleo", "zendesk", "trustpilot", "templafy", "lunar", "cognite", "gelato",
                 "oda", "tibber", "kolonial", "supermetrics", "smartly", "aiven", "relex",
                 "reaktor", "wolt", "epicgames", "unity", "swappie"],
    "CH/AT": ["on-running", "scandit", "frontify", "nexthink", "sonarsource", "bitpanda",
              "gostudent", "refurbed", "tractive", "runtastic", "smartrecruiters"],
}


async def probe(client: httpx.AsyncClient, sem: asyncio.Semaphore,
                country: str, slug: str) -> list[dict]:
    hits = []
    for ats, tpl in ENDPOINTS.items():
        async with sem:
            try:
                r = await client.get(tpl.format(s=slug), timeout=12.0,
                                     follow_redirects=True)
            except Exception:
                continue
            if r.status_code != 200:
                continue
            n = count_jobs(ats, r)
            if n > 0:
                hits.append({"ats": ats, "slug": slug, "country": country, "jobs": n})
                print(f"  ✓ {ats:16} {slug:22} {n:4} вакансий", flush=True)
                break   # one company, one board - first hit wins
    return hits


async def main() -> None:
    sem = asyncio.Semaphore(12)
    async with httpx.AsyncClient(headers=UA) as client:
        tasks = [probe(client, sem, c, s)
                 for c, slugs in CANDIDATES.items() for s in slugs]
        results = await asyncio.gather(*tasks)
    hits = [h for group in results for h in group]
    hits.sort(key=lambda h: (h["ats"], h["slug"]))
    if "--dry-run" in sys.argv:
        print("\n--dry-run: реестр не тронут")
    else:
        seen, rows = set(), []
        for h in hits:
            key = (h["ats"], h["slug"])
            if key in seen:
                continue
            seen.add(key)
            rows.append({"ats": h["ats"], "slug": h["slug"], "region": h["country"]})
        out = {
            "_comment": (
                "European ATS boards — company career pages that never reach LinkedIn. "
                "Every slug here was verified with a live request; an unverified slug is "
                "a silently dead source. Regenerate with scripts/probe_boards.py."
            ),
            "_verified": datetime.date.today().isoformat(),
            "_counts": {"boards": len(rows)},
            "boards": rows,
        }
        path = pathlib.Path(__file__).resolve().parent.parent / "scrapers" / "registry" / "eu_boards.json"
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nреестр перезаписан: {path} ({len(rows)} бордов)")
    total_candidates = sum(len(v) for v in CANDIDATES.values())
    print(f"\nпроверено кандидатов: {total_candidates}, подтверждено: {len(hits)}, "
          f"вакансий суммарно: {sum(h['jobs'] for h in hits)}")
    from collections import Counter
    print("по ATS:", dict(Counter(h["ats"] for h in hits)))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
