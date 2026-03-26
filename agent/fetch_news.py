"""
M365 & IAM Intelligence Agent
Haalt dagelijks nieuws op uit RSS-feeds en laat Claude het analyseren.
Schrijft het resultaat naar public/data.json zodat het dashboard het kan tonen.
"""

import json
import os
import datetime
import feedparser
import anthropic

# ── Databronnen ────────────────────────────────────────────────────────────────

FEEDS = [
    {
        "name": "Microsoft Tech Community - Security",
        "url": "https://techcommunity.microsoft.com/plugins/custom/microsoft/o365/custom-blog-rss?tid=&board=MicrosoftSecurityandCompliance&size=10",
        "category": "security",
    },
    {
        "name": "Microsoft Entra Blog",
        "url": "https://techcommunity.microsoft.com/plugins/custom/microsoft/o365/custom-blog-rss?tid=&board=Identity&size=10",
        "category": "identity",
    },
    {
        "name": "Microsoft 365 Tech Community",
        "url": "https://techcommunity.microsoft.com/plugins/custom/microsoft/o365/custom-blog-rss?tid=&board=Microsoft365General&size=10",
        "category": "m365",
    },
    {
        "name": "Microsoft Security Blog",
        "url": "https://www.microsoft.com/en-us/security/blog/feed/",
        "category": "security",
    },
]

# ── RSS ophalen ────────────────────────────────────────────────────────────────

def fetch_articles(max_per_feed: int = 5) -> list[dict]:
    """Haal recente artikelen op uit alle feeds."""
    articles = []
    for feed_info in FEEDS:
        try:
            parsed = feedparser.parse(feed_info["url"])
            for entry in parsed.entries[:max_per_feed]:
                articles.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", entry.get("description", ""))[:600],
                    "link": entry.get("link", ""),
                    "source": feed_info["name"],
                    "category": feed_info["category"],
                    "published": entry.get("published", ""),
                })
        except Exception as e:
            print(f"Feed fout ({feed_info['name']}): {e}")
    return articles


# ── Claude analyse ─────────────────────────────────────────────────────────────

def analyse_with_claude(articles: list[dict]) -> list[dict]:
    """Laat Claude elk artikel beoordelen op relevantie en impact."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    articles_text = "\n\n".join([
        f"[{i+1}] TITEL: {a['title']}\nBRON: {a['source']}\nCATEGORIE: {a['category']}\nSAMENVATTING: {a['summary']}"
        for i, a in enumerate(articles)
    ])

    prompt = f"""Je bent een Microsoft 365 en Identity & Access Management specialist.
Analyseer de volgende nieuwsartikelen en geef per artikel een beoordeling.

ARTIKELEN:
{articles_text}

Geef je antwoord ALLEEN als een JSON-array (geen markdown, geen uitleg erbuiten), met per artikel:
- index: het nummer van het artikel (integer)
- relevant: true of false (is het relevant voor M365/IAM beheerders?)
- impact: "high", "medium", of "low"
- impact_nl: korte Nederlandse uitleg van de impact (max 15 woorden)
- samenvatting: Nederlandse samenvatting in 2-3 zinnen, geschreven voor een IT-beheerder
- actie_vereist: true of false

Voorbeeld:
[
  {{
    "index": 1,
    "relevant": true,
    "impact": "high",
    "impact_nl": "Directe actie vereist voor alle Exchange Online tenants",
    "samenvatting": "Microsoft heeft een kritieke patch uitgebracht...",
    "actie_vereist": true
  }}
]"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Verwijder eventuele markdown code-fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# ── Alles samenvoegen ──────────────────────────────────────────────────────────

def build_data(articles: list[dict], analyses: list[dict]) -> dict:
    """Combineer artikelen met Claude's analyse tot dashboard-data."""
    analysis_map = {a["index"]: a for a in analyses}
    today = datetime.date.today().isoformat()

    items = []
    for i, article in enumerate(articles):
        analysis = analysis_map.get(i + 1, {})
        if not analysis.get("relevant", True):
            continue
        items.append({
            "id": i + 1,
            "title": article["title"],
            "summary": analysis.get("samenvatting", article["summary"]),
            "impact_label": analysis.get("impact_nl", ""),
            "impact": analysis.get("impact", "medium"),
            "category": article["category"],
            "source": article["source"],
            "link": article["link"],
            "published": article["published"],
            "actie_vereist": analysis.get("actie_vereist", False),
            "date": today,
        })

    # Sorteren: high impact eerst, dan actie_vereist
    items.sort(key=lambda x: (
        {"high": 0, "medium": 1, "low": 2}.get(x["impact"], 1),
        not x["actie_vereist"]
    ))

    stats = {
        "total": len(items),
        "high_impact": sum(1 for x in items if x["impact"] == "high"),
        "actie_vereist": sum(1 for x in items if x["actie_vereist"]),
        "identity_count": sum(1 for x in items if x["category"] == "identity"),
        "last_updated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "date": today,
    }

    return {"stats": stats, "items": items}


# ── Hoofdprogramma ─────────────────────────────────────────────────────────────

def main():
    print("Agent gestart...")

    print("1/3 Nieuws ophalen...")
    articles = fetch_articles(max_per_feed=5)
    print(f"   {len(articles)} artikelen gevonden")

    if not articles:
        print("Geen artikelen gevonden, agent stopt.")
        return

    print("2/3 Claude analyseert artikelen...")
    analyses = analyse_with_claude(articles)
    print(f"   {len(analyses)} analyses ontvangen")

    print("3/3 Data opslaan...")
    data = build_data(articles, analyses)

    output_path = os.path.join(os.path.dirname(__file__), "..", "public", "data.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Klaar! {data['stats']['total']} relevante items opgeslagen.")
    print(f"Waarvan {data['stats']['high_impact']} met hoge impact.")


if __name__ == "__main__":
    main()
