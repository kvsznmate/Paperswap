# Tech & Finance News Visual Card Generator ⚡

A Python application that fetches the **latest 20 news articles** about the **Tech Industry** and **Finance**, processes article details, and generates visual cards for each news item.

All code and outputs are located inside the `backend/` folder.

---

## 🌟 Features

- **20 News Items (10 Tech + 10 Finance)**: Fetches fresh news headlines, sources, publication dates, and thumbnails.
- **NewsAPI + Fallback Engine**: Uses [NewsAPI.org](https://newsapi.org/) when an API key is configured. Automatically falls back to Google News RSS feeds if no API key is provided, so it works out-of-the-box.
- **Python Pillow Visual PNG Card Generator**: Generates high-resolution 1000x560px dark-mode PNG cards for every article in `backend/output/cards/`.
- **FastAPI Visual Dashboard**: Live interactive web gallery displaying modern visual cards with image previews, category filters, direct article links, and PNG downloads.

---

## 📁 Directory Structure

```text
backend/
├── main.py                # FastAPI web app & CLI runner
├── news_fetcher.py        # NewsAPI & RSS news retrieval module
├── card_generator.py      # Pillow PNG visual card renderer
├── requirements.txt       # Dependencies
├── .env.example           # Environment template (NEWS_API_KEY)
├── templates/
│   └── dashboard.html     # Interactive web cards gallery UI
└── output/
    └── cards/             # 20 generated visual PNG cards (card_01_tech.png - card_20_finance.png)
```

---

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure NewsAPI Key (Optional)
Copy `.env.example` to `.env` and add your API key from [NewsAPI.org](https://newsapi.org/):
```env
NEWS_API_KEY=your_actual_api_key_here
```
*(If left blank, the app uses live public tech & finance news feeds).*

---

## 🏃 Usage Options

### Option A: Run via CLI (Direct Card Generation)
Generates 20 visual PNG cards directly to `output/cards/`:
```bash
python main.py --cli
```

### Option B: Launch Web Dashboard
Starts the FastAPI backend server with live visual cards dashboard:
```bash
python main.py
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser!

---

## 🖼 Output Cards

Generated PNG card files are saved under `backend/output/cards/`:
- `card_01_tech.png` ... `card_10_tech.png` (Tech Industry Cards)
- `card_11_finance.png` ... `card_20_finance.png` (Finance & Market Cards)
