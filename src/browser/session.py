"""One isolated Chromium process and conversation per task."""

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright


class BrowserSession:
    def __init__(self, *, headless: bool = False):
        self.headless = headless
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.messages: list[str] = []
        self._playwright: Playwright | None = None
        self._tools = None

    @property
    def tools(self):
        from src.browser.tools import BrowserTools

        if self._tools is None:
            self._tools = BrowserTools(self)
        return self._tools

    @property
    def active(self) -> bool:
        return self.browser is not None and self.browser.is_connected()

    def start(self) -> None:
        """Close the previous task before allocating a new browser."""
        self.close()
        try:
            self._playwright = sync_playwright().start()
            self.browser = self._playwright.chromium.launch(headless=self.headless, handle_sigint=False)
            self.context = self.browser.new_context(viewport={"width": 1280, "height": 720})
            self.context.set_default_timeout(5000)
            self.context.set_default_navigation_timeout(10000)
            self.page = self.context.new_page()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Release resources even after a partial launch or browser disconnect."""
        browser, runtime = self.browser, self._playwright
        if self._tools is not None:
            self._tools.close()
            self._tools = None
        self.browser = self.context = self.page = self._playwright = None
        self.messages.clear()
        try:
            if browser is not None:
                browser.close()
        finally:
            if runtime is not None:
                runtime.stop()
