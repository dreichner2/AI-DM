#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import struct
import sys
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable

try:
    from scripts.render_rc_issue_evidence import latest_visual_smoke_dir
except ModuleNotFoundError:  # pragma: no cover - exercised when run as a script path
    from render_rc_issue_evidence import latest_visual_smoke_dir  # type: ignore[no-redef]


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_REPORT = REPO_ROOT / 'tmp' / 'release' / 'visual-smoke-review.md'
DEFAULT_JSON_REPORT = REPO_ROOT / 'tmp' / 'release' / 'visual-smoke-review.json'
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


@dataclass(frozen=True)
class ScreenshotExpectation:
    file_name: str
    width: int
    min_height: int
    max_height: int | None = None


@dataclass(frozen=True)
class PngInspection:
    width: int
    height: int
    color_type: str
    byte_count: int
    unique_color_count: int
    unique_color_count_capped: bool
    different_pixel_ratio: float


EXPECTED_SCREENSHOTS: tuple[ScreenshotExpectation, ...] = (
    ScreenshotExpectation('desktop-shell.png', width=1440, min_height=900, max_height=900),
    ScreenshotExpectation('laptop-shell.png', width=1280, min_height=800, max_height=800),
    ScreenshotExpectation('tablet-landscape.png', width=1024, min_height=768, max_height=768),
    ScreenshotExpectation('tablet-portrait.png', width=768, min_height=1024, max_height=1024),
    ScreenshotExpectation('mobile-full.png', width=390, min_height=844, max_height=844),
    ScreenshotExpectation('mobile-narrow.png', width=360, min_height=800, max_height=800),
)

COLOR_CHANNELS = {
    0: ('grayscale', 1),
    2: ('rgb', 3),
    3: ('indexed', 1),
    4: ('grayscale-alpha', 2),
    6: ('rgba', 4),
}


def _resolve_repo_path(path: pathlib.Path) -> pathlib.Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _relative_or_absolute(path: pathlib.Path | str) -> str:
    candidate = pathlib.Path(path)
    try:
        return str(candidate.relative_to(REPO_ROOT))
    except ValueError:
        return str(candidate)


