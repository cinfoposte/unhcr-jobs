# UNHCR Jobs RSS Feed

Automated scraper that collects international professional vacancies (P-1 through P-5, D-1, D-2) and internships/fellowships from UNHCR's Workday career site and publishes them as an RSS feed via GitHub Pages.

**Live RSS feed:**
https://cinfoposte.github.io/unhcr-jobs/unhcr_jobs.xml

## What it does

- Scrapes UNHCR's Workday career portal (`https://unhcr.wd3.myworkdayjobs.com/en-GB/External`) using the Workday JSON API
- Filters jobs by grade level, including only P-1 to P-5, D-1, D-2, internships, and fellowships
- Excludes consultants, general service (G-1 to G-7), national officers (NOA–NOD), service contracts (SB-1 to SB-4), and local service contracts (LSC-1 to LSC-11)
- Generates a valid RSS 2.0 feed with accumulated job entries
- Runs automatically every Thursday and Sunday at 06:00 UTC via GitHub Actions

## Local run

```bash
# Clone the repository
git clone https://github.com/cinfoposte/unhcr-jobs.git
cd unhcr-jobs

# Install dependencies
pip install -r requirements.txt

# Run the scraper
python scraper.py
```

The output will be written to `unhcr_jobs.xml` in the repo root.

## GitHub Pages activation

1. Go to **Settings** → **Pages**
2. Under **Source**, select **Deploy from a branch**
3. Choose branch: **main**, folder: **/ (root)**
4. Click **Save**

The feed will be available at:
`https://cinfoposte.github.io/unhcr-jobs/unhcr_jobs.xml`

## cinfoPoste import mapping

| Portal-Feld | Dropdown-Auswahl |
|---|---|
| TITLE | → Title |
| LINK | → Link |
| DESCRIPTION | → Description |
| PUBDATE | → Date |
| ITEM | → Start item |
| GUID | → Unique ID |

## Schedule

The GitHub Actions workflow runs on:
- **Thursday** at 06:00 UTC
- **Sunday** at 06:00 UTC

You can also trigger it manually from the **Actions** tab → **Scrape UNHCR Jobs** → **Run workflow**.
