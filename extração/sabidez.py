# Requer Python 3.10+
# pip install selenium pyyaml  (ou: pip install playwright && playwright install chromium)
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import urlencode, urlparse

import yaml

DEFAULT_TIMEOUT = 15  # segundos
RAW_PREFIXES = ("css=", "xpath=", "//", "#", ".", "[")


def is_raw_selector(t: str) -> bool:
    return t.startswith(RAW_PREFIXES)


class ElementNotFound(Exception):
    pass


# exceção adicionada para representar erros http no get_json do navegador
class HttpStatusError(Exception):
    """Erro HTTP retornado por get_json no navegador."""

    def __init__(self, status: int, url: str, body: str = "", retry_after: str = ""):
        super().__init__(f"HTTP {status} em {url}: {body[:200]!r}")
        self.status = status
        self.url = url
        self.body = body
        self.retry_after = retry_after


# função adicionada para montar url com query string
def _build_url(url: str, params: dict | None) -> str:
    if not params:
        return url
    qs = urlencode(params, doseq=True)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{qs}"


# função adicionada para extrair a origem (esquema + host) da url
def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


# ---------------------------------------------------------------- interface
# classe refatorada para suportar context manager e método abstrato get_json
class Driver(ABC):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @abstractmethod
    def goto(self, url: str): ...
    @abstractmethod
    def find(self, target: str, kind: str | None = None, timeout: float = DEFAULT_TIMEOUT): ...
    @abstractmethod
    def click(self, el): ...
    @abstractmethod
    def fill(self, el, value: str): ...
    @abstractmethod
    def press(self, key: str): ...
    @abstractmethod
    def text(self, el) -> str: ...
    @abstractmethod
    def screenshot(self, path: str): ...
    @abstractmethod
    def get_json(self, url: str, params: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict: ...
    @abstractmethod
    def close(self): ...

    def _poll(self, attempt, timeout, target):
        deadline = time.monotonic() + timeout
        while True:
            el = attempt()
            if el is not None:
                return el
            if time.monotonic() >= deadline:
                raise ElementNotFound(f"Não encontrei: {target!r}")
            time.sleep(0.3)


# ---------------------------------------------------------------- Playwright
class PlaywrightDriver(Driver):
    def __init__(self, headless=True, browser="chromium"):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        if browser in ("auto", "chrome", "edge"):
            browser = "chromium"
        self._browser = getattr(self._pw, browser).launch(headless=headless)
        self.page = self._browser.new_page()
        self._warmed_origins: set[str] = set()

    def goto(self, url):
        self.page.goto(url, wait_until="domcontentloaded")
        self._warmed_origins.add(_origin(url))

    def _candidates(self, scope, t, kind):
        if is_raw_selector(t):
            yield scope.locator(t)  # Playwright entende css=, xpath= e //
            return
        rx = re.compile(re.escape(t), re.I)
        esc = t.replace('"', r'\"')
        if kind in (None, "input"):
            yield scope.get_by_label(rx)          # inclui aria-label
            yield scope.get_by_placeholder(rx)
            yield scope.get_by_role("textbox", name=rx)
            yield scope.locator(f'[name*="{esc}" i]')
        if kind in (None, "button"):
            yield scope.get_by_role("button", name=rx)
            yield scope.get_by_role("link", name=rx)
        yield scope.get_by_text(rx)
        yield scope.get_by_title(rx)

    def find(self, target, kind=None, timeout=DEFAULT_TIMEOUT):
        def attempt():
            for frame in self.page.frames:  # varre iframes também
                for loc in self._candidates(frame, target, kind):
                    try:
                        for cand in loc.all()[:5]:
                            if cand.is_visible():
                                return cand
                    except Exception:
                        continue
            return None
        return self._poll(attempt, timeout, target)

    def click(self, el): el.click()
    def fill(self, el, value): el.fill(value)
    def press(self, key): self.page.keyboard.press(key)       # "Enter", "Tab"...
    def text(self, el): return el.inner_text()
    def screenshot(self, path): self.page.screenshot(path=path, full_page=True)

    # função adicionada para buscar json usando a sessão do playwright
    def get_json(self, url: str, params: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
        full_url = _build_url(url, params)
        orig = _origin(full_url)
        self.page.set_default_timeout(timeout * 1000)

        if orig not in self._warmed_origins:
            resp = self.page.goto(full_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            self._warmed_origins.add(orig)
            status = resp.status if resp else 200
            retry_after = (resp.headers.get("retry-after", "") if resp else "")
            body = self.page.locator("body").inner_text()
            if status >= 400:
                raise HttpStatusError(status, full_url, body, retry_after)
            if not body.strip():
                raise ValueError("resposta vazia")
            return json.loads(body)

        res = self.page.evaluate(
            """async (u) => {
                const r = await fetch(u, {headers: {"Accept": "application/json"}, credentials: "include"});
                const text = await r.text();
                return {status: r.status, retryAfter: r.headers.get("Retry-After") || "", text};
            }""",
            full_url,
        )
        if res["status"] >= 400:
            raise HttpStatusError(res["status"], full_url, res["text"], res["retryAfter"])
        if not res["text"].strip():
            raise ValueError("resposta vazia")
        return json.loads(res["text"])

    def close(self):
        self._browser.close()
        self._pw.stop()


# ---------------------------------------------------------------- Selenium
def xpath_literal(s: str) -> str:
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in s.split("'")) + ")"


# função adicionada para detectar automaticamente o navegador instalado no sistema
def _detect_selenium_browser(preferred: str = "auto") -> str:
    if preferred and preferred != "auto":
        return preferred.lower()
    candidates = [
        ("firefox", [r"C:\Program Files\Mozilla Firefox\firefox.exe"]),
        ("edge", [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]),
        ("chrome", [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]),
    ]
    for name, paths in candidates:
        if shutil.which(name) or any(Path(p).is_file() for p in paths):
            return name
    return "firefox"


class SeleniumDriver(Driver):
    UP = "ABCDEFGHIJKLMNOPQRSTUVWXYZÁÀÂÃÉÊÍÓÔÕÚÇ"
    LO = "abcdefghijklmnopqrstuvwxyzáàâãéêíóôõúç"

    # função refatorada para aceitar browser automático, edge e desativar jsonview do firefox
    def __init__(self, headless=True, browser="auto"):
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        self.By = By
        self._warmed_origins: set[str] = set()
        browser = _detect_selenium_browser(browser)
        if browser == "firefox":
            opts = webdriver.FirefoxOptions()
            opts.set_preference("devtools.jsonview.enabled", False)
            if headless:
                opts.add_argument("-headless")
            self.d = webdriver.Firefox(options=opts)
        elif browser == "edge":
            opts = webdriver.EdgeOptions()
            if headless:
                opts.add_argument("--headless=new")
            self.d = webdriver.Edge(options=opts)
        else:
            opts = webdriver.ChromeOptions()
            if headless:
                opts.add_argument("--headless=new")
            self.d = webdriver.Chrome(options=opts)

    def goto(self, url):
        self.d.get(url)
        self._warmed_origins.add(_origin(url))

    def _xpaths(self, t, kind):
        lit = xpath_literal(t.lower())
        ci = lambda e: f"translate({e},'{self.UP}','{self.LO}')"
        has = lambda e: f"contains({ci(e)}, {lit})"
        field = "(self::input or self::textarea or self::select)"
        clickable = ("(self::button or self::a or @role='button' or "
                     "(self::input and (@type='submit' or @type='button')))")
        if kind in (None, "input"):
            yield f"//*[{field}][@id = //label[{has('normalize-space(.)')}]/@for]"
            yield f"//label[{has('normalize-space(.)')}]//*[{field}]"
            yield f"//*[{field}][{has('@placeholder')} or {has('@aria-label')} or {has('@name')}]"
        if kind in (None, "button"):
            yield (f"//*[{clickable}][{has('normalize-space(.)')} or "
                   f"{has('@value')} or {has('@aria-label')}]")
        yield f"//*[{has('normalize-space(text())')}]"
        yield f"//*[{has('@title')}]"

    def find(self, target, kind=None, timeout=DEFAULT_TIMEOUT):
        By = self.By
        if is_raw_selector(target):
            if target.startswith(("xpath=", "//")):
                strategies = [(By.XPATH, target.removeprefix("xpath="))]
            else:
                strategies = [(By.CSS_SELECTOR, target.removeprefix("css="))]
        else:
            strategies = [(By.XPATH, x) for x in self._xpaths(target, kind)]

        def attempt():
            for by, sel in strategies:
                try:
                    for el in self.d.find_elements(by, sel)[:5]:
                        if el.is_displayed():
                            return el
                except Exception:
                    continue
            return None
        return self._poll(attempt, timeout, target)

    def click(self, el):
        try:
            el.click()
        except Exception:  # elemento coberto por overlay etc.
            self.d.execute_script("arguments[0].click()", el)

    def fill(self, el, value):
        el.clear()
        el.send_keys(value)

    def press(self, key):
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys
        ActionChains(self.d).send_keys(getattr(Keys, key.upper(), key)).perform()

    def text(self, el): return el.text
    def screenshot(self, path): self.d.save_screenshot(path)

    # função adicionada para buscar json usando a sessão do selenium
    def get_json(self, url: str, params: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
        full_url = _build_url(url, params)
        orig = _origin(full_url)
        self.d.set_page_load_timeout(timeout)
        self.d.set_script_timeout(timeout)

        # Na primeira requisição ao domínio, abre a URL diretamente no navegador para estabelecer cookies/sessão
        if orig not in self._warmed_origins:
            self.d.get(full_url)
            self._warmed_origins.add(orig)
            pres = self.d.find_elements(self.By.TAG_NAME, "pre")
            body = pres[0].text if pres else self.d.find_element(self.By.TAG_NAME, "body").text
            titulo = (self.d.title or "").lower()
            if "acesso bloqueado" in titulo or "access denied" in titulo or "403" in titulo:
                raise HttpStatusError(403, full_url, body)
            if not body.strip():
                raise ValueError("resposta vazia")
            return json.loads(body)

        # Nas requisições seguintes do mesmo domínio, usa fetch() assíncrono na sessão já aberta
        res = self.d.execute_async_script(
            """
            const url = arguments[0];
            const done = arguments[arguments.length - 1];
            fetch(url, {headers: {"Accept": "application/json"}, credentials: "include"})
              .then(async r => {
                  const text = await r.text();
                  done({ok: true, status: r.status, retryAfter: r.headers.get("Retry-After") || "", text: text});
              })
              .catch(e => done({ok: false, error: String(e)}));
            """,
            full_url,
        )
        if not res or not res.get("ok"):
            raise ConnectionError(res.get("error", "falha no fetch do navegador") if res else "sem resposta do navegador")
        status = int(res.get("status", 200))
        texto = res.get("text", "")
        if status >= 400:
            raise HttpStatusError(status, full_url, texto, res.get("retryAfter", ""))
        if not texto.strip():
            raise ValueError("resposta vazia")
        return json.loads(texto)

    def close(self): self.d.quit()


# ---------------------------------------------------------------- motor
ENV_RX = re.compile(r"\$\{(\w+)\}")


# função refatorada para expandir variáveis de ambiente recursivamente em dicionários e listas
def expand(v):
    if isinstance(v, str):
        return ENV_RX.sub(lambda m: os.environ.get(m.group(1), ""), v)
    if isinstance(v, dict):
        return {k: expand(val) for k, val in v.items()}
    if isinstance(v, list):
        return [expand(item) for item in v]
    return v


class Engine:
    def __init__(self, driver: Driver, out_dir="saida"):
        self.d = driver
        self.data: dict[str, object] = {}
        self.out = Path(out_dir)
        self.out.mkdir(exist_ok=True)

    def run(self, steps, retries=2):
        for i, step in enumerate(steps, 1):
            (action, arg), = step.items()
            for attempt in range(retries + 1):
                try:
                    self._do(action, arg)
                    break
                except Exception as e:
                    if attempt == retries:
                        shot = self.out / f"erro_passo_{i}.png"
                        try:
                            self.d.screenshot(str(shot))
                        except Exception:
                            pass
                        raise RuntimeError(
                            f"Passo {i} ({action}) falhou: {e}. Print em {shot}") from e
        return self.data

    # função refatorada para incluir as ações get_json e paginate_ckan
    def _do(self, action, arg):
        arg = expand(arg)
        d = self.d
        match action:
            case "goto":       d.goto(arg)
            case "click":      d.click(d.find(arg, kind="button"))
            case "fill":       d.fill(d.find(arg["target"], kind="input"), arg["value"])
            case "press":      d.press(arg)
            case "wait_for":   d.find(arg, timeout=30)
            case "sleep":      time.sleep(float(arg))
            case "extract":    self.data[arg["as"]] = d.text(d.find(arg["target"]))
            case "screenshot": d.screenshot(str(self.out / arg))
            case "get_json":
                dados = d.get_json(arg["url"], params=arg.get("params"), timeout=float(arg.get("timeout", 60)))
                if "as" in arg:
                    self.data[arg["as"]] = dados
                if "save" in arg:
                    caminho = self.out / arg["save"]
                    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
            case "paginate_ckan":
                url = arg["url"]
                resource_id = arg["resource_id"]
                limit = int(arg.get("limit", 5000))
                offset = int(arg.get("offset", 0))
                timeout = float(arg.get("timeout", 60))
                registros = []
                while True:
                    pagina = d.get_json(
                        url,
                        params={"resource_id": resource_id, "limit": limit, "offset": offset, "sort": "_id asc"},
                        timeout=timeout,
                    )
                    res = pagina.get("result") or {}
                    lote = res.get("records") or []
                    if not lote:
                        break
                    registros.extend(lote)
                    offset += len(lote)
                    total = res.get("total")
                    if total is not None and offset >= int(total):
                        break
                if "as" in arg:
                    self.data[arg["as"]] = registros
                if "save" in arg:
                    caminho = self.out / arg["save"]
                    caminho.write_text(json.dumps(registros, ensure_ascii=False), encoding="utf-8")
            case _:            raise ValueError(f"Ação desconhecida: {action}")


# função refatorada para escolher automaticamente entre selenium e playwright quando name="auto"
def build_driver(name="auto", **kw) -> Driver:
    if name == "auto":
        try:
            import selenium  # noqa: F401
            name = "selenium"
        except ImportError:
            name = "playwright"
    return {"playwright": PlaywrightDriver, "selenium": SeleniumDriver}[name](**kw)


if __name__ == "__main__":
    recipe = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
    drv = build_driver(
        recipe.get("driver", "auto"),
        headless=recipe.get("headless", True),
        browser=recipe.get("browser", "auto"),
    )
    try:
        resultado = Engine(drv).run(recipe["steps"])
        resumo = {k: (f"<{len(v)} itens>" if isinstance(v, (list, dict)) else v) for k, v in resultado.items()}
        print(resumo)
    finally:
        drv.close()
