import os
from dotenv import load_dotenv

load_dotenv()

# LLM Configuration — Groq is free at console.groq.com
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"

# Scraping limits
MAX_PAGES_TO_SCRAPE = 12
MAX_CONTENT_LENGTH = 6000   # chars per page before truncation
REQUEST_TIMEOUT = 15         # seconds
REQUEST_DELAY = 0.8          # seconds between requests

# External research
MAX_SEARCH_RESULTS = 6

# Report sections (add/remove as needed)
REPORT_SECTIONS = [
    "Company Snapshot",
    "Business Model & Revenue Deep-Dive",
    "Competitive Landscape",
    "Recent News & Strategic Developments",
    "Investment & Risk Outlook",
]

RELEVANT_PAGE_KEYWORDS = [
    "about", "product", "service", "team", "contact",
    "solution", "blog", "news", "pricing", "feature",
    "company", "story", "mission", "career", "client",
    "portfolio", "case-study", "platform", "how-it-works",
]
