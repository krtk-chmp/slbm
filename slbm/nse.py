"""NSE data downloads. Uses curl_cffi to impersonate Chrome (NSE blocks plain scripts)."""
import io
import time
import zipfile
import logging
from datetime import date

from curl_cffi import requests as cr

log = logging.getLogger("slbm")

ARCHIVES = "https://nsearchives.nseindia.com"
MAIN = "https://www.nseindia.com"


class NSEClient:
    def __init__(self):
        self.s = cr.Session(impersonate="chrome")
        self._warmed = False

    def _warm(self):
        """Visit homepage once to collect cookies (needed for API calls, not archives)."""
        if not self._warmed:
            try:
                self.s.get(MAIN, timeout=20)
                self._warmed = True
            except Exception as e:
                log.warning("homepage warm-up failed: %s", e)

    def _get(self, url: str, tries: int = 3) -> bytes | None:
        """GET with retries. Returns None on 404 (holiday / file not published)."""
        for i in range(tries):
            try:
                r = self.s.get(url, timeout=30)
                if r.status_code == 404:
                    return None
                if r.status_code == 200:
                    body = r.content
                    # archives server sometimes returns a 200 HTML error page
                    if body.lstrip()[:9] == b"<!DOCTYPE":
                        return None
                    return body
                log.warning("HTTP %s for %s (try %d)", r.status_code, url, i + 1)
            except Exception as e:
                log.warning("error fetching %s: %s (try %d)", url, e, i + 1)
            time.sleep(2 * (i + 1))
        return None

    # ---- daily files ----------------------------------------------------

    def slbm_bhavcopy(self, d: date) -> str | None:
        """SLBM daily trades. 17 cols, no header. Verified mapping:
        0 name, 1 symbol, 2 series, 3 rev_leg_date, 4 flag, 5 prev_close,
        6 open, 7 high, 8 low, 9 close, 10 (blank), 11 qty, 12 value,
        13 period_high, 14 period_low, 15 trade_date, 16 num_trades"""
        url = f"{ARCHIVES}/archives/slbs/bhavcopy/SLBM_BC_{d:%d%m%Y}.DAT"
        b = self._get(url)
        return b.decode("utf-8", "replace") if b else None

    def slb_open_positions(self, d: date) -> str | None:
        url = f"{ARCHIVES}/archives/slbs/open_pos/slb_openpos_{d:%d%m%Y}.csv"
        b = self._get(url)
        return b.decode("utf-8", "replace") if b else None

    def equity_bhavdata(self, d: date) -> str | None:
        """Full CM bhavdata: prices + delivery %."""
        url = f"{ARCHIVES}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"
        b = self._get(url)
        return b.decode("utf-8", "replace") if b else None

    def fo_bhavcopy(self, d: date) -> str | None:
        """F&O UDIFF bhavcopy (zipped CSV) -> csv text."""
        url = f"{ARCHIVES}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
        b = self._get(url)
        if not b:
            return None
        try:
            z = zipfile.ZipFile(io.BytesIO(b))
            return z.read(z.namelist()[0]).decode("utf-8", "replace")
        except Exception as e:
            log.warning("bad FO zip for %s: %s", d, e)
            return None

    # ---- APIs (main site, needs cookies; degrade gracefully) ------------

    def corporate_actions(self) -> list[dict]:
        """Forthcoming + recent corporate actions (dividends etc)."""
        self._warm()
        try:
            r = self.s.get(
                f"{MAIN}/api/corporates-corporateActions?index=equities", timeout=30
            )
            if r.status_code == 200:
                return r.json()
            log.warning("corp actions HTTP %s", r.status_code)
        except Exception as e:
            log.warning("corp actions failed: %s", e)
        return []
