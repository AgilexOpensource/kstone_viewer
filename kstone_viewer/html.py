"""Tiny inline-styled HTML fragments for status cards (theme-neutral colors that read in light and dark mode)."""

from __future__ import annotations

import html as _html
import math
from typing import Any, Iterable, Sequence

TONES = {
    "ok": ("#0f9d58", "rgba(15,157,88,0.14)"),
    "warn": ("#e8a200", "rgba(232,162,0,0.16)"),
    "bad": ("#e5484d", "rgba(229,72,77,0.15)"),
    "info": ("#3b82f6", "rgba(59,130,246,0.14)"),
    "muted": ("#8a8f98", "rgba(138,143,152,0.14)"),
    "accent": ("#7c5cff", "rgba(124,92,255,0.14)"),
}
FONT = "font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;"
# tabular digits keep live numbers from changing width (and shaking the layout) as they update
MONO = "font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums;"
ONE_LINE = "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"


def esc(value: Any) -> str:
    return _html.escape(str(value), quote=True)


def fmt(value: Any, digits: int = 3) -> str:
    if isinstance(value, bool):
        return "✓" if value else "–"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "∞" if value > 0 else ("-∞" if value < 0 else "NaN")
        return f"{round(value, digits) + 0.0:.{digits}f}"
    if isinstance(value, (tuple, list)):
        return "(" + ", ".join(fmt(v, digits) for v in value) + ")"
    return str(value)


def signed(value: Any, digits: int = 3) -> str:
    """Signed fixed-point text; values that round to zero print as +0.000, never -0.000."""
    return f"{round(float(value), digits) + 0.0:+.{digits}f}"


def badge(text: str, tone: str = "muted") -> str:
    color, background = TONES.get(tone, TONES["muted"])
    return (f"<span style=\"display:inline-block;padding:1px 8px;margin:1px 3px 1px 0;border-radius:999px;"
            f"font-size:11px;font-weight:600;line-height:18px;color:{color};background:{background};"
            f"border:1px solid {color}33;white-space:nowrap\">{esc(text)}</span>")


def stat(label: str, value: str, tone: str | None = None, sub: str = "") -> str:
    color = TONES[tone][0] if tone else "inherit"
    # the sub line is always present and single-line so the cell height never depends on the numbers
    sub_html = f"<div style=\"font-size:10px;line-height:14px;height:14px;opacity:.6;{MONO}{ONE_LINE}\">" \
               f"{esc(sub) if sub else '&nbsp;'}</div>"
    return (f"<div style=\"padding:6px 8px;border-radius:8px;background:rgba(127,127,127,0.08);"
            f"border:1px solid rgba(127,127,127,0.16);min-width:0\">"
            f"<div style=\"font-size:10px;letter-spacing:.04em;text-transform:uppercase;opacity:.62;"
            f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis\">{esc(label)}</div>"
            f"<div style=\"font-size:15px;font-weight:650;color:{color};{MONO}white-space:nowrap;overflow:hidden;"
            f"text-overflow:ellipsis\">{esc(value)}</div>{sub_html}</div>")


def badge_row(badges: Iterable[str]) -> str:
    """Single fixed-height line of badges; overflow is clipped instead of wrapping onto a new line."""
    return (f"<div style=\"height:22px;{ONE_LINE}margin-bottom:4px\">{''.join(badges)}</div>")


def grid(cells: Iterable[str], columns: int = 3) -> str:
    return (f"<div style=\"display:grid;grid-template-columns:repeat({columns},minmax(0,1fr));gap:6px;"
            f"margin:2px 0 6px 0\">{''.join(cells)}</div>")


def card(body: str, *, title: str = "", tone: str | None = None, right: str = "") -> str:
    border = TONES[tone][0] if tone else "rgba(127,127,127,0.25)"
    header = ""
    if title or right:
        header = (f"<div style=\"display:flex;justify-content:space-between;align-items:center;margin-bottom:6px\">"
                  f"<span style=\"font-weight:650;font-size:13px\">{esc(title)}</span><span>{right}</span></div>")
    return (f"<div style=\"{FONT}font-size:12px;padding:8px 10px;margin:2px 0 6px 0;border-radius:10px;"
            f"border:1px solid rgba(127,127,127,0.18);border-left:3px solid {border};"
            f"background:rgba(127,127,127,0.04)\">{header}{body}</div>")


