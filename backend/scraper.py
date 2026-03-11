"""
Slovakia Monitor — Backend Scraper
====================================
Zbiera dáta z verejných API a RSS zdrojov:
  - Štatistický úrad SR (data.statistics.sk/api/v2)
  - NBS RSS feed
  - IFP MF SR RSS
  - NMS Market Research RSS
  - Eurostat SDMX API
  - politpro.eu scraper

Výstup: docs/data/latest.json  (číta frontend)
Spúšťa sa automaticky cez GitHub Actions každé 4 hodiny.
"""

import requests
import feedparser
import json
import os
import time
import logging
from datetime import datetime, timezone
from bs4 import BeautifulSoup

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/scraper.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "SlovakiaNow/1.0 (transparentny verejny dashboard)"
}

# ── Zdroje ────────────────────────────────────────────────────────────────────
RSS_SOURCES = {
    "nbs":       "https://nbs.sk/sk/rss",
    "mfsr":      "https://www.mfsr.sk/sk/rss/spravy.rss",
    "sme":       "https://sme.sk/rss/ekonomika",
    "pravda":    "https://ekonomika.pravda.sk/rss/",
    "stvr":      "https://spravy.stvr.sk/feed/",
    "dennikn":   "https://dennikn.sk/feed/",
    "startitup": "https://startitup.sk/feed/",
}

# ── Pomocné funkcie ───────────────────────────────────────────────────────────
def safe_get(url, timeout=15):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            log.warning(f"[Pokus {attempt+1}/3] {url} → {e}")
            time.sleep(2 ** attempt)
    log.error(f"❌ Nepodarilo sa: {url}")
    return None


def parse_jsonstat(data):
    """Parsuje JSON-stat formát z data.statistics.sk"""
    try:
        dims = data.get("dimension", {})
        values = data.get("value", [])
        dim_ids = data.get("id", [])
        if len(dim_ids) < 2:
            return []
        dim0_cats = list(dims[dim_ids[0]]["category"]["label"].items())
        dim1_cats = list(dims[dim_ids[1]]["category"]["label"].items())
        result = []
        idx = 0
        for r_code, r_label in dim0_cats:
            for p_code, p_label in dim1_cats:
                val = values[idx] if idx < len(values) else None
                result.append({"rok": r_label, "perioda": p_label, "hodnota": val})
                idx += 1
        return result
    except Exception as e:
        log.error(f"parse_jsonstat chyba: {e}")
        return []


def fetch_rss(url, max_items=5):
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:max_items]:
            items.append({
                "title":     entry.get("title", ""),
                "link":      entry.get("link", ""),
                "published": entry.get("published", ""),
                "summary":   BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()[:300],
                "source":    feed.feed.get("title", url),
            })
        return items
    except Exception as e:
        log.error(f"RSS chyba {url}: {e}")
        return []


def scrape_nms_polls():
    # Skúsime viacero URL variantov
    r = None
    for url in [
        "https://nms.global/sk/category/volebny-model/",
        "https://nms.global/category/volebny-model/",
        "https://nms.global/sk/",
    ]:
        r = safe_get(url)
        if r and r.status_code == 200:
            break
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    articles = soup.select("article")[:4]
    results = []
    for art in articles:
        title_el = art.select_one("h2 a, h3 a")
        if not title_el:
            continue
        date_el = art.select_one("time")
        results.append({
            "title":  title_el.get_text(strip=True),
            "link":   title_el["href"],
            "date":   date_el["datetime"] if date_el and date_el.has_attr("datetime") else "",
            "source": "NMS Market Research"
        })
    log.info(f"NMS: {len(results)} prieskumov")
    return results


def scrape_politpro():
    url = "https://politpro.eu/cs/slovensko/volebni-pruzkumy"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    links = soup.select("a[href*='parlamentni-volby']")[:6]
    polls = []
    for lnk in links:
        href = lnk["href"]
        polls.append({
            "title":  lnk.get_text(strip=True),
            "link":   "https://politpro.eu" + href if href.startswith("/") else href,
            "source": "PolitPro / AKO"
        })
    log.info(f"PolitPro: {len(polls)} odkazov")
    return polls


