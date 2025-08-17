from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import base64
import json
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

# ===== CONFIG =====
BASE_URL = "https://vtopcc.vit.ac.in/vtop/open/page"
LOGIN_PAGE_URL = "https://vtopcc.vit.ac.in/vtop/login"
# This POST URL may differ on your campus. Common patterns:
#   /vtop/doLogin  OR  /vtop/processLogin  OR a form action found on login page
# We will auto-detect form action from the login page HTML below.

# ===== FASTAPI APP =====
app = FastAPI(title="VTOP Login API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For testing; later restrict to your Lovable domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class StartResponse(BaseModel):
    captcha_needed: bool
    captcha_image_b64: str | None = None
    prelogin_cookies_token: str
    login_form_action: str | None = None

class FinishRequest(BaseModel):
    username: str
    password: str
    captcha_text: str | None = None
    prelogin_cookies_token: str
    login_form_action: str | None = None

class CookiesModel(BaseModel):
    cookies: dict

class TimetableRequest(BaseModel):
    cookies: dict
    timetable_url: str | None = None  # optional override

# ----- Helper to encode/decode cookies safely -----
def encode_cookies(cj: requests.cookies.RequestsCookieJar) -> str:
    data = requests.utils.dict_from_cookiejar(cj)
    return base64.b64encode(json.dumps(data).encode()).decode()

def decode_cookies(token: str) -> requests.cookies.RequestsCookieJar:
    data = json.loads(base64.b64decode(token).decode())
    return requests.utils.cookiejar_from_dict(data)

# ----- Detect captcha <img> on login page -----
def find_captcha_info(soup: BeautifulSoup) -> str | None:
    # Try common patterns
    img = soup.select_one("img#captcha, img[src*='captcha'], img[id*='captcha']")
    if img and img.get("src"):
        return img["src"]
    return None

# ----- Detect login form action URL -----
def find_login_action(soup: BeautifulSoup) -> str | None:
    form = soup.find("form")
    if form and form.get("action"):
        return form["action"]
    return None

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/login/start", response_model=StartResponse)
def login_start():
    s = requests.Session()
    resp = s.get(LOGIN_PAGE_URL, timeout=20)
    if not resp.ok:
        raise HTTPException(status_code=502, detail="Failed to open login page")

    soup = BeautifulSoup(resp.text, "lxml")
    captcha_src = find_captcha_info(soup)
    action = find_login_action(soup)

    captcha_b64 = None
    if captcha_src:
        # Make absolute URL if needed
        full_captcha_url = urljoin(LOGIN_PAGE_URL, captcha_src)
        cimg = s.get(full_captcha_url, timeout=20)
        if cimg.ok:
            captcha_b64 = base64.b64encode(cimg.content).decode()

    token = encode_cookies(s.cookies)
    return StartResponse(
        captcha_needed=bool(captcha_src),
        captcha_image_b64=captcha_b64,
        prelogin_cookies_token=token,
        login_form_action=action,
    )

@app.post("/login/finish")
def login_finish(payload: FinishRequest):
    # Restore the same session (cookies) used for captcha
    s = requests.Session()
    s.cookies = decode_cookies(payload.prelogin_cookies_token)

    # Get a fresh copy of login page to read any hidden fields
    pg = s.get(LOGIN_PAGE_URL, timeout=20)
    if not pg.ok:
        raise HTTPException(status_code=502, detail="Failed to open login page (finish)")
    soup = BeautifulSoup(pg.text, "lxml")

    # Determine form action
    action = payload.login_form_action or find_login_action(soup)
    if not action:
        action = "/vtop/doLogin"  # fallback guess; adjust if needed
    action_url = urljoin(LOGIN_PAGE_URL, action)

    # Collect form fields (username/password + any hidden inputs)
    form = soup.find("form")
    data = {}
    if form:
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            data[name] = inp.get("value", "")

    # Map common field names (adjust if your portal differs)
    # Typical names: username, password, captchaCode
    # Replace these if the actual input names differ (inspect in DevTools > Network > login request)
    data.update({
        "username": payload.username,
        "password": payload.password,
    })
    if payload.captcha_text:
        data["captchaCode"] = payload.captcha_text

    # Mimic browser
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/118 Safari/537.36",
        "Referer": LOGIN_PAGE_URL,
    }

    r = s.post(action_url, data=data, headers=headers, timeout=30, allow_redirects=True)

    # Heuristic: if redirected to dashboard or any authenticated page, login succeeded
    if r.status_code in (200, 302) and ("logout" in r.text.lower() or "dashboard" in r.text.lower() or r.url != LOGIN_PAGE_URL):
        return {
            "success": True,
            "cookies": requests.utils.dict_from_cookiejar(s.cookies),
            "landing_url": r.url,
        }

    # Otherwise, try to detect common errors
    if "captcha" in r.text.lower():
        raise HTTPException(status_code=401, detail="Captcha required or incorrect")
    if "invalid" in r.text.lower() or "incorrect" in r.text.lower():
        raise HTTPException(status_code=401, detail="Invalid credentials")

    raise HTTPException(status_code=400, detail="Login failed; check form field names/action URL")

@app.post("/timetable")
def get_timetable(req: TimetableRequest):
    s = requests.Session()
    s.cookies = requests.utils.cookiejar_from_dict(req.cookies)

    # Default guess for timetable URL; change if your portal uses a different path
    url = req.timetable_url or f"{BASE_URL}/vtop/academics/student/timetable"

    r = s.get(url, timeout=30)
    if not r.ok:
        raise HTTPException(status_code=502, detail=f"Failed to fetch timetable: {r.status_code}")

    soup = BeautifulSoup(r.text, "lxml")
    table = soup.find("table")
    if not table:
        raise HTTPException(status_code=404, detail="No table found; verify you are logged in and URL is correct")

    # Parse table into JSON (rows -> list of dicts using header texts)
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    data_rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        values = [td.get_text(strip=True) for td in tds]
        if headers and len(values) == len(headers):
            row = dict(zip(headers, values))
        else:
            # fallback generic
            row = {f"col{i}": v for i, v in enumerate(values, start=1)}
        data_rows.append(row)

    return {"success": True, "count": len(data_rows), "rows": data_rows}
