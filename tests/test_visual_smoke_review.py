from __future__ import annotations

import struct
import zlib

from scripts.review_visual_smoke_artifacts import (
    ScreenshotExpectation,
    inspect_png,
    render_markdown,
    review_visual_smoke_artifacts,
)


def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(payload, crc)
    return struct.pack('>I', len(payload)) + chunk_type + payload + struct.pack('>I', crc & 0xFFFFFFFF)


def _write_rgb_png(path, *, width: int, height: int, varied: bool) -> None:
    raw_rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            if varied:
                row.extend(((x * 31 + y * 17) % 256, (x * 11 + y * 19) % 256, (x * 7 + y * 5) % 256))
            else:
                row.extend((240, 240, 240))
        raw_rows.append(b'\x00' + bytes(row))
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b'\x89PNG\r\n\x1a\n'
        + _chunk(b'IHDR', ihdr)
        + _chunk(b'IDAT', zlib.compress(b''.join(raw_rows)))
        + _chunk(b'IEND', b'')
    )


def _expectations() -> tuple[ScreenshotExpectation, ...]:
    return (
        ScreenshotExpectation('desktop-shell.png', width=8, min_height=6, max_height=6),
        ScreenshotExpectation('laptop-shell.png', width=7, min_height=6, max_height=6),
        ScreenshotExpectation('mobile-full.png', width=5, min_height=7),
        ScreenshotExpectation('mobile-narrow.png', width=4, min_height=7),
        ScreenshotExpectation('tablet-landscape.png', width=6, min_height=5, max_height=5),
        ScreenshotExpectation('tablet-portrait.png', width=5, min_height=8, max_height=8),
    )


def test_inspect_png_reports_dimensions_and_pixel_variation(tmp_path):
    screenshot = tmp_path / 'desktop-shell.png'
    _write_rgb_png(screenshot, width=8, height=6, varied=True)

    result = inspect_png(screenshot)

    assert result.width == 8
    assert result.height == 6
    assert result.color_type == 'rgb'
    assert result.byte_count > 0
    assert result.unique_color_count > 1
    assert result.different_pixel_ratio > 0.5


def test_review_visual_smoke_artifacts_passes_expected_screenshots(tmp_path):
    for expectation in _expectations():
        _write_rgb_png(tmp_path / expectation.file_name, width=expectation.width, height=expectation.min_height, varied=True)

    review = review_visual_smoke_artifacts(
        tmp_path,
        expectations=_expectations(),
        minimum_file_bytes=1,
        minimum_unique_colors=2,
        minimum_different_pixel_ratio=0.1,
        reviewed_at='2026-06-19T00:05:00+00:00',
    )
    markdown = render_markdown(review)

    assert review['status'] == 'passed'
    assert review['failures'] == []
    assert '- Status: passed' in markdown
    assert '- Screenshots: 6/6' in markdown
    assert '| desktop-shell.png | passed | 8x6 |' in markdown


def test_review_visual_smoke_artifacts_fails_blank_or_missing_screenshots(tmp_path):
    _write_rgb_png(tmp_path / 'desktop-shell.png', width=8, height=6, varied=False)

    review = review_visual_smoke_artifacts(
        tmp_path,
        expectations=_expectations(),
        minimum_file_bytes=1,
        minimum_unique_colors=2,
        minimum_different_pixel_ratio=0.1,
        reviewed_at='2026-06-19T00:05:00+00:00',
    )

    assert review['status'] == 'failed'
    assert any('desktop-shell.png' in failure and 'unique color count' in failure for failure in review['failures'])
    assert any('laptop-shell.png' in failure and 'missing expected screenshot' in failure for failure in review['failures'])
