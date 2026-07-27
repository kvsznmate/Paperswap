# Walkthrough & Project Documentation ⚡

This document outlines the complete setup, architecture, and features of the **Tinder-Style Tech & Finance News App**.

---

## 📁 Workspace Files in `backend/`

- **[`main.py`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/main.py)**: FastAPI web app server, CLI runner, and REST endpoints.
- **[`database.py`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/database.py)**: SQLite database engine (`news_database.db`) with deduplication & swipe tracking.
- **[`news_fetcher.py`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/news_fetcher.py)**: NewsAPI & Google News RSS fetcher with smart short summary generator.
- **[`card_generator.py`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/card_generator.py)**: Pillow 9:16 vertical portrait card renderer (720x1280 px).
- **[`templates/mobile_preview.html`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/templates/mobile_preview.html)**: Interactive Tinder swipe mobile app UI.
- **[`Dockerfile`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/Dockerfile)** & **[`docker-compose.yml`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/docker-compose.yml)**: Docker container suite.
- **[`output/cards/`](file:///c:/Users/matek_yulq090/Desktop/Antigrav_test/backend/output/cards)**: Stores generated 9:16 PNG news card images.

---

## 🚀 How to Run

### Option 1: Run with Python
```bash
python main.py
```
- Open **`http://localhost:8000/mobile`** for the Tinder Swipe App.
- Open **`http://localhost:8000`** for the Gallery Dashboard.

### Option 2: Run with Docker Compose
```bash
docker-compose up --build
```

---

## 🗄 SQLite Database & Deduplication

1. **Unique MD5 Hash Key**: Computes a unique key for each article using `title` + `url`.
2. **Deduplication**: Checks SQLite before rendering PNG cards or inserting. If the article already exists, it skips duplicate card rendering and database inserts.
3. **Swipe API**: Logs user swiping actions via `POST /api/v1/swipe`.
