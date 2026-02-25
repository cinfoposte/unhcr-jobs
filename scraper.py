#!/usr/bin/env python3
"""
UNHCR Workday Job Scraper → RSS Feed Generator

Scrapes UNHCR's Workday career site for international professional positions
(P-1 through P-5, D-1, D-2) and internships/fellowships, excluding consultants,
general service (G), national officer (NO), service contracts (SB), and local
service contracts (LSC).

Outputs an RSS 2.0 feed to unhcr_jobs.xml.
"""

import hashlib
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.dom import minidom
from xml.etree.ElementTree import Element, SubElement, parse, tostring

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://unhcr.wd3.myworkdayjobs.com/en-GB/External"
KNOWN_ENDPOINT = "https://unhcr.wd3.myworkdayjobs.com/wday/cxs/unhcr/External/jobs"
PAGE_SIZE = 20
MAX_INCLUDED_JOBS = 50
MAX_PAGES = 50  # safety cap to avoid infinite loops
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unhcr_jobs.xml")
FEED_SELF_URL = "https://cinfoposte.github.io/unhcr-jobs/unhcr_jobs.xml"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://unhcr.wd3.myworkdayjobs.com",
    "Referer": BASE_URL,
})

# ---------------------------------------------------------------------------
# Grade / level patterns
# ---------------------------------------------------------------------------

# Regex to normalize compact grade forms like P4 -> P-4, LSC10 -> LSC-10
GRADE_NORMALIZE_RE = re.compile(
    r'\b(P|D|G|SB|LSC|NO)(\d+)\b', re.IGNORECASE
)

INCLUDED_GRADES = {"P-1", "P-2", "P-3", "P-4", "P-5", "D-1", "D-2"}

EXCLUDED_GRADE_PATTERNS = [
    re.compile(r'\bG-[1-7]\b'),
    re.compile(r'\bNO[A-D]\b'),
    re.compile(r'\bNOA\b'),
    re.compile(r'\bNOB\b'),
    re.compile(r'\bNOC\b'),
    re.compile(r'\bNOD\b'),
    re.compile(r'\bSB-[1-4]\b'),
    re.compile(r'\bLSC-\d{1,2}\b'),
]

CONSULTANT_RE = re.compile(r'\bCONSULTAN', re.IGNORECASE)
INTERN_FELLOWSHIP_RE = re.compile(r'\b(INTERN|FELLOWSHIP)\b', re.IGNORECASE)


def normalize_text(text: str) -> str:
    """Normalize text for grade detection."""
    # Normalize unicode dashes to ASCII hyphen
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uFE58\uFE63\uFF0D]', '-', text)
    # Normalize compact grade forms: P4 -> P-4, LSC10 -> LSC-10, etc.
    text = GRADE_NORMALIZE_RE.sub(lambda m: f"{m.group(1).upper()}-{m.group(2)}", text)
    # Uppercase
    text = text.upper()
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def detect_grades(text: str) -> set:
    """Return set of detected grade strings (e.g. {'P-3', 'D-1'})."""
    normalized = normalize_text(text)
    grades = set()
    for g in INCLUDED_GRADES:
        if g in normalized:
            grades.add(g)
    return grades


def is_excluded_grade(text: str) -> bool:
    """Check if text contains any excluded grade pattern."""
    normalized = normalize_text(text)
    for pat in EXCLUDED_GRADE_PATTERNS:
        if pat.search(normalized):
            return True
    return False


def is_consultant(text: str) -> bool:
    """Check if text mentions consultant/consultancy."""
    return bool(CONSULTANT_RE.search(text))


def is_intern_or_fellowship(text: str) -> bool:
    """Check if text mentions internship or fellowship."""
    return bool(INTERN_FELLOWSHIP_RE.search(text))


def should_include_job(combined_text: str) -> bool:
    """
    Apply filtering decision logic (priority order):
    1) Consultant -> EXCLUDE
    2) Excluded grade (G/NO/SB/LSC) -> EXCLUDE
    3) Included grade (P-1..P-5, D-1..D-2) -> INCLUDE
    4) Internship/Fellowship -> INCLUDE
    5) Else -> EXCLUDE
    """
    if is_consultant(combined_text):
        return False
    if is_excluded_grade(combined_text):
        return False
    if detect_grades(combined_text):
        return True
    if is_intern_or_fellowship(combined_text):
        return True
    return False


