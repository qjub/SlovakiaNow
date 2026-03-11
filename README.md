# 🇸🇰 SlovakiaNow

Transparentný AI dashboard — politika, ekonomika, energie.

## Štruktúra

```
SlovakiaNow/
├── docs/
│   ├── index.html        ← Frontend (GitHub Pages)
│   └── data/
│       └── latest.json   ← Generovaný scraperom
├── backend/
│   └── scraper.py        ← Python scraper
├── .github/workflows/
│   └── scraper.yml       ← GitHub Actions (každé 4 hod.)
└── requirements.txt
```

## GitHub Pages nastavenie

Settings → Pages → Source: **main** branch, folder: **`/docs`**
