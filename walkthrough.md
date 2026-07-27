# Walkthrough & Project Documentation ⚡

This document outlines the complete setup, architecture, and features of the **Tinder-Style Tech & Finance News App**.

---

## 📁 Workspace Files in `backend/`

- **[`backend/main.py`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/main.py)**: FastAPI web app server, CLI runner, and REST endpoints.
- **[`backend/database.py`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/database.py)**: SQLite database engine (`news_database.db`) supporting **50 active cards** (25 Tech + 25 Finance) with deduplication & swipe tracking.
- **[`backend/news_fetcher.py`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/news_fetcher.py)**: Fetches 50 news items per batch with smart short summary generator.
- **[`backend/card_generator.py`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/card_generator.py)**: Pillow 9:16 vertical portrait card renderer (720x1280 px).
- **[`backend/templates/mobile_preview.html`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/templates/mobile_preview.html)**: Interactive Tinder swipe mobile app UI.
- **[`backend/Dockerfile`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/Dockerfile)** & **[`backend/docker-compose.yml`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/docker-compose.yml)**: Docker container suite.
- **[`backend/output/cards/`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/output/cards)**: Stores generated 9:16 PNG news card images.

---

## 🚀 How to Run

### Option 1: Run with Python
```bash
cd backend
python main.py
```
- Open **`http://localhost:8000/mobile`** for the Tinder Swipe App (now serving **50 cards**).
- Open **`http://localhost:8000`** for the Gallery Dashboard.

### Option 2: Run with Docker Compose
```bash
cd backend
docker-compose up --build
```

---

## 🗄 SQLite Database & Deduplication (50-Card Batch Capacity)

1. **Batch Fetch Capacity**: Expanded from 20 to **50 latest cards** (25 Tech Industry + 25 Finance & Markets).
2. **Unique MD5 Hash Key**: Computes a unique key for each article using `title` + `url`.
3. **Deduplication Engine**: Checks SQLite before rendering PNG cards or inserting. If the article already exists, it skips duplicate card rendering and database inserts.
4. **Swipe API**: Logs user swiping actions via `POST /api/v1/swipe`.