# ---------------------------------------------------------------------------
# GUID generation
# ---------------------------------------------------------------------------

def generate_numeric_id(url: str) -> str:
    """Generate a 16-digit zero-padded numeric ID from a URL via MD5."""
    hex_dig = hashlib.md5(url.encode()).hexdigest()
    return str(int(hex_dig[:16], 16) % 10000000000000000).zfill(16)


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

XML_ILLEGAL_RE = re.compile(
    r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f'
    r'\ud800-\udfff\ufdd0-\ufdef\ufffe\uffff]'
)


def clean_xml_text(text: str) -> str:
    """Remove XML 1.0 illegal characters."""
    return XML_ILLEGAL_RE.sub('', text)


# ---------------------------------------------------------------------------
# Endpoint discovery
# ---------------------------------------------------------------------------

def discover_endpoint() -> str:
    """
    Try to discover the Workday JSON jobs endpoint from the career page HTML.
    Falls back to the known endpoint if discovery fails.
    """
    locales = ["en-GB", "en-US", "fr-FR"]
    for locale in locales:
        url = f"https://unhcr.wd3.myworkdayjobs.com/{locale}/External"
        try:
            resp = SESSION.get(url, timeout=30, headers={"Accept": "text/html"})
            if resp.status_code == 200:
                # Look for the CXS endpoint path in the HTML/JS
                match = re.search(
                    r'/wday/cxs/([^/]+)/([^/]+)/jobs', resp.text
                )
                if match:
                    tenant = match.group(1)
                    site = match.group(2)
                    endpoint = f"https://unhcr.wd3.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
                    print(f"[INFO] Discovered endpoint: {endpoint}")
                    return endpoint
        except requests.RequestException as e:
            print(f"[WARN] Failed to fetch {url}: {e}")

    print(f"[INFO] Using known endpoint: {KNOWN_ENDPOINT}")
    return KNOWN_ENDPOINT


# ---------------------------------------------------------------------------
# Job listing via Workday JSON API
# ---------------------------------------------------------------------------

