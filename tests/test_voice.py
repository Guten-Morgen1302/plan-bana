import httpx
import pytest

from maa.voice import EmptyTranscript, MediaFetchError, ProviderError, download_media, transcribe


def client_with(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_download_media_two_hops_with_token():
    seen = []

    def handler(req: httpx.Request):
        seen.append((str(req.url), req.headers.get("authorization")))
        if req.url.path.endswith("/MEDIA1"):
            return httpx.Response(200, json={"url": "https://lookaside.example/blob", "mime_type": "audio/ogg; codecs=opus"})
        return httpx.Response(200, content=b"OggS...")

    async with client_with(handler) as c:
        data, mime = await download_media(c, "MEDIA1", "TOK")
    assert data == b"OggS..." and mime.startswith("audio/ogg")
    assert all(auth == "Bearer TOK" for _, auth in seen) and len(seen) == 2


async def test_download_media_error_is_typed():
    async with client_with(lambda req: httpx.Response(404, json={"error": "gone"})) as c:
        with pytest.raises(MediaFetchError):
            await download_media(c, "MEDIA1", "TOK")


async def test_transcribe_sends_ogg_and_codemix():
    captured = {}

    def handler(req: httpx.Request):
        captured["key"] = req.headers.get("api-subscription-key")
        captured["body"] = req.content
        return httpx.Response(200, json={"transcript": " बेटा दूध भेज दो ", "language_code": "hi-IN", "language_probability": 0.98})

    async with client_with(handler) as c:
        t = await transcribe(c, b"OggS", "audio/ogg; codecs=opus", "KEY")
    assert t.text == "बेटा दूध भेज दो" and t.language == "hi-IN"
    assert captured["key"] == "KEY"
    assert b'name="mode"' in captured["body"] and b"codemix" in captured["body"]
    assert b'filename="voice.ogg"' in captured["body"]
    assert b"keyterms" not in captured["body"]  # they get forced into transcripts


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transcribe_upstream_failures_are_provider_errors(status):
    async with client_with(lambda req: httpx.Response(status, text="busy")) as c:
        with pytest.raises(ProviderError):
            await transcribe(c, b"OggS", "audio/ogg", "KEY")


async def test_empty_transcript():
    async with client_with(lambda req: httpx.Response(200, json={"transcript": "  "})) as c:
        with pytest.raises(EmptyTranscript):
            await transcribe(c, b"OggS", "audio/ogg", "KEY")
