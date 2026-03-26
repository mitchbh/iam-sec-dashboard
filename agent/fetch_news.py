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


# ── Geschiedenis laden ─────────────────────────────────────────────────────────

def load_existing(output_path: str) -> dict:
    """Laad bestaande data.json als die al bestaat, anders geef lege structuur terug."""
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"stats": {}, "history": {}, "items": []}


# ── Alles samenvoegen ──────────────────────────────────────────────────────────

def build_data(articles: list[dict], analyses: list[dict], existing: dict) -> dict:
    """Combineer nieuwe artikelen met bestaande geschiedenis."""
    analysis_map = {a["index"]: a for a in analyses}
    today = datetime.date.today().isoformat()

    # Nieuwe items van vandaag bouwen
    new_items = []
    for i, article in enumerate(articles):
        analysis = analysis_map.get(i + 1, {})
        if not analysis.get("relevant", True):
            continue
        new_items.append({
            "id": f"{today}-{i+1}",
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
    new_items.sort(key=lambda x: (
        {"high": 0, "medium": 1, "low": 2}.get(x["impact"], 1),
        not x["actie_vereist"]
    ))

    # Geschiedenis bijwerken: sla items op per datum
    history = existing.get("history", {})
    history[today] = new_items

    # Maximaal 90 dagen bewaren zodat het bestand niet eindeloos groeit
    if len(history) > 90:
        oldest = sorted(history.keys())[0]
        del history[oldest]
        print(f"   Oudste dag ({oldest}) verwijderd uit archief (max 90 dagen)")

    # Stats alleen over vandaag
    stats = {
        "total": len(new_items),
        "high_impact": sum(1 for x in new_items if x["impact"] == "high"),
        "actie_vereist": sum(1 for x in new_items if x["actie_vereist"]),
        "identity_count": sum(1 for x in new_items if x["category"] == "identity"),
        "last_updated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "date": today,
        "days_archived": len(history),
    }

    # items = vandaag (voor het dashboard), history = volledig archief
    return {
        "stats": stats,
        "items": new_items,
        "history": history,
    }


# ── Hoofdprogramma ─────────────────────────────────────────────────────────────

def main():
    print("Agent gestart...")

    output_path = os.path.join(os.path.dirname(__file__), "..", "public", "data.json")

    print("1/4 Bestaande data laden...")
    existing = load_existing(output_path)
    print(f"   {len(existing.get('history', {}))} dag(en) in archief")

    print("2/4 Nieuws ophalen...")
    articles = fetch_articles(max_per_feed=5)
    print(f"   {len(articles)} artikelen gevonden")

    if not articles:
        print("Geen artikelen gevonden, agent stopt.")
        return

    print("3/4 Claude analyseert artikelen...")
    analyses = analyse_with_claude(articles)
    print(f"   {len(analyses)} analyses ontvangen")

    print("4/4 Data opslaan met geschiedenis...")
    data = build_data(articles, analyses, existing)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Klaar! {data['stats']['total']} nieuwe items opgeslagen.")
    print(f"Archief bevat nu {data['stats']['days_archived']} dag(en).")


if __name__ == "__main__":
    main()
