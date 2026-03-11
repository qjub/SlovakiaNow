"""
Slovakia Monitor — Backend Scraper v2.0
========================================
Opravené a rozšírené dátové zdroje:

EKONOMIKA:
  - ECB SDMX API  → Inflácia HICP SK (mesačne)
  - Eurostat SDMX → HDP rast SK % (štvrťročne, medziročne)
  - Eurostat SDMX → Nezamestnanosť SK % (mesačne)
  - Eurostat SDMX → Priemerná mzda SK € (štvrťročne)
  - Eurostat SDMX → Vládny dlh % HDP (ročne)
  - Eurostat SDMX → Vládny deficit % HDP (ročne)
  - Eurostat SDMX → Inflácia podľa kategórií (potraviny, energia)

ENERGIE:
  - Eurostat SDMX → Ceny elektriny pre domácnosti (ct/kWh, polročne)
  - Eurostat SDMX → Ceny plynu pre domácnosti (ct/kWh, polročne)
  - GlobalPetrolPrices → Benzín 95 a nafta SR (týždenné)

ÚROKOVÉ SADZBY:
  - ECB SDMX → Kľúčová úroková sadzba ECB (depozitná)
  - ECB SDMX → EURIBOR 3M (relevantné pre hypotéky)

POLITIKA:
  - NMS Market Research scraper → Volebné prieskumy
  - PolitPro / AKO scraper → Prieskumy

SPRÁVY (RSS):
  - NBS, MF SR, STVR, SME, Pravda, DenníkN, Startitup

Výstup: docs/data/latest.json
Spúšťa sa každé 4 hodiny cez GitHub Actions.
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
    "User-Agent": "SlovakiaNow/2.0 (transparentny verejny dashboard; github.com/qjub/SlovakiaNow)"
}

# ── Pomocné funkcie ───────────────────────────────────────────────────────────

def safe_get(url, timeout=20, retries=3):
    """HTTP GET s retry logikou."""
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError as e:
            log.warning(f"[{attempt+1}/{retries}] HTTP {e.response.status_code}: {url}")
            if e.response.status_code in (404, 400):
                return None  # netreba retrynúť
            time.sleep(2 ** attempt)
        except Exception as e:
            log.warning(f"[{attempt+1}/{retries}] {url} → {e}")
            time.sleep(2 ** attempt)
    log.error(f"❌ Nepodarilo sa: {url}")
    return None


def parse_eurostat_timeseries(data):
    """
    Parsuje Eurostat SDMX JSON formát.
    Vracia list {perioda, hodnota} zoradený chronologicky.
    """
    try:
        dims = data.get("dimension", {})
        values_raw = data.get("value", [])

        # Hodnoty môžu byť dict (sparse) alebo list
        if isinstance(values_raw, dict):
            values = {int(k): v for k, v in values_raw.items() if v is not None}
        else:
            values = {i: v for i, v in enumerate(values_raw) if v is not None}

        # Nájdeme time dimenziu
        time_key = None
        for k in dims:
            if "time" in k.lower() or k == list(dims.keys())[-1]:
                time_key = k
                break

        if not time_key:
            log.warning("parse_eurostat_timeseries: nenašla sa time dimenzia")
            return []

        time_labels = list(dims[time_key]["category"]["label"].values())

        result = []
        for i, label in enumerate(time_labels):
            if i in values:
                result.append({"perioda": label, "hodnota": round(values[i], 3)})

        return result
    except Exception as e:
        log.error(f"parse_eurostat_timeseries chyba: {e}")
        return []


def parse_ecb_timeseries(data):
    """
    Parsuje ECB SDMX JSON formát (iná štruktúra ako Eurostat).
    Vracia list {perioda, hodnota}.
    """
    try:
        series_data = data["dataSets"][0]["series"]["0:0:0:0:0:0"]["observations"]
        time_labels = data["structure"]["dimensions"]["observation"][0]["values"]

        result = []
        for i, tl in enumerate(time_labels):
            val = series_data.get(str(i), [None])[0]
            if val is not None:
                result.append({"perioda": tl["id"], "hodnota": round(val, 3)})
        return result
    except Exception as e:
        log.error(f"parse_ecb_timeseries chyba: {e}")
        return []


def fetch_rss(url, max_items=5):
    """Stiahne RSS feed a vráti zoznam článkov."""
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:max_items]:
            items.append({
                "title":     entry.get("title", ""),
                "link":      entry.get("link", ""),
                "published": entry.get("published", ""),
                "summary":   BeautifulSoup(
                    entry.get("summary", ""), "html.parser"
                ).get_text()[:300],
                "source":    feed.feed.get("title", url),
            })
        return items
    except Exception as e:
        log.error(f"RSS chyba {url}: {e}")
        return []


# ── Ekonomika — Eurostat HDP ──────────────────────────────────────────────────

def fetch_hdp():
    """
    HDP rast Slovenska % medziročne (year-on-year).
    Eurostat: namq_10_gdp, unit=PCH_PRE_PER = percentage change vs. same quarter previous year
    Toto je správny ukazovateľ: napr. +1.5% = ekonomika rástla o 1.5% oproti rovnakému štvrťroku minulého roka.
    """
    log.info("📊 Eurostat — HDP rast SK (% medziročne)...")
    url = (
        "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/namq_10_gdp"
        "?geo=SK&unit=PCH_PRE_PER&na_item=B1GQ&freq=Q&format=JSON&lastTimePeriod=20"
    )
    r = safe_get(url)
    if not r:
        # Záložná URL — iný endpoint
        url2 = (
            "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/namq_10_gdp"
            "?geo=SK&unit=PCH_PRE_PER&na_item=B1GQ&freq=Q&lang=SK"
        )
        r = safe_get(url2)
    if not r:
        log.error("❌ HDP: oba endpointy zlyhali")
        return []

    try:
        d = r.json()
        points = parse_eurostat_timeseries(d)
        # Filter: len reálne percentuálne hodnoty (-20% až +20%)
        points = [p for p in points if -20 <= p["hodnota"] <= 20]
        log.info(f"  ✅ HDP: {len(points)} bodov, posledný: {points[-1] if points else 'N/A'}")
        return points
    except Exception as e:
        log.error(f"  ❌ HDP parsovanie: {e}")
        return []


def fetch_nezamestnanost():
    """
    Nezamestnanosť SK % mesačne (sezónne očistená).
    Eurostat: une_rt_m, SA=seasonally adjusted, AGE=TOTAL, SEX=T
    """
    log.info("📊 Eurostat — Nezamestnanosť SK...")
    url = (
        "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/une_rt_m"
        "?geo=SK&s_adj=SA&age=TOTAL&sex=T&unit=PC_ACT&format=JSON&lastTimePeriod=24"
    )
    r = safe_get(url)
    if not r:
        return []
    try:
        d = r.json()
        points = parse_eurostat_timeseries(d)
        # Filter: reálne hodnoty nezamestnanosti (1-30%)
        points = [p for p in points if 1 <= p["hodnota"] <= 30]
        log.info(f"  ✅ Nezamestnanosť: {len(points)} bodov, posledná: {points[-1] if points else 'N/A'}")
        return points
    except Exception as e:
        log.error(f"  ❌ Nezamestnanosť: {e}")
        return []


def fetch_mzda():
    """
    Priemerná hodinová mzda SK (Index).
    SÚSR DATAcube API — priemerná mesačná mzda štvrťročne.
    Kód: pr3003qs — Priemerná mesačná mzda zamestnanca
    """
    log.info("📊 SÚSR — Priemerná mzda SK...")

    # Skúsime SÚSR DATAcube API
    url = "https://data.statistics.sk/api/v2/dataset/pr3003qs/all/all?lang=sk&type=json"
    r = safe_get(url)
    if r:
        try:
            d = r.json()
            from collections import OrderedDict
            dims = d.get("dimension", {})
            values = d.get("value", [])
            dim_ids = d.get("id", [])

            # Nájdi time a value dimenzie
            result = []
            if len(dim_ids) >= 2:
                time_cats = list(dims[dim_ids[0]]["category"]["label"].items())
                value_cats = list(dims[dim_ids[1]]["category"]["label"].items()) if len(dim_ids) > 1 else [("T", "Spolu")]

                # Berieme len "Spolu" (celková ekonomika)
                spolu_idx = 0
                for i, (code, label) in enumerate(value_cats):
                    if "spolu" in label.lower() or "total" in label.lower() or code in ("T", "TOTAL", "_T"):
                        spolu_idx = i
                        break

                n_time = len(time_cats)
                n_val = len(value_cats)
                idx = spolu_idx  # prvý riadok pre "Spolu"
                for t_idx, (t_code, t_label) in enumerate(time_cats):
                    pos = t_idx * n_val + spolu_idx
                    v = values[pos] if pos < len(values) else None
                    if v is not None and isinstance(v, (int, float)) and 500 <= v <= 5000:
                        result.append({"perioda": t_label, "hodnota": round(v, 0)})

            if result:
                log.info(f"  ✅ Mzda SÚSR: {len(result)} bodov, posledná: {result[-1]}")
                return result[-20:]
        except Exception as e:
            log.warning(f"  ⚠️  SÚSR mzda parsovanie: {e}")

    # Fallback: Eurostat earnings
    log.info("  → Fallback: Eurostat mzdy...")
    url2 = (
        "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/earn_eses_agt"
        "?geo=SK&nace_r2=B-S&siz_emp=TOTAL&format=JSON&lastTimePeriod=10"
    )
    r2 = safe_get(url2)
    if r2:
        try:
            d = r2.json()
            points = parse_eurostat_timeseries(d)
            points = [p for p in points if 500 <= p["hodnota"] <= 5000]
            if points:
                log.info(f"  ✅ Mzda Eurostat: {len(points)} bodov")
                return points
        except Exception as e:
            log.warning(f"  ⚠️  Eurostat mzdy: {e}")

    log.error("  ❌ Mzda: všetky zdroje zlyhali")
    return []


def fetch_vladny_dlh():
    """
    Vládny dlh SR % HDP (ročne) a deficit.
    Eurostat: gov_10dd_edpt1
    """
    log.info("📊 Eurostat — Vládny dlh a deficit SK...")
    results = {"dlh": [], "deficit": []}

    # Dlh (General government consolidated gross debt)
    url_dlh = (
        "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/gov_10dd_edpt1"
        "?geo=SK&unit=PC_GDP&na_item=GD&sector=S13&freq=A&format=JSON&lastTimePeriod=12"
    )
    r = safe_get(url_dlh)
    if r:
        try:
            d = r.json()
            points = parse_eurostat_timeseries(d)
            points = [p for p in points if 0 <= p["hodnota"] <= 200]
            results["dlh"] = points
            log.info(f"  ✅ Dlh: {len(points)} bodov, posledný: {points[-1] if points else 'N/A'}")
        except Exception as e:
            log.error(f"  ❌ Dlh: {e}")

    # Deficit (Net lending/borrowing)
    url_def = (
        "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/gov_10dd_edpt1"
        "?geo=SK&unit=PC_GDP&na_item=B9&sector=S13&freq=A&format=JSON&lastTimePeriod=12"
    )
    r2 = safe_get(url_def)
    if r2:
        try:
            d = r2.json()
            points = parse_eurostat_timeseries(d)
            points = [p for p in points if -20 <= p["hodnota"] <= 10]
            results["deficit"] = points
            log.info(f"  ✅ Deficit: {len(points)} bodov, posledný: {points[-1] if points else 'N/A'}")
        except Exception as e:
            log.error(f"  ❌ Deficit: {e}")

    return results


def fetch_inflacia_hicp():
    """
    Inflácia HICP SK — mesačne, medziročne (%).
    ECB Data Portal API.
    """
    log.info("📊 ECB — Inflácia HICP SK...")
    url = (
        "https://data-api.ecb.europa.eu/service/data/ICP/M.SK.N.000000.4.ANR"
        "?format=jsondata&lastNObservations=36"
    )
    r = safe_get(url)
    if not r:
        return []
    try:
        d = r.json()
        points = parse_ecb_timeseries(d)
        log.info(f"  ✅ HICP: {len(points)} bodov, posledný: {points[-1] if points else 'N/A'}")
        return points
    except Exception as e:
        log.error(f"  ❌ HICP: {e}")
        return []


def fetch_inflacia_kategorie():
    """
    Inflácia HICP SK podľa kategórií — potraviny, energia, celková.
    ECB API — rôzne COICOP kódy.
    """
    log.info("📊 ECB — Inflácia podľa kategórií...")
    kategorie = {
        "potraviny":  "ICP/M.SK.N.01+02......4.ANR",  # Food and beverages
        "energia":    "ICP/M.SK.N.045.....4.ANR",       # Energy
        "byvanie":    "ICP/M.SK.N.04......4.ANR",       # Housing
    }
    result = {}
    for nazov, series in kategorie.items():
        url = f"https://data-api.ecb.europa.eu/service/data/{series}?format=jsondata&lastNObservations=24"
        r = safe_get(url)
        if r:
            try:
                d = r.json()
                points = parse_ecb_timeseries(d)
                if points:
                    result[nazov] = points
                    log.info(f"  ✅ Inflácia {nazov}: {points[-1]}")
                else:
                    log.warning(f"  ⚠️  Inflácia {nazov}: žiadne body")
            except Exception as e:
                log.warning(f"  ⚠️  Inflácia {nazov}: {e}")
    return result


# ── Energie ───────────────────────────────────────────────────────────────────

def fetch_elektrina():
    """
    Ceny elektriny pre domácnosti SK — ct/kWh s DPH (polročne).
    Eurostat: nrg_pc_204
    Správny filter: siec=KWH2500-4999 (domácnosti 2500-4999 kWh/rok)
    """
    log.info("⚡ Eurostat — Ceny elektriny SK...")
    url = (
        "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/nrg_pc_204"
        "?geo=SK&unit=KWH&currency=EUR&tax=I_TAX&siec=KWH2500-4999&freq=S"
        "&format=JSON&lastTimePeriod=12"
    )
    r = safe_get(url)
    if not r:
        # Záloha — iný tier
        url2 = (
            "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/nrg_pc_204"
            "?geo=SK&unit=KWH&currency=EUR&tax=I_TAX&siec=KWH1000-2499&freq=S"
            "&format=JSON&lastTimePeriod=12"
        )
        r = safe_get(url2)
    if not r:
        return []
    try:
        d = r.json()
        points = parse_eurostat_timeseries(d)
        # Konverzia: Eurostat dáva €/kWh, my chceme ct/kWh
        points_ct = []
        for p in points:
            v = p["hodnota"]
            # Ak je hodnota < 1, je v €/kWh → konvertovať na ct
            if v < 1:
                v = round(v * 100, 2)
            points_ct.append({"perioda": p["perioda"], "hodnota": v})
        # Reálne hodnoty: 10-50 ct/kWh
        points_ct = [p for p in points_ct if 5 <= p["hodnota"] <= 100]
        log.info(f"  ✅ Elektrina: {len(points_ct)} bodov, posledná: {points_ct[-1] if points_ct else 'N/A'}")
        return points_ct
    except Exception as e:
        log.error(f"  ❌ Elektrina: {e}")
        return []


def fetch_plyn():
    """
    Ceny plynu pre domácnosti SK — ct/kWh s DPH (polročne).
    Eurostat: nrg_pc_202
    """
    log.info("⚡ Eurostat — Ceny plynu SK...")
    url = (
        "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/nrg_pc_202"
        "?geo=SK&unit=KWH&currency=EUR&tax=I_TAX&freq=S&format=JSON&lastTimePeriod=12"
    )
    r = safe_get(url)
    if not r:
        return []
    try:
        d = r.json()
        points = parse_eurostat_timeseries(d)
        points_ct = []
        for p in points:
            v = p["hodnota"]
            if v < 1:
                v = round(v * 100, 2)
            points_ct.append({"perioda": p["perioda"], "hodnota": v})
        points_ct = [p for p in points_ct if 2 <= p["hodnota"] <= 50]
        log.info(f"  ✅ Plyn: {len(points_ct)} bodov, posledný: {points_ct[-1] if points_ct else 'N/A'}")
        return points_ct
    except Exception as e:
        log.error(f"  ❌ Plyn: {e}")
        return []


def fetch_phm():
    """
    Ceny pohonných hmôt SR — benzín 95 a nafta (€/liter).
    Zdroj: GlobalPetrolPrices.com — má štruktúrované dáta pre SK.
    Scraper číta tabuľku cien.
    """
    log.info("⛽ GlobalPetrolPrices — PHM SK...")
    result = {"benzin": [], "nafta": []}

    sources = [
        ("benzin", "https://www.globalpetrolprices.com/Slovakia/gasoline_prices/"),
        ("nafta",  "https://www.globalpetrolprices.com/Slovakia/diesel_prices/"),
    ]

    for typ, url in sources:
        r = safe_get(url, timeout=20)
        if not r:
            continue
        try:
            soup = BeautifulSoup(r.text, "html.parser")

            # Hľadáme tabuľku s historickými cenami
            table = soup.find("table", {"id": "tf"}) or soup.find("table", class_="graph_table")
            if not table:
                # Skúsime nájsť cenu priamo zo stránky
                price_el = soup.find("span", {"class": "price_number"}) or \
                           soup.find("h2", {"class": "price"})
                if price_el:
                    try:
                        price = float(price_el.get_text().strip().replace(",", "."))
                        if 0.5 <= price <= 3.0:
                            from datetime import date
                            result[typ] = [{"perioda": date.today().strftime("%Y-%m-%d"), "hodnota": price}]
                            log.info(f"  ✅ PHM {typ} (aktuálna cena): {price}")
                    except:
                        pass
                continue

            rows = table.find_all("tr")[1:]  # skip header
            prices = []
            for row in rows[:24]:
                cols = row.find_all("td")
                if len(cols) >= 2:
                    try:
                        date_str = cols[0].get_text(strip=True)
                        price_str = cols[1].get_text(strip=True).replace(",", ".")
                        price = float(price_str)
                        if 0.5 <= price <= 3.0:
                            prices.append({"perioda": date_str, "hodnota": price})
                    except:
                        continue

            if prices:
                result[typ] = list(reversed(prices))  # chronologicky
                log.info(f"  ✅ PHM {typ}: {len(prices)} bodov, posledný: {prices[0]}")
            else:
                log.warning(f"  ⚠️  PHM {typ}: žiadne dáta v tabuľke")

        except Exception as e:
            log.error(f"  ❌ PHM {typ}: {e}")

    return result


# ── Úrokové sadzby ────────────────────────────────────────────────────────────

def fetch_urokove_sadzby():
    """
    ECB kľúčové úrokové sadzby a EURIBOR 3M.
    Relevantné pre ľudí s hypotékami a sporiacimi účtami.
    """
    log.info("💶 ECB — Úrokové sadzby...")
    result = {}

    # ECB depozitná sadzba (hlavná referenčná sadzba od mar 2024)
    url_ecb = (
        "https://data-api.ecb.europa.eu/service/data/FM/B.U2.EUR.4F.KR.DFR.LEV"
        "?format=jsondata&lastNObservations=20"
    )
    r = safe_get(url_ecb)
    if r:
        try:
            d = r.json()
            points = parse_ecb_timeseries(d)
            points = [p for p in points if -2 <= p["hodnota"] <= 10]
            result["ecb_sadzba"] = points
            log.info(f"  ✅ ECB sadzba: {points[-1] if points else 'N/A'}")
        except Exception as e:
            log.warning(f"  ⚠️  ECB sadzba: {e}")

    # EURIBOR 3M (dôležité pre hypotéky s variabilnou sadzbou)
    url_euribor = (
        "https://data-api.ecb.europa.eu/service/data/FM/M.U2.EUR.RT.MM.EURIBOR3MD_.HSTA"
        "?format=jsondata&lastNObservations=24"
    )
    r2 = safe_get(url_euribor)
    if r2:
        try:
            d = r2.json()
            points = parse_ecb_timeseries(d)
            points = [p for p in points if -2 <= p["hodnota"] <= 10]
            result["euribor_3m"] = points
            log.info(f"  ✅ EURIBOR 3M: {points[-1] if points else 'N/A'}")
        except Exception as e:
            log.warning(f"  ⚠️  EURIBOR 3M: {e}")

    return result


# ── Politika — prieskumy ──────────────────────────────────────────────────────

def scrape_nms_polls():
    """NMS Market Research — volebné prieskumy."""
    log.info("🗳  NMS Market Research...")
    for url in [
        "https://nms.global/sk/category/volebny-model/",
        "https://nms.global/category/volebny-model/",
    ]:
        r = safe_get(url)
        if not r or r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        articles = soup.select("article")[:5]
        results = []
        for art in articles:
            title_el = art.select_one("h2 a, h3 a")
            if not title_el:
                continue
            date_el = art.select_one("time")
            results.append({
                "title":  title_el.get_text(strip=True),
                "link":   title_el["href"],
                "date":   date_el.get("datetime", "") if date_el else "",
                "source": "NMS Market Research"
            })
        log.info(f"  ✅ NMS: {len(results)} prieskumov")
        return results

    log.warning("  ⚠️  NMS: žiadne výsledky")
    return []


def scrape_politpro():
    """PolitPro / AKO — prieskumy."""
    log.info("🗳  PolitPro / AKO...")
    url = "https://politpro.eu/sk/slovensko/volebne-prieskumy"
    r = safe_get(url)
    if not r:
        url = "https://politpro.eu/cs/slovensko/volebni-pruzkumy"
        r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    # Hľadáme linky na prieskumy
    links = soup.select("a[href*='prieskum'], a[href*='pruzkum'], a[href*='parlamentn']")[:8]
    polls = []
    seen = set()
    for lnk in links:
        href = lnk.get("href", "")
        title = lnk.get_text(strip=True)
        if not title or not href or href in seen or len(title) < 10:
            continue
        seen.add(href)
        polls.append({
            "title":  title,
            "link":   "https://politpro.eu" + href if href.startswith("/") else href,
            "source": "PolitPro / AKO"
        })
    log.info(f"  ✅ PolitPro: {len(polls)} odkazov")
    return polls


# ── RSS Feedy ─────────────────────────────────────────────────────────────────

RSS_SOURCES = {
    "nbs":       ("https://nbs.sk/sk/rss", 4),
    "mfsr":      ("https://www.mfsr.sk/sk/rss/spravy.rss", 3),
    "sme":       ("https://sme.sk/rss/ekonomika", 4),
    "pravda":    ("https://ekonomika.pravda.sk/rss/", 4),
    "stvr":      ("https://spravy.stvr.sk/feed/", 5),
    "dennikn":   ("https://dennikn.sk/feed/", 4),
    "startitup": ("https://startitup.sk/feed/", 3),
}


# ── Hlavná funkcia ────────────────────────────────────────────────────────────

def run_scraper():
    log.info("=" * 60)
    log.info("🚀 SlovakiaNow Scraper v2.0 — štart")
    log.info("=" * 60)

    output = {
        "meta": {
            "aktualizovane": datetime.now(timezone.utc).isoformat(),
            "verzia": "2.0.0",
            "zdroje": []
        },
        "ekonomika": {},
        "energie": {},
        "urokove_sadzby": {},
        "prieskumy": {},
        "spravy": [],
        "errors": []
    }

    def add_source(nazov, url, fmt):
        output["meta"]["zdroje"].append({"nazov": nazov, "url": url, "format": fmt})

    def add_error(msg):
        output["errors"].append(msg)
        log.warning(f"  ⚠️  {msg}")

    # ── 1. INFLÁCIA HICP ──────────────────────────────────────────────────────
    hicp = fetch_inflacia_hicp()
    if hicp:
        output["ekonomika"]["inflacia_mesacna"] = hicp
        output["ekonomika"]["hicp_eurostat"] = hicp
        add_source("ECB — HICP Inflácia SK", "https://data-api.ecb.europa.eu", "JSON")
    else:
        output["ekonomika"]["inflacia_mesacna"] = []
        output["ekonomika"]["hicp_eurostat"] = []
        add_error("ECB inflácia: nedostupné")

    # ── 2. HDP RAST ───────────────────────────────────────────────────────────
    hdp = fetch_hdp()
    if hdp:
        output["ekonomika"]["hdp_stvrtrocne"] = hdp
        add_source("Eurostat — HDP rast SK (%)", "https://ec.europa.eu/eurostat", "SDMX JSON")
    else:
        output["ekonomika"]["hdp_stvrtrocne"] = []
        add_error("Eurostat HDP: nedostupné")

    # ── 3. NEZAMESTNANOSŤ ─────────────────────────────────────────────────────
    unemp = fetch_nezamestnanost()
    if unemp:
        output["ekonomika"]["nezamestnanost"] = unemp
        add_source("Eurostat — Nezamestnanosť SK", "https://ec.europa.eu/eurostat", "SDMX JSON")
    else:
        output["ekonomika"]["nezamestnanost"] = []
        add_error("Eurostat nezamestnanosť: nedostupné")

    # ── 4. PRIEMERNÁ MZDA ─────────────────────────────────────────────────────
    mzda = fetch_mzda()
    if mzda:
        output["ekonomika"]["priem_mzda"] = mzda
        add_source("SÚSR — Priemerná mzda SK", "https://data.statistics.sk/api/v2", "JSON-stat")
    else:
        output["ekonomika"]["priem_mzda"] = []
        add_error("SÚSR mzda: nedostupné")

    # ── 5. VLÁDNY DLH A DEFICIT ───────────────────────────────────────────────
    dlh_data = fetch_vladny_dlh()
    output["ekonomika"]["vladny_dlh"]    = dlh_data.get("dlh", [])
    output["ekonomika"]["vladny_deficit"] = dlh_data.get("deficit", [])
    if dlh_data.get("dlh"):
        add_source("Eurostat — Vládny dlh SK", "https://ec.europa.eu/eurostat", "SDMX JSON")
    else:
        add_error("Eurostat vládny dlh: nedostupné")

    # ── 6. INFLÁCIA PODĽA KATEGÓRIÍ ───────────────────────────────────────────
    infl_kat = fetch_inflacia_kategorie()
    output["ekonomika"]["inflacia_kategorie"] = infl_kat
    if infl_kat:
        add_source("ECB — Inflácia SK podľa kategórií", "https://data-api.ecb.europa.eu", "JSON")

    # ── 7. ELEKTRINA ──────────────────────────────────────────────────────────
    el = fetch_elektrina()
    if el:
        output["energie"]["elektrina_centkwh"] = el
        add_source("Eurostat — Ceny elektriny SK", "https://ec.europa.eu/eurostat", "SDMX JSON")
    else:
        output["energie"]["elektrina_centkwh"] = []
        add_error("Eurostat elektrina: nedostupné")

    # ── 8. PLYN ───────────────────────────────────────────────────────────────
    pl = fetch_plyn()
    if pl:
        output["energie"]["plyn_centkwh"] = pl
        add_source("Eurostat — Ceny plynu SK", "https://ec.europa.eu/eurostat", "SDMX JSON")
    else:
        output["energie"]["plyn_centkwh"] = []
        add_error("Eurostat plyn: nedostupné")

    # ── 9. POHONNÉ HMOTY ──────────────────────────────────────────────────────
    phm = fetch_phm()
    output["energie"]["benzin"] = phm.get("benzin", [])
    output["energie"]["nafta"]  = phm.get("nafta", [])
    if phm.get("benzin"):
        add_source("GlobalPetrolPrices — PHM SR", "https://www.globalpetrolprices.com/Slovakia/", "HTML")
    else:
        add_error("PHM benzín: nedostupné")
    if not phm.get("nafta"):
        add_error("PHM nafta: nedostupné")

    # ── 10. ÚROKOVÉ SADZBY ────────────────────────────────────────────────────
    sadzby = fetch_urokove_sadzby()
    output["urokove_sadzby"] = sadzby
    if sadzby.get("ecb_sadzba"):
        add_source("ECB — Kľúčové úrokové sadzby", "https://data-api.ecb.europa.eu", "JSON")
    else:
        add_error("ECB úrokové sadzby: nedostupné")
    if sadzby.get("euribor_3m"):
        add_source("ECB — EURIBOR 3M", "https://data-api.ecb.europa.eu", "JSON")
    else:
        add_error("ECB EURIBOR 3M: nedostupné")

    # ── 11. RSS FEEDY ─────────────────────────────────────────────────────────
    all_news = []
    for key, (url, max_items) in RSS_SOURCES.items():
        log.info(f"📰 RSS: {key}...")
        items = fetch_rss(url, max_items=max_items)
        for item in items:
            item["kategoria"] = key
        all_news.extend(items)
        if items:
            add_source(items[0].get("source", key), url, "RSS")
        else:
            add_error(f"RSS {key}: žiadne správy")

    output["spravy"] = all_news
    log.info(f"  ✅ Správy celkom: {len(all_news)}")

    # ── 12. PRIESKUMY ─────────────────────────────────────────────────────────
    output["prieskumy"]["nms"]      = scrape_nms_polls()
    output["prieskumy"]["politpro"] = scrape_politpro()
    if output["prieskumy"]["nms"]:
        add_source("NMS Market Research", "https://nms.global/sk/", "HTML")
    if output["prieskumy"]["politpro"]:
        add_source("PolitPro / AKO", "https://politpro.eu/sk/", "HTML")

    # ── Uložiť výstup ─────────────────────────────────────────────────────────
    os.makedirs("docs/data", exist_ok=True)
    with open("docs/data/latest.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info("=" * 60)
    log.info(f"✅ Hotovo! Chyby: {len(output['errors'])}")
    for e in output["errors"]:
        log.warning(f"  ⚠️  {e}")
    log.info("Uložené: docs/data/latest.json")
    log.info("=" * 60)

    return output


if __name__ == "__main__":
    run_scraper()