# ── Hlavná funkcia ────────────────────────────────────────────────────────────
def run_scraper():
    log.info("=" * 55)
    log.info("🚀 SlovakiaNow Scraper — štart")
    log.info("=" * 55)

    output = {
        "meta": {
            "aktualizovane": datetime.now(timezone.utc).isoformat(),
            "verzia": "1.0.0",
            "zdroje": []
        },
        "ekonomika": {},
        "prieskumy": {},
        "spravy": [],
        "energie": {},
        "errors": []
    }

    # ── ECB API — Inflácia HICP Slovensko ─────────────────────────────────────
    # ECB Data Portal: séria ICP.M.SK.N.000000.4.ANR (HICP YoY %)
    log.info("📊 ECB API — Inflácia HICP Slovensko...")
    r = safe_get("https://data-api.ecb.europa.eu/service/data/ICP/M.SK.N.000000.4.ANR?format=jsondata&lastNObservations=24")
    if r:
        try:
            d = r.json()
            series = d["dataSets"][0]["series"]["0:0:0:0:0:0"]["observations"]
            time_labels = list(d["structure"]["dimensions"]["observation"][0]["values"])
            points = []
            for i, tl in enumerate(time_labels):
                val = series.get(str(i), [None])[0]
                if val is not None:
                    points.append({"perioda": tl["id"], "hodnota": round(val, 2)})
            output["ekonomika"]["inflacia_mesacna"] = points
            output["ekonomika"]["hicp_eurostat"] = points  # rovnaké dáta pre oba grafy
            output["meta"]["zdroje"].append({"nazov": "ECB — HICP Inflácia SK", "url": "https://data-api.ecb.europa.eu", "format": "JSON"})
            log.info(f"  ✅ Inflácia ECB: {len(points)} bodov")
        except Exception as e:
            output["errors"].append(f"ECB inflácia: {e}")
            log.error(f"  ❌ ECB parsovanie: {e}")
            output["ekonomika"]["inflacia_mesacna"] = []
            output["ekonomika"]["hicp_eurostat"] = []
    else:
        output["ekonomika"]["inflacia_mesacna"] = []
        output["ekonomika"]["hicp_eurostat"] = []
        output["errors"].append("ECB inflácia: nedostupné")

    # ── Eurostat — HDP rast Slovensko ─────────────────────────────────────────
    # Správna URL pre Eurostat SDMX 2.1 REST API
    log.info("📊 Eurostat — HDP rast SK...")
    r = safe_get("https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/namq_10_gdp?geo=SK&unit=PCH_PRE_PER&na_item=B1GQ&freq=Q&format=JSON&lastTimePeriod=20")
    if r:
        try:
            d = r.json()
            time_dim = d.get("dimension", {}).get("time", {})
            time_labels = list(time_dim.get("category", {}).get("label", {}).values())
            values_raw = list(d.get("value", {}).values()) if isinstance(d.get("value"), dict) else d.get("value", [])
            points = [{"perioda": t, "hodnota": round(v, 2)} for t, v in zip(time_labels, values_raw) if v is not None]
            output["ekonomika"]["hdp_stvrtrocne"] = points[-20:]
            output["meta"]["zdroje"].append({"nazov": "Eurostat — HDP rast SK", "url": "https://ec.europa.eu/eurostat", "format": "SDMX JSON"})
            log.info(f"  ✅ HDP Eurostat: {len(points)} bodov")
        except Exception as e:
            output["errors"].append(f"Eurostat HDP: {e}")
            log.error(f"  ❌ Eurostat HDP: {e}")
            output["ekonomika"]["hdp_stvrtrocne"] = []
    else:
        output["ekonomika"]["hdp_stvrtrocne"] = []
        output["errors"].append("Eurostat HDP: nedostupné")

    # ── Eurostat — Ceny elektriny (opravená URL) ──────────────────────────────
    log.info("⚡ Eurostat — Ceny elektriny...")
    # Správny parameter: siec=KWH2500-4999 (nie consom)
    r = safe_get("https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/nrg_pc_204?geo=SK&unit=KWH&currency=EUR&tax=I_TAX&siec=KWH2500-4999&freq=S&format=JSON&lastTimePeriod=10")
    if r:
        try:
            d = r.json()
            time_dim = d.get("dimension", {}).get("time", {})
            time_labels = list(time_dim.get("category", {}).get("label", {}).values())
            values_raw = list(d.get("value", {}).values()) if isinstance(d.get("value"), dict) else d.get("value", [])
            points = [{"perioda": t, "hodnota": round(v * 100, 2)} for t, v in zip(time_labels, values_raw) if v]
            output["energie"]["elektrina_centkwh"] = points
            log.info(f"  ✅ Elektrina: {len(points)} bodov")
        except Exception as e:
            output["errors"].append(f"Eurostat energia: {e}")
            output["energie"]["elektrina_centkwh"] = []
    else:
        output["energie"]["elektrina_centkwh"] = []
        output["errors"].append("Eurostat energia: nedostupné")

    # ── RSS Feedy ─────────────────────────────────────────────────────────────
    all_news = []
    for key, url in RSS_SOURCES.items():
        log.info(f"📰 RSS: {key}...")
        items = fetch_rss(url, max_items=4)
        for item in items:
            item["kategoria"] = key
        all_news.extend(items)
        if items:
            output["meta"]["zdroje"].append({"nazov": items[0].get("source", key), "url": url, "format": "RSS"})
    output["spravy"] = all_news
    log.info(f"  ✅ Správy celkom: {len(all_news)}")

    # ── NMS Prieskumy ─────────────────────────────────────────────────────────
    log.info("🗳️  NMS Market Research...")
    output["prieskumy"]["nms"] = scrape_nms_polls()

    # ── PolitPro / AKO ────────────────────────────────────────────────────────
    log.info("🗳️  PolitPro / AKO...")
    output["prieskumy"]["politpro"] = scrape_politpro()

    # ── Uložiť výstup ─────────────────────────────────────────────────────────
    os.makedirs("docs/data", exist_ok=True)
    with open("docs/data/latest.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info("=" * 55)
    log.info(f"✅ Hotovo! Chyby: {len(output['errors'])}")
    if output["errors"]:
        for e in output["errors"]:
            log.warning(f"  ⚠️  {e}")
    log.info("Uložené: docs/data/latest.json")
    log.info("=" * 55)
    return output


if __name__ == "__main__":
    run_scraper()
