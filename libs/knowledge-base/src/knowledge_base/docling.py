"""Client for the Docling container (docling-serve): PDF, Word, PowerPoint ... -> Markdown."""

import httpx

SUPPORTED = {".pdf", ".docx", ".pptx", ".html", ".md"}


class ConversionError(RuntimeError):
    """The document could not be converted (service down, unreadable file, nothing found)."""


class DoclingClient:
    """Sends a file to `POST /v1/convert/file` and returns the document as Markdown.

    OCR is off: the documents in scope are digital (their text can be read directly), which is
    much faster on a CPU. Images are replaced by placeholders, since only text is searched.
    """

    def __init__(
        self, base_url: str, *, timeout_s: float = 300.0, client: httpx.Client | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._http = client or httpx

    def convert(self, filename: str, content: bytes) -> str:
        try:
            response = self._http.post(
                f"{self.base_url}/v1/convert/file",
                files={"files": (filename, content, "application/octet-stream")},
                data={"to_formats": "md", "do_ocr": "false", "image_export_mode": "placeholder"},
                timeout=httpx.Timeout(10.0, read=self.timeout_s),
            )
        except httpx.HTTPError as exc:
            raise ConversionError(
                f"the document processor is not reachable ({type(exc).__name__})"
            ) from exc
        if response.status_code >= 400:
            raise ConversionError(f"the document processor refused the file: {_detail(response)}")
        body = response.json()
        markdown = ((body.get("document") or {}).get("md_content") or "").strip()
        if body.get("status") not in {"success", "partial_success"} or not markdown:
            errors = "; ".join(str(e)[:150] for e in body.get("errors") or []) or "no text found"
            raise ConversionError(f"conversion of {filename} failed: {errors}")
        return markdown


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", response.text))[:200]
    except ValueError:
        return response.text[:200]