def _paeth_predictor(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    left_distance = abs(estimate - left)
    up_distance = abs(estimate - up)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= up_distance and left_distance <= upper_left_distance:
        return left
    if up_distance <= upper_left_distance:
        return up
    return upper_left


def _decode_png_scanlines(
    *,
    compressed: bytes,
    width: int,
    height: int,
    channels: int,
) -> Iterable[bytes]:
    row_bytes = width * channels
    raw = zlib.decompress(compressed)
    expected_bytes = (row_bytes + 1) * height
    if len(raw) != expected_bytes:
        raise ValueError(f'decoded PNG byte count {len(raw)} did not match expected {expected_bytes}')

    previous = bytearray(row_bytes)
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset:offset + row_bytes])
        offset += row_bytes

        if filter_type == 0:
            pass
        elif filter_type == 1:
            for index in range(row_bytes):
                left = row[index - channels] if index >= channels else 0
                row[index] = (row[index] + left) & 0xFF
        elif filter_type == 2:
            for index in range(row_bytes):
                row[index] = (row[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(row_bytes):
                left = row[index - channels] if index >= channels else 0
                up = previous[index]
                row[index] = (row[index] + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            for index in range(row_bytes):
                left = row[index - channels] if index >= channels else 0
                up = previous[index]
                upper_left = previous[index - channels] if index >= channels else 0
                row[index] = (row[index] + _paeth_predictor(left, up, upper_left)) & 0xFF
        else:
            raise ValueError(f'unsupported PNG filter type {filter_type}')

        previous = row
        yield bytes(row)


def inspect_png(path: pathlib.Path, *, unique_color_limit: int = 512) -> PngInspection:
    png_path = _resolve_repo_path(path)
    data = png_path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError('not a PNG file')

    offset = len(PNG_SIGNATURE)
    width = 0
    height = 0
    bit_depth = 0
    color_type = 0
    compression_method = 0
    filter_method = 0
    interlace_method = 0
    idat_chunks: list[bytes] = []

    while offset + 8 <= len(data):
        chunk_length = struct.unpack('>I', data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_length
        if chunk_end + 4 > len(data):
            raise ValueError('truncated PNG chunk')
        chunk_data = data[chunk_start:chunk_end]
        offset = chunk_end + 4

        if chunk_type == b'IHDR':
            if chunk_length != 13:
                raise ValueError('invalid PNG IHDR length')
            (
                width,
                height,
                bit_depth,
                color_type,
                compression_method,
                filter_method,
                interlace_method,
            ) = struct.unpack('>IIBBBBB', chunk_data)
        elif chunk_type == b'IDAT':
            idat_chunks.append(chunk_data)
        elif chunk_type == b'IEND':
            break

    if not width or not height:
        raise ValueError('missing PNG IHDR')
    if bit_depth != 8:
        raise ValueError(f'unsupported PNG bit depth {bit_depth}')
    if color_type not in COLOR_CHANNELS:
        raise ValueError(f'unsupported PNG color type {color_type}')
    if compression_method != 0 or filter_method != 0:
        raise ValueError('unsupported PNG compression or filter method')
    if interlace_method != 0:
        raise ValueError('interlaced PNGs are not supported')
    if not idat_chunks:
        raise ValueError('missing PNG image data')

    color_type_label, channels = COLOR_CHANNELS[color_type]
    unique_colors: set[bytes] = set()
    unique_color_count_capped = False
    first_pixel: bytes | None = None
    different_pixels = 0
    pixel_count = width * height

    for row in _decode_png_scanlines(
        compressed=b''.join(idat_chunks),
        width=width,
        height=height,
        channels=channels,
    ):
        for index in range(0, len(row), channels):
            pixel = row[index:index + channels]
            if first_pixel is None:
                first_pixel = pixel
            elif pixel != first_pixel:
                different_pixels += 1
            if len(unique_colors) < unique_color_limit:
                unique_colors.add(pixel)
            else:
                unique_color_count_capped = True

    return PngInspection(
        width=width,
        height=height,
        color_type=color_type_label,
        byte_count=png_path.stat().st_size,
        unique_color_count=len(unique_colors),
        unique_color_count_capped=unique_color_count_capped,
        different_pixel_ratio=different_pixels / pixel_count if pixel_count else 0.0,
    )


def _review_screenshot(
    visual_smoke_dir: pathlib.Path,
    expectation: ScreenshotExpectation,
    *,
    minimum_file_bytes: int,
    minimum_unique_colors: int,
    minimum_different_pixel_ratio: float,
) -> dict[str, Any]:
    screenshot_path = visual_smoke_dir / expectation.file_name
    if not screenshot_path.exists():
        return {
            'file': expectation.file_name,
            'path': str(screenshot_path),
            'status': 'missing',
            'notes': ['missing expected screenshot'],
        }

    notes: list[str] = []
    try:
        inspection = inspect_png(screenshot_path)
    except (OSError, ValueError, zlib.error) as exc:
        return {
            'file': expectation.file_name,
            'path': str(screenshot_path),
            'status': 'failed',
            'notes': [str(exc)],
        }

    if inspection.width != expectation.width:
        notes.append(f'width {inspection.width}px did not match expected {expectation.width}px')
    if inspection.height < expectation.min_height:
        notes.append(f'height {inspection.height}px was below expected minimum {expectation.min_height}px')
    if expectation.max_height is not None and inspection.height > expectation.max_height:
        notes.append(f'height {inspection.height}px exceeded expected maximum {expectation.max_height}px')
    if inspection.byte_count < minimum_file_bytes:
        notes.append(f'file size {inspection.byte_count} bytes was below minimum {minimum_file_bytes} bytes')
    if inspection.unique_color_count < minimum_unique_colors and not inspection.unique_color_count_capped:
        notes.append(
            f'unique color count {inspection.unique_color_count} was below minimum {minimum_unique_colors}'
        )
    if inspection.different_pixel_ratio < minimum_different_pixel_ratio:
        notes.append(
            'different-pixel ratio '
            f'{inspection.different_pixel_ratio:.4f} was below minimum {minimum_different_pixel_ratio:.4f}'
        )

    return {
        'file': expectation.file_name,
        'path': str(screenshot_path),
        'status': 'failed' if notes else 'passed',
        'width': inspection.width,
        'height': inspection.height,
        'color_type': inspection.color_type,
        'byte_count': inspection.byte_count,
        'unique_color_count': inspection.unique_color_count,
        'unique_color_count_capped': inspection.unique_color_count_capped,
        'different_pixel_ratio': inspection.different_pixel_ratio,
        'notes': notes or ['ok'],
    }


def review_visual_smoke_artifacts(
    visual_smoke_dir: pathlib.Path | None = None,
    *,
    expectations: tuple[ScreenshotExpectation, ...] = EXPECTED_SCREENSHOTS,
    minimum_file_bytes: int = 10_000,
    minimum_unique_colors: int = 16,
    minimum_different_pixel_ratio: float = 0.01,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    smoke_dir = _resolve_repo_path(visual_smoke_dir) if visual_smoke_dir is not None else latest_visual_smoke_dir()
    reviewed_at = reviewed_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    if smoke_dir is None:
        return {
            'status': 'missing',
            'reviewed_at': reviewed_at,
            'artifact_dir': '',
            'screenshots': [],
            'failures': ['no visual-smoke artifact directory found'],
        }
    if not smoke_dir.exists() or not smoke_dir.is_dir():
        return {
            'status': 'missing',
            'reviewed_at': reviewed_at,
            'artifact_dir': str(smoke_dir),
            'screenshots': [],
            'failures': [f'visual-smoke artifact directory not found: {smoke_dir}'],
        }

    screenshots = [
        _review_screenshot(
            smoke_dir,
            expectation,
            minimum_file_bytes=minimum_file_bytes,
            minimum_unique_colors=minimum_unique_colors,
            minimum_different_pixel_ratio=minimum_different_pixel_ratio,
        )
        for expectation in expectations
    ]
    failures = [
        f"{screenshot['file']}: {'; '.join(screenshot.get('notes') or [])}"
        for screenshot in screenshots
        if screenshot.get('status') != 'passed'
    ]
    return {
        'status': 'passed' if not failures else 'failed',
        'reviewed_at': reviewed_at,
        'artifact_dir': str(smoke_dir),
        'screenshots': screenshots,
        'failures': failures,
    }


def render_markdown(review: dict[str, Any]) -> str:
    screenshots = review.get('screenshots') or []
    failures = review.get('failures') or []
    rows = [
        '| Screenshot | Status | Dimensions | Bytes | Unique Colors | Different Pixels | Notes |',
        '| --- | --- | ---: | ---: | ---: | ---: | --- |',
    ]
    for screenshot in screenshots:
        dimensions = (
            f"{screenshot.get('width')}x{screenshot.get('height')}"
            if screenshot.get('width') and screenshot.get('height')
            else ''
        )
        unique_colors = str(screenshot.get('unique_color_count') or '')
        if screenshot.get('unique_color_count_capped'):
            unique_colors += '+'
        different_ratio = screenshot.get('different_pixel_ratio')
        rows.append(
            '| '
            + ' | '.join(
                [
                    str(screenshot.get('file') or ''),
                    str(screenshot.get('status') or 'unknown'),
                    dimensions,
                    str(screenshot.get('byte_count') or ''),
                    unique_colors,
                    f'{different_ratio:.4f}' if isinstance(different_ratio, (int, float)) else '',
                    '; '.join(screenshot.get('notes') or []),
                ]
            )
            + ' |'
        )
    if not screenshots:
        rows.append('| None | missing |  |  |  |  | no screenshots reviewed |')

    return '\n'.join(
        [
            '# Visual Smoke Review Evidence',
            '',
            f"- Status: {review.get('status') or 'unknown'}",
            f"- Reviewed: {review.get('reviewed_at') or 'unknown'}",
            f"- Artifact dir: `{_relative_or_absolute(review.get('artifact_dir') or '')}`",
            f"- Screenshots: {sum(1 for screenshot in screenshots if screenshot.get('status') == 'passed')}/{len(screenshots)}",
            '- Failures: ' + ('; '.join(failures) if failures else 'None.'),
            '',
            '## Screenshot Checks',
            '',
            *rows,
            '',
        ]
    )


def write_reports(
    review: dict[str, Any],
    *,
    evidence_report: pathlib.Path,
    json_output: pathlib.Path | None = None,
) -> None:
    report_path = _resolve_repo_path(evidence_report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown(review), encoding='utf-8')
    if json_output is not None:
        json_path = _resolve_repo_path(json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(review, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Review visual-smoke screenshot artifacts for RC evidence.')
    parser.add_argument(
        '--visual-smoke-dir',
        type=pathlib.Path,
        default=None,
        help='Visual-smoke artifact directory. Defaults to the newest tmp/verification_artifacts/visual-smoke run.',
    )
    parser.add_argument(
        '--evidence-report',
        type=pathlib.Path,
        default=DEFAULT_EVIDENCE_REPORT,
        help='Markdown evidence report to write.',
    )
    parser.add_argument(
        '--json-output',
        type=pathlib.Path,
        default=None,
        help='Optional JSON review report to write.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    review = review_visual_smoke_artifacts(args.visual_smoke_dir)
    write_reports(review, evidence_report=args.evidence_report, json_output=args.json_output)
    print(
        '[visual-smoke-review] '
        f"{review.get('status')} for {_relative_or_absolute(review.get('artifact_dir') or '')}; "
        f"evidence written to {_relative_or_absolute(_resolve_repo_path(args.evidence_report))}."
    )
    if args.json_output is not None:
        print(f'[visual-smoke-review] JSON written to {_relative_or_absolute(_resolve_repo_path(args.json_output))}.')
    return 0 if review.get('status') == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
