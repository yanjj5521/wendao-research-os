from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from services.integral import (
    BINARY_SUFFIXES,
    IntegralError,
    integrate,
    parse_binary,
    parse_text,
    render_svg,
)
from web.runtime import File, Form, Request, UploadFile, context, templates

app = APIRouter()

METHOD_LABELS = {
    "auto": "自动（优先 Simpson）",
    "trapezoid": "复合梯形法",
    "simpson": "Simpson 1/3",
}


def _render(request: Request, **extra):
    payload = {
        "form": {},
        "error": "",
        "dataset": None,
        "result": None,
        "svg": "",
        "preview": [],
        "method_labels": METHOD_LABELS,
    }
    payload.update(extra)
    return templates.TemplateResponse(
        request=request, name="integral.html", context=context(request, "integral", **payload)
    )


@app.get("/integral", name="integral_page")
def integral_page(request: Request):
    return _render(request)


@app.post("/integral", name="integral_calculate")
async def integral_calculate(
    request: Request,
    file: UploadFile = File(...),
    data_format: str = Form("auto"),
    dtype: str = Form("float64"),
    endian: str = Form("little"),
    layout: str = Form("xy"),
    method: str = Form("auto"),
):
    filename = file.filename or "upload"
    form = {
        "data_format": data_format,
        "dtype": dtype,
        "endian": endian,
        "layout": layout,
        "method": method,
    }
    raw = await file.read()
    if not raw:
        return _render(request, form=form, filename=filename, error="文件为空，请重新选择。")
    suffix = Path(filename).suffix.lower()
    binary = data_format == "binary" or (
        data_format == "auto" and suffix in BINARY_SUFFIXES
    )
    try:
        dataset = (
            parse_binary(raw, label=filename, dtype=dtype, endian=endian, layout=layout)
            if binary
            else parse_text(raw, label=filename)
        )
        result = integrate(dataset, method)
    except IntegralError as exc:
        return _render(request, form=form, filename=filename, error=str(exc))
    except (ValueError, ArithmeticError) as exc:
        return _render(
            request, form=form, filename=filename, error=f"无法计算：{exc}"
        )
    preview = list(zip(dataset.x, dataset.y))[:8]
    return _render(
        request,
        form=form,
        filename=filename,
        dataset=dataset,
        result=result,
        svg=render_svg(dataset),
        preview=preview,
    )
