"""Probe /search-posts — the remaining unidentified endpoint."""
import json
import pathlib

import requests

API_KEY = "ebe379537emsh25d4f1c35a4548ap102a82jsn3d058755d911"
HOST = "fresh-linkedin-profile-data.p.rapidapi.com"
OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "sample_responses"

url = f"https://{HOST}/search-posts"

payload = {
    "search_keywords": "",
    "sort_by": "Latest",
    "date_posted": "",
    "content_type": "",
    "from_member": ["ACoAAA8BYqEBCGLg_vT_ca6mMEqkpp9nVffJ3hc"],
    "from_company": [],
    "mentioning_member": [],
    "mentioning_company": [],
    "author_company": [],
    "author_industry": [],
    "author_keyword": "",
    "page": 1,
}

headers = {
    "x-rapidapi-host": HOST,
    "x-rapidapi-key": API_KEY,
    "Content-Type": "application/json",
}

resp = requests.post(url, json=payload, headers=headers, timeout=60)
print(f"status={resp.status_code}")
try:
    body = resp.json()
except ValueError:
    body = {"__text__": resp.text[:1500]}
print(json.dumps(body, indent=2)[:2000])

(OUT / "search_posts.json").write_text(
    json.dumps(
        {
            "__probe_meta__": {
                "slug": "search_posts",
                "method": "POST",
                "path": "/search-posts",
                "status_code": resp.status_code,
                "body": payload,
            },
            "response": body,
        },
        indent=2,
        default=str,
    )
)
print(f"Saved to {OUT / 'search_posts.json'}")