def fetch_job_listings(endpoint: str, offset: int = 0, limit: int = PAGE_SIZE) -> dict:
    """Fetch a page of job listings from the Workday JSON endpoint."""
    payload = {
        "limit": limit,
        "offset": offset,
        "searchText": "",
        "appliedFacets": {},
    }
    resp = SESSION.post(endpoint, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def build_job_url(external_path: str) -> str:
    """Build the full public job URL from the externalPath."""
    if external_path.startswith("http"):
        return external_path
    return f"https://unhcr.wd3.myworkdayjobs.com/en-GB/External{external_path}"


# ---------------------------------------------------------------------------
# Job detail page fetch (for grade detection)
# ---------------------------------------------------------------------------

def fetch_job_detail_text(job_url: str) -> str:
    """Fetch a job's public detail page and return visible text."""
    try:
        resp = SESSION.get(job_url, timeout=30, headers={"Accept": "text/html"})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            return soup.get_text(separator=" ", strip=True)
    except requests.RequestException as e:
        print(f"[WARN] Failed to fetch job detail {job_url}: {e}")
    return ""


def fetch_job_detail_json(endpoint_base: str, external_path: str) -> dict:
    """Fetch structured job detail from the Workday CXS JSON API."""
    detail_url = endpoint_base.replace("/jobs", external_path)
    try:
        resp = SESSION.get(detail_url, timeout=30)
        if resp.status_code == 200:
            return resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[WARN] Failed to fetch job detail JSON {detail_url}: {e}")
    return {}


# ---------------------------------------------------------------------------
# Existing feed parsing
# ---------------------------------------------------------------------------

def load_existing_links(filepath: str) -> set:
    """Parse existing RSS XML and return set of <link> values."""
    links = set()
    if not os.path.isfile(filepath):
        return links
    try:
        tree = parse(filepath)
        for item in tree.iter("item"):
            link_el = item.find("link")
            if link_el is not None and link_el.text:
                links.add(link_el.text.strip())
    except Exception as e:
        print(f"[WARN] Could not parse existing feed: {e}")
    return links


def load_existing_items(filepath: str) -> list:
    """Parse existing RSS XML and return list of item dicts."""
    items = []
    if not os.path.isfile(filepath):
        return items
    try:
        tree = parse(filepath)
        for item in tree.iter("item"):
            item_data = {}
            for child in item:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if tag == "source":
                    item_data["source_text"] = child.text or ""
                    item_data["source_url"] = child.get("url", "")
                else:
                    item_data[tag] = child.text or ""
                    # Preserve guid attribute
                    if tag == "guid":
                        item_data["guid_isPermaLink"] = child.get("isPermaLink", "false")
            items.append(item_data)
    except Exception as e:
        print(f"[WARN] Could not parse existing items: {e}")
    return items


# ---------------------------------------------------------------------------
# RSS generation
# ---------------------------------------------------------------------------

def build_rss_xml(items: list) -> str:
    """
    Build a valid RSS 2.0 XML string with CDATA descriptions.
    items: list of dicts with keys: title, link, description, guid, pubDate, location
    """
    now_rfc2822 = format_datetime(datetime.now(timezone.utc))

    rss = Element("rss", version="2.0")
    rss.set("xmlns:dc", "http://purl.org/dc/elements/1.1/")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")

    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = "UNHCR Job Vacancies"
    SubElement(channel, "link").text = BASE_URL
    SubElement(channel, "description").text = "List of vacancies at UNHCR"
    SubElement(channel, "language").text = "en"

    atom_link = SubElement(channel, "atom:link")
    atom_link.set("href", FEED_SELF_URL)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    SubElement(channel, "pubDate").text = now_rfc2822

    for item_data in items:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = clean_xml_text(item_data.get("title", ""))
        SubElement(item, "link").text = item_data.get("link", "")
        # Description placeholder — will be replaced with CDATA below
        desc_text = item_data.get("description", "")
        SubElement(item, "description").text = f"__CDATA__{clean_xml_text(desc_text)}__CDATA__"
        guid_el = SubElement(item, "guid")
        guid_el.set("isPermaLink", "false")
        guid_el.text = item_data.get("guid", "")
        SubElement(item, "pubDate").text = item_data.get("pubDate", now_rfc2822)
        source_el = SubElement(item, "source")
        source_el.set("url", BASE_URL)
        source_el.text = "UNHCR Job Vacancies"

    # Convert to string via minidom for pretty-printing
    rough = tostring(rss, encoding="unicode", xml_declaration=False)
    # Fix namespace prefix for atom:link (ElementTree escapes the colon)
    rough = rough.replace("atom:link", "atom:link")
    dom = minidom.parseString(rough)
    pretty = dom.toprettyxml(indent="  ", encoding=None)

    # Remove extra XML declaration from minidom
    lines = pretty.split("\n")
    if lines and lines[0].startswith("<?xml"):
        lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
    result = "\n".join(lines)

    # Inject CDATA sections
    result = result.replace("__CDATA__", "]]>")
    # Fix: we need opening CDATA, not closing
    # Pattern: <description>]]>content]]></description>
    # Replace first ]]> after <description> with <![CDATA[
    result = re.sub(
        r'<description>\]\]>',
        '<description><![CDATA[',
        result
    )

    return result


# ---------------------------------------------------------------------------
# Main scraping logic
# ---------------------------------------------------------------------------

def scrape_jobs():
    """Main entry point: discover endpoint, paginate, filter, build RSS."""
    print("[INFO] Starting UNHCR job scraper...")

    # Load existing feed
    existing_links = load_existing_links(OUTPUT_FILE)
    existing_items = load_existing_items(OUTPUT_FILE)
    print(f"[INFO] Loaded {len(existing_items)} existing items from feed")

    # Discover endpoint
    endpoint = discover_endpoint()

    # Paginate through listings
    included_jobs = []
    offset = 0
    total_processed = 0
    pages_fetched = 0

    while len(included_jobs) < MAX_INCLUDED_JOBS and pages_fetched < MAX_PAGES:
        print(f"[INFO] Fetching page at offset={offset}...")
        try:
            data = fetch_job_listings(endpoint, offset=offset, limit=PAGE_SIZE)
        except requests.RequestException as e:
            print(f"[ERROR] Failed to fetch listings at offset={offset}: {e}")
            break

        job_postings = data.get("jobPostings", [])
        if not job_postings:
            print("[INFO] No more job postings returned. Done paginating.")
            break

        total_available = data.get("total", 0)
        print(f"[INFO] Got {len(job_postings)} postings (total available: {total_available})")

        for posting in job_postings:
            if len(included_jobs) >= MAX_INCLUDED_JOBS:
                break

            title = posting.get("title", "").strip()
            external_path = posting.get("externalPath", "")
            job_url = build_job_url(external_path)
            location = posting.get("locationsText", "") or "Unknown"
            posted_on = posting.get("postedOn", "")

            # Skip duplicates
            if job_url in existing_links:
                total_processed += 1
                continue

            # Gather text for filtering: title + any bullet/subtitle fields
            filter_text_parts = [title, location]

            # Check structured fields from listing
            bullet_fields = posting.get("bulletFields", [])
            if bullet_fields:
                filter_text_parts.extend(str(f) for f in bullet_fields)

            listing_text = " ".join(filter_text_parts)

            # Quick check: if consultant in title, skip immediately
            if is_consultant(listing_text):
                total_processed += 1
                continue

            # Try to get grade from listing text first
            normalized_listing = normalize_text(listing_text)
            has_included = bool(detect_grades(normalized_listing))
            has_excluded = is_excluded_grade(normalized_listing)
            has_intern = is_intern_or_fellowship(listing_text)

            # If we can decide from listing alone, do so
            if has_excluded:
                total_processed += 1
                continue

            if has_included or has_intern:
                # Include this job
                pass
            else:
                # Need to check job detail page for grade info
                print(f"[INFO] Checking detail page for: {title[:60]}...")
                detail_text = ""

                # Try JSON detail first
                detail_json = fetch_job_detail_json(endpoint, external_path)
                if detail_json:
                    job_desc = detail_json.get("jobPostingInfo", {})
                    detail_parts = [
                        job_desc.get("jobDescription", ""),
                        job_desc.get("additionalInformation", ""),
                        str(job_desc.get("jobReqSubCategory", "")),
                        str(job_desc.get("workerSubType", "")),
                    ]
                    detail_text = " ".join(detail_parts)

                # Fallback: fetch HTML page
                if not detail_text or not should_include_job(listing_text + " " + detail_text):
                    html_text = fetch_job_detail_text(job_url)
                    if html_text:
                        detail_text = html_text

                combined = listing_text + " " + detail_text
                if not should_include_job(combined):
                    total_processed += 1
                    continue

            # Build description
            desc_parts = [
                f"UNHCR has a vacancy for the position of {title}.",
                f"Location: {location}.",
            ]
            grades = detect_grades(normalize_text(listing_text))
            if grades:
                desc_parts.append(f"Grade: {', '.join(sorted(grades))}.")
            if posted_on:
                desc_parts.append(f"Posted: {posted_on}.")

            description = " ".join(desc_parts)

            # Build pub date
            pub_date = format_datetime(datetime.now(timezone.utc))
            if posted_on:
                try:
                    dt = datetime.fromisoformat(posted_on.replace("Z", "+00:00"))
                    pub_date = format_datetime(dt)
                except (ValueError, TypeError):
                    pass

            job_item = {
                "title": title,
                "link": job_url,
                "description": description,
                "guid": generate_numeric_id(job_url),
                "pubDate": pub_date,
                "location": location,
            }

            included_jobs.append(job_item)
            existing_links.add(job_url)
            total_processed += 1
            print(f"[INFO] INCLUDED ({len(included_jobs)}/{MAX_INCLUDED_JOBS}): {title[:60]}")

            # Be polite
            time.sleep(0.3)

        offset += PAGE_SIZE
        pages_fetched += 1

        if offset >= total_available:
            print("[INFO] Reached end of all postings.")
            break

    print(f"\n[INFO] Processed {total_processed} new postings, included {len(included_jobs)} jobs")

    # Merge: existing items + new items
    all_items = existing_items.copy()
    for job in included_jobs:
        all_items.append(job)

    # Validate minimum title length
    all_items = [item for item in all_items if len(item.get("title", "")) >= 5]

    # Generate RSS
    rss_xml = build_rss_xml(all_items)

    # Write output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(rss_xml)

    print(f"[INFO] Wrote {len(all_items)} items to {OUTPUT_FILE}")
    print("[INFO] Done.")


if __name__ == "__main__":
    scrape_jobs()