_WARNING_SVG = ("<svg width=\"20\" height=\"20\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" "
                "stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M10.3 3.9 1.8 18a2 2 0 0 0 "
                "1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z\"/><line x1=\"12\" y1=\"9\" x2=\"12\" y2=\"13\"/>"
                "<line x1=\"12\" y1=\"17\" x2=\"12.01\" y2=\"17\"/></svg>")


def dialog(headline: str, text: str, *, tone: str = "warn") -> str:
    """Body of a confirmation modal: a soft icon + headline and one sentence."""
    color, background = TONES.get(tone, TONES["warn"])
    return (f"<div style=\"{FONT}font-size:13px;line-height:1.6;padding:2px 2px 8px 2px\">"
            f"<div style=\"display:flex;gap:12px;align-items:center\">"
            f"<div style=\"flex:none;width:36px;height:36px;border-radius:50%;display:flex;align-items:center;"
            f"justify-content:center;color:{color};background:{background}\">{_WARNING_SVG}</div>"
            f"<div style=\"font-size:15px;font-weight:600\">{esc(headline)}</div></div>"
            f"<div style=\"margin:10px 0 0 48px;opacity:.75\">{esc(text)}</div></div>")


def rows(pairs: Sequence[tuple[str, str]], *, mono: bool = True) -> str:
    style = MONO if mono else ""
    body = "".join(f"<tr><td style=\"opacity:.65;padding:1px 8px 1px 0;white-space:nowrap;vertical-align:top\">"
                   f"{esc(label)}</td><td style=\"{style}padding:1px 0;word-break:break-all\">{value}</td></tr>"
                   for label, value in pairs)
    return f"<table style=\"width:100%;border-collapse:collapse;font-size:12px\">{body}</table>"


def bar(fraction: float, tone: str = "info", *, marker: float | None = None, height: int = 6) -> str:
    fraction = 0.0 if not math.isfinite(fraction) else min(max(fraction, 0.0), 1.0)
    color = TONES.get(tone, TONES["info"])[0]
    mark = ""
    if marker is not None and math.isfinite(marker):
        mark = (f"<div style=\"position:absolute;left:{min(max(marker, 0.0), 1.0) * 100:.1f}%;top:-2px;width:2px;"
                f"height:{height + 4}px;background:currentColor;opacity:.55\"></div>")
    return (f"<div style=\"position:relative;height:{height}px;border-radius:{height}px;"
            f"background:rgba(127,127,127,0.18)\"><div style=\"width:{fraction * 100:.1f}%;height:100%;"
            f"border-radius:{height}px;background:{color}\"></div>{mark}</div>")


def table(header: Sequence[str], body: Sequence[Sequence[str]], *, align: Sequence[str] | None = None,
          max_height: int | None = None, widths: Sequence[str] | None = None) -> str:
    align = align or ["left"] * len(header)
    head = "".join(f"<th style=\"text-align:{a};padding:3px 6px;font-size:10px;letter-spacing:.04em;"
                   f"text-transform:uppercase;opacity:.6;position:sticky;top:0;"
                   f"background:var(--mantine-color-body,#fff)\">{esc(h)}</th>" for h, a in zip(header, align))
    clip = ONE_LINE if widths else "white-space:nowrap;"
    lines = "".join("<tr style=\"border-top:1px solid rgba(127,127,127,0.14)\">" +
                    "".join(f"<td style=\"text-align:{a};padding:3px 6px;{MONO}{clip}\">{cell}</td>"
                            for cell, a in zip(line, align)) + "</tr>" for line in body)
    scroll = f"max-height:{max_height}px;overflow-y:auto;" if max_height else ""
    # fixed column widths: live values then never re-flow the columns
    cols = "<colgroup>" + "".join(f"<col style=\"width:{w}\">" for w in widths) + "</colgroup>" if widths else ""
    layout = "table-layout:fixed;" if widths else ""
    return (f"<div style=\"{FONT}{scroll}border-radius:8px;border:1px solid rgba(127,127,127,0.16)\">"
            f"<table style=\"width:100%;{layout}border-collapse:collapse;font-size:11.5px\">{cols}<thead><tr>{head}</tr></thead>"
            f"<tbody>{lines}</tbody></table></div>")


def note(text: str, tone: str = "info", *, one_line: bool = False) -> str:
    color, background = TONES.get(tone, TONES["info"])
    clip = ONE_LINE if one_line else ""
    return (f"<div style=\"{FONT}font-size:12px;padding:6px 10px;margin:2px 0 6px 0;border-radius:8px;{clip}"
            f"color:{color};background:{background};border:1px solid {color}40\">{text}</div>")
