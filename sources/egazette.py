from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag
from playwright.sync_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError

from models import DocumentMetadata, SourceRunResult, SourceStats
from sources.base import BaseSourceCollector, SourceRuntime
from utils.files import utc_now_iso
from utils.http import (
    absolutize_url,
    canonicalize_url,
    classify_document_type,
    clean_text,
    extract_page_title,
    extract_year,
    make_soup,
)


LOGGER = logging.getLogger(__name__)
GAZETTE_ID_RE = re.compile(r"\b[A-Z]{2}-[A-Z]{2}-[EW]-\d{8}-\d{5,}\b")
POSTBACK_RE = re.compile(r"__doPostBack\('([^']+)','([^']*)'\)")


@dataclass(frozen=True, slots=True)
class HomepageAction:
    label: str
    selector: str


@dataclass(slots=True)
class PageRowCandidate:
    title: str
    gazette_id: str
    year: int | None
    document_type: str
    document_url: str
    parent_page_url: str
    page_title: str
    anchor_text: str
    download_selector: str | None


@dataclass(frozen=True, slots=True)
class NextAction:
    selector: str | None = None
    event_target: str | None = None
    event_argument: str | None = None


class EGazetteCollector(BaseSourceCollector):
    source_name = "egazette"
    home_url = "https://egazette.gov.in/"
    homepage_actions = (
        HomepageAction(label="Bills & Acts", selector="text=Bills & Acts"),
        HomepageAction(label="Land Acquisition", selector="text=Land Acquisition"),
        HomepageAction(label="Recruitment Rules", selector="text=Recruitment Rules"),
        HomepageAction(label="Recent Extra Ordinary", selector="#lnk_Extra_All"),
        HomepageAction(label="Recent Weekly", selector="#lnk_Week_All"),
        HomepageAction(label="Search Gazette", selector="text=Search Gazette"),
        HomepageAction(label="Gazette Directory", selector="text=Gazette Directory"),
    )
    safe_path_hints = (
        "default.aspx",
        "recentuploads.aspx",
        "searchmenu.aspx",
        "searchcategory.aspx",
        "gazettedirectory.aspx",
        "gazette.aspx",
    )
    relevant_text_hints = (
        "gazette",
        "acts",
        "bills",
        "recruitment",
        "land acquisition",
        "extra ordinary",
        "weekly",
        "part",
        "section",
        "search",
        "directory",
        "view all",
    )

    def discover(self, runtime: SourceRuntime) -> SourceRunResult:
        if runtime.browser is None:
            raise RuntimeError("eGazette collection requires Playwright browser support.")

        stats = SourceStats(source=self.source_name)
        records: list[DocumentMetadata] = []
        seen_documents: set[str] = set()
        crawl_timestamp = utc_now_iso()
        page = runtime.browser.new_page()

        try:
            runtime.browser.safe_goto(page, self.home_url)
            records.extend(
                self._collect_current_listing(
                    page,
                    runtime=runtime,
                    stats=stats,
                    page_label="Homepage",
                    crawl_timestamp=crawl_timestamp,
                    seen_documents=seen_documents,
                )
            )
        finally:
            try:
                page.close()
            except PlaywrightError:
                pass

        for action in self.homepage_actions:
            allowed_entrypoints = {item.strip().lower() for item in runtime.settings.egazette.entrypoints if item.strip()}
            if allowed_entrypoints and action.label.lower() not in allowed_entrypoints:
                continue
            action_page = runtime.browser.new_page()
            try:
                runtime.browser.safe_goto(action_page, self.home_url)
                if not self._activate_homepage_action(action_page, action, runtime):
                    continue
                runtime.logger.info("[%s] Visiting entry point: %s -> %s", self.source_name, action.label, action_page.url)
                records.extend(
                    self._collect_action_cluster(
                        action_page,
                        runtime=runtime,
                        stats=stats,
                        action_label=action.label,
                        crawl_timestamp=crawl_timestamp,
                        seen_documents=seen_documents,
                    )
                )
            finally:
                try:
                    action_page.close()
                except PlaywrightError:
                    pass

        return SourceRunResult(source=self.source_name, records=records, stats=stats)

    def _activate_homepage_action(
        self,
        page: Page,
        action: HomepageAction,
        runtime: SourceRuntime,
    ) -> bool:
        locator = page.locator(action.selector).first
        previous_url = page.url
        previous_title = page.title()
        try:
            if locator.count() == 0:
                runtime.logger.warning("[%s] Home action not found: %s", self.source_name, action.label)
                return False
            locator.click()
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except PlaywrightTimeoutError:
                pass
            runtime.browser.wait_for_readiness(page)
            return page.url != previous_url or page.title() != previous_title
        except PlaywrightTimeoutError:
            runtime.logger.warning("[%s] Timed out opening %s", self.source_name, action.label)
        except PlaywrightError as exc:
            runtime.logger.warning("[%s] Failed to open %s: %s", self.source_name, action.label, exc)
        return False

    def _collect_action_cluster(
        self,
        page: Page,
        *,
        runtime: SourceRuntime,
        stats: SourceStats,
        action_label: str,
        crawl_timestamp: str,
        seen_documents: set[str],
    ) -> list[DocumentMetadata]:
        html = page.content()
        if self._is_listing_page(html):
            return self._collect_current_listing(
                page,
                runtime=runtime,
                stats=stats,
                page_label=action_label,
                crawl_timestamp=crawl_timestamp,
                seen_documents=seen_documents,
            )

        collected: list[DocumentMetadata] = []
        follow_links = self._extract_safe_links(page.url, html)
        for follow_url in follow_links[: runtime.settings.egazette.max_follow_links_per_entrypoint]:
            try:
                runtime.browser.safe_goto(page, follow_url)
            except PlaywrightError as exc:
                runtime.logger.warning("[%s] Failed to follow %s: %s", self.source_name, follow_url, exc)
                stats.register_failure()
                continue
            collected.extend(
                self._collect_current_listing(
                    page,
                    runtime=runtime,
                    stats=stats,
                    page_label=action_label,
                    crawl_timestamp=crawl_timestamp,
                    seen_documents=seen_documents,
                )
            )
        return collected

    def _collect_current_listing(
        self,
        page: Page,
        *,
        runtime: SourceRuntime,
        stats: SourceStats,
        page_label: str,
        crawl_timestamp: str,
        seen_documents: set[str],
    ) -> list[DocumentMetadata]:
        collected: list[DocumentMetadata] = []
        seen_page_keys: set[str] = set()

        for _ in range(runtime.settings.egazette.max_listing_pages):
            current_page_key = canonicalize_url(page.url, source=self.source_name)
            if current_page_key in seen_page_keys:
                break
            seen_page_keys.add(current_page_key)

            runtime.browser.wait_for_readiness(page)
            html = page.content()
            stats.register_page()
            runtime.logger.info("[%s] Parsing listing page: %s", self.source_name, page.url)

            if not self._is_listing_page(html):
                break

            for record in self._extract_records_from_listing_page(
                page,
                html,
                runtime=runtime,
                page_label=page_label,
                crawl_timestamp=crawl_timestamp,
            ):
                record_key = canonicalize_url(record.document_url or record.pdf_url, source=self.source_name)
                if record_key in seen_documents:
                    continue
                seen_documents.add(record_key)
                collected.append(record)
                stats.register_document(has_pdf=bool(record.pdf_url))

            next_action = self._find_next_action(html)
            if next_action is None:
                break
            if not self._trigger_next_action(page, next_action, runtime):
                break

        return collected

    def _extract_records_from_listing_page(
        self,
        page: Page,
        html: str,
        *,
        runtime: SourceRuntime,
        page_label: str,
        crawl_timestamp: str,
    ) -> list[DocumentMetadata]:
        soup = make_soup(html)
        page_title = extract_page_title(soup) or page_label
        candidates: list[PageRowCandidate] = []
        seen_candidates: set[str] = set()

        if "default.aspx" in page.url.lower():
            for candidate in self._extract_homepage_candidates(
                soup,
                page_url=page.url,
                page_title=page_title,
            ):
                candidate_key = candidate.download_selector or candidate.document_url or candidate.gazette_id
                if candidate_key in seen_candidates:
                    continue
                seen_candidates.add(candidate_key)
                candidates.append(candidate)
        else:
            for table in soup.find_all("table"):
                headers = self._extract_table_headers(table)
                if not headers or "gazette id" not in headers or "download" not in headers:
                    continue
                if not GAZETTE_ID_RE.search(clean_text(table.get_text(" ", strip=True))):
                    continue
                section_title = page_label
                for row in self._table_direct_rows(table):
                    candidate = self._extract_row_candidate(
                        row,
                        headers=headers,
                        page_url=page.url,
                        page_title=page_title,
                        section_title=section_title,
                    )
                    if candidate is None:
                        continue
                    candidate_key = candidate.download_selector or candidate.document_url or candidate.gazette_id
                    if candidate_key in seen_candidates:
                        continue
                    seen_candidates.add(candidate_key)
                    candidates.append(candidate)

        records: list[DocumentMetadata] = []
        for candidate in candidates[: runtime.settings.egazette.max_rows_per_page]:
            pdf_url = self._capture_download_url(page, candidate.download_selector, runtime) if candidate.download_selector else ""
            document_url = candidate.document_url or canonicalize_url(pdf_url, source=self.source_name)
            if not document_url and not pdf_url:
                continue
            if not document_url:
                document_url = canonicalize_url(pdf_url, source=self.source_name)

            records.append(
                self.make_record(
                    title=candidate.title,
                    document_type=candidate.document_type,
                    year=candidate.year,
                    document_url=document_url,
                    pdf_url=pdf_url,
                    parent_page_url=candidate.parent_page_url,
                    page_title=candidate.page_title,
                    anchor_text=candidate.anchor_text,
                    crawl_timestamp=crawl_timestamp,
                )
            )

        return records

    def _extract_table_headers(self, table: BeautifulSoup) -> list[str]:
        header_row = table.find("tr")
        if header_row is None:
            return []
        return [clean_text(cell.get_text(" ", strip=True)).lower() for cell in header_row.find_all("th")]

    def _table_direct_rows(self, table: BeautifulSoup) -> list[Tag]:
        tbody = table.find("tbody")
        container = tbody or table
        return [row for row in container.find_all("tr", recursive=False)]

    def _extract_homepage_candidates(
        self,
        soup: BeautifulSoup,
        *,
        page_url: str,
        page_title: str,
    ) -> list[PageRowCandidate]:
        candidates: list[PageRowCandidate] = []
        patterns = (
            ("rpt_Extra", "Recent Extra Ordinary", "E"),
            ("rpt_Week", "Recent Weekly", "W"),
        )

        for prefix, section_title, suffix in patterns:
            for control in soup.find_all(id=re.compile(fr"^{prefix}_ImgDownLoad{suffix}_\d+$")):
                index = control["id"].rsplit("_", 1)[-1]
                subject = self._homepage_field_text(soup, prefix, f"lbl_Subject{suffix}_{index}")
                gazette_id = self._homepage_field_text(soup, prefix, f"lbl_UGID{'Extra' if prefix == 'rpt_Extra' else 'Weekly'}_{index}")
                publish_date = self._homepage_field_text(soup, prefix, f"lbl_Date{suffix}_{index}")
                ministry = self._homepage_field_text(soup, prefix, f"lbl_Ministry{suffix}_{index}")
                title = subject or ministry or section_title
                candidates.append(
                    PageRowCandidate(
                        title=title,
                        gazette_id=gazette_id,
                        year=extract_year(publish_date, title),
                        document_type=classify_document_type(f"{section_title} {title}", fallback="Gazette"),
                        document_url="",
                        parent_page_url=canonicalize_url(page_url, source=self.source_name),
                        page_title=page_title,
                        anchor_text=title,
                        download_selector=self._control_selector(control),
                    )
                )
        return candidates

    def _homepage_field_text(self, soup: BeautifulSoup, prefix: str, tail: str) -> str:
        node = soup.find(id=f"{prefix}_{tail}")
        return clean_text(node.get_text(" ", strip=True)) if node else ""

    def _extract_row_candidate(
        self,
        row: Tag,
        *,
        headers: list[str],
        page_url: str,
        page_title: str,
        section_title: str,
    ) -> PageRowCandidate | None:
        row_text = clean_text(row.get_text(" ", strip=True))
        if not row_text:
            return None

        gazette_id_match = GAZETTE_ID_RE.search(row_text)
        download_control = row.find(
            lambda tag: isinstance(tag, Tag)
            and tag.name in {"input", "button", "a"}
            and (
                "download-pdf" in clean_text(tag.get("src", ""))
                or "pdf_icon" in clean_text(tag.get("src", "")).lower()
                or "imgdownload" in clean_text(tag.get("id", "")).lower()
                or "imgdownload" in clean_text(tag.get("name", "")).lower()
                or "imgbtndownload" in clean_text(tag.get("id", "")).lower()
                or "imgbtndownload" in clean_text(tag.get("name", "")).lower()
                or "download" in clean_text(tag.get("id", "")).lower()
            )
        )

        if gazette_id_match is None and download_control is None:
            return None

        cells = row.find_all("td")
        values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        mapping = self._map_row_values(headers, values)

        title = (
            mapping.get("subject")
            or mapping.get("title")
            or self._best_title_from_values(values, gazette_id_match.group(0) if gazette_id_match else "")
            or section_title
        )
        document_type = classify_document_type(f"{section_title} {title}", fallback="Gazette")
        year = extract_year(mapping.get("publish date", ""), mapping.get("issue date", ""), title, section_title)
        document_url = self._extract_document_url(row, page_url)
        download_selector = self._control_selector(download_control) if download_control else None

        return PageRowCandidate(
            title=title,
            gazette_id=gazette_id_match.group(0) if gazette_id_match else "",
            year=year,
            document_type=document_type,
            document_url=document_url,
            parent_page_url=canonicalize_url(page_url, source=self.source_name),
            page_title=page_title,
            anchor_text=title,
            download_selector=download_selector,
        )

    def _map_row_values(self, headers: list[str], values: list[str]) -> dict[str, str]:
        if not headers or len(headers) != len(values):
            return {}
        return {header: value for header, value in zip(headers, values, strict=False)}

    def _best_title_from_values(self, values: list[str], gazette_id: str) -> str:
        filtered: list[str] = []
        for value in values:
            lowered = value.lower()
            if not value or value == gazette_id:
                continue
            if re.fullmatch(r"\d+\.", value):
                continue
            if extract_year(value) and len(value) <= 12:
                continue
            if "mb" in lowered and value.endswith("MB"):
                continue
            filtered.append(value)
        return filtered[0] if filtered else gazette_id

    def _extract_document_url(self, row: Tag, page_url: str) -> str:
        anchor = row.find("a", href=True)
        if anchor is not None:
            href = anchor["href"]
            if "javascript:" not in href.lower():
                return canonicalize_url(absolutize_url(page_url, href), source=self.source_name)
            derived = self._parse_open_window(href)
            if derived:
                return canonicalize_url(absolutize_url(page_url, derived), source=self.source_name)

        for element in row.find_all(attrs={"onclick": True}):
            derived = self._parse_open_window(clean_text(element.get("onclick")))
            if derived:
                return canonicalize_url(absolutize_url(page_url, derived), source=self.source_name)
        return ""

    def _parse_open_window(self, script_text: str) -> str | None:
        match = re.search(r"openWindow\(([^,]+),\s*([^)]+)\)", script_text)
        if not match:
            return None
        row_id = match.group(1).strip(" '\"")
        tab_id = match.group(2).strip(" '\"")
        return f"Gazette.aspx?RowID={row_id}&TabID={tab_id}"

    def _control_selector(self, control: Tag) -> str | None:
        control_id = control.get("id")
        if control_id:
            return f"#{control_id}"
        control_name = control.get("name")
        if control_name:
            escaped_name = control_name.replace("\\", "\\\\").replace('"', '\\"')
            return f'[name="{escaped_name}"]'
        return None

    def _capture_download_url(
        self,
        page: Page,
        selector: str | None,
        runtime: SourceRuntime,
    ) -> str:
        if not selector or not runtime.settings.egazette.capture_download_urls:
            return ""

        current_url = page.url
        locator = page.locator(selector).first
        try:
            if locator.count() == 0:
                return ""
            try:
                with page.expect_popup(timeout=runtime.settings.browser.download_timeout_ms) as popup_info:
                    locator.click(force=True)
                popup = popup_info.value
                try:
                    popup.wait_for_load_state("domcontentloaded")
                    runtime.browser.wait_for_readiness(popup)
                    iframe = popup.locator("iframe").first
                    if iframe.count() > 0:
                        iframe_src = iframe.get_attribute("src") or ""
                        if iframe_src:
                            time.sleep(runtime.settings.polite_delay_seconds)
                            return absolutize_url(popup.url, iframe_src)
                    if popup.url.lower().endswith(".pdf"):
                        time.sleep(runtime.settings.polite_delay_seconds)
                        return popup.url
                finally:
                    popup.close()
            except PlaywrightTimeoutError:
                pass

            with page.expect_download(timeout=runtime.settings.browser.download_timeout_ms) as download_info:
                locator.click(force=True, no_wait_after=True)
            download = download_info.value
            pdf_url = getattr(download, "url", "") or ""
            try:
                download.delete()
            except PlaywrightError:
                pass
            time.sleep(runtime.settings.polite_delay_seconds)
            return pdf_url
        except PlaywrightTimeoutError:
            runtime.logger.debug("[%s] Download capture timed out for selector %s", self.source_name, selector)
            return ""
        except PlaywrightError as exc:
            runtime.logger.debug("[%s] Download capture failed for selector %s: %s", self.source_name, selector, exc)
            return ""
        finally:
            if page.url != current_url:
                try:
                    page.go_back(wait_until="domcontentloaded")
                    runtime.browser.wait_for_readiness(page)
                except PlaywrightError:
                    pass

    def _find_next_action(self, html: str) -> NextAction | None:
        soup = make_soup(html)

        for anchor in soup.find_all("a", href=True):
            label = clean_text(anchor.get_text(" ", strip=True))
            href = anchor["href"]
            if "next" not in label.lower() and "page$next" not in href.lower():
                continue
            postback_match = POSTBACK_RE.search(href)
            if postback_match:
                return NextAction(
                    event_target=postback_match.group(1),
                    event_argument=postback_match.group(2),
                )
            selector = self._control_selector(anchor)
            if selector:
                return NextAction(selector=selector)

        for element in soup.find_all(attrs={"id": True}):
            identifier = clean_text(element.get("id")).lower()
            if "next" in identifier and element.name in {"input", "button", "a"}:
                selector = self._control_selector(element)
                if selector:
                    return NextAction(selector=selector)
        return None

    def _trigger_next_action(self, page: Page, next_action: NextAction, runtime: SourceRuntime) -> bool:
        try:
            if next_action.event_target:
                with page.expect_navigation(wait_until="domcontentloaded"):
                    page.evaluate(
                        "(payload) => __doPostBack(payload.target, payload.argument)",
                        {"target": next_action.event_target, "argument": next_action.event_argument or ""},
                    )
                runtime.browser.wait_for_readiness(page)
                return True

            if next_action.selector:
                locator = page.locator(next_action.selector).first
                if locator.count() == 0:
                    return False
                with page.expect_navigation(wait_until="domcontentloaded"):
                    locator.click()
                runtime.browser.wait_for_readiness(page)
                return True
        except PlaywrightTimeoutError:
            runtime.logger.warning("[%s] Timed out while moving to next eGazette listing page", self.source_name)
        except PlaywrightError as exc:
            runtime.logger.warning("[%s] Failed to paginate eGazette listing: %s", self.source_name, exc)
        return False

    def _find_section_title(self, table: BeautifulSoup) -> str:
        for previous in table.find_all_previous(limit=10):
            if not isinstance(previous, Tag):
                continue
            text = clean_text(previous.get_text(" ", strip=True))
            if not text:
                continue
            lowered = text.lower()
            if any(hint in lowered for hint in ("recent", "gazette", "acts", "recruitment", "land acquisition")):
                return text
        return ""

    def _extract_safe_links(self, page_url: str, html: str) -> list[str]:
        soup = make_soup(html)
        discovered: list[str] = []
        seen: set[str] = set()

        for anchor in soup.find_all("a", href=True):
            href = clean_text(anchor["href"])
            if not href or href.lower().startswith("javascript:"):
                continue
            absolute_url = absolutize_url(page_url, href)
            canonical_url = canonicalize_url(absolute_url, source=self.source_name)
            if canonical_url in seen:
                continue
            if not self._is_safe_follow_url(absolute_url):
                continue
            link_text = clean_text(anchor.get_text(" ", strip=True))
            if not self._is_relevant_follow_link(link_text, absolute_url):
                continue
            seen.add(canonical_url)
            discovered.append(absolute_url)

        return discovered

    def _is_safe_follow_url(self, url: str) -> bool:
        normalized = url.lower()
        if "egazette.gov.in" not in normalized:
            return False
        return any(hint in normalized for hint in self.safe_path_hints)

    def _is_relevant_follow_link(self, text: str, url: str) -> bool:
        haystack = f"{text} {url}".lower()
        return any(hint in haystack for hint in self.relevant_text_hints)

    def _is_listing_page(self, html: str) -> bool:
        lowered = html.lower()
        return bool(GAZETTE_ID_RE.search(html)) and (
            "download-pdf" in lowered
            or "imgdownload" in lowered
            or "imgbtndownload" in lowered
            or "pdf_icon" in lowered
        )
