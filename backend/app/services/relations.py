"""模板 — 样本 — 基线关系识别与重算回执。

升级页面解析模板时，只能影响“确实消费了该模板某个版本”的基线。
关系链固定为：

    解析模板(站点 + 版本) -> 页面样本(独立来源引用 + 模板版本 + 解析价格)
                          -> 价格基线(通过样本引用/商品+规格+站点关联消费)

同规格但不同站点的样本必须按站点隔离，并持有各自独立的 ``来源引用``，
这样北站模板升级才不会顺着“同规格”把只用南站样本的基线误判为受影响。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from app.store import store


# ---------------------------------------------------------------------------
# 基础取值工具
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    # 容忍 "¥45.00" / "45元" / "￥29" 这类展示文本。
    cleaned = ""
    for ch in raw:
        if ch.isdigit() or ch in ".-":
            cleaned += ch
        elif cleaned:
            break
    try:
        return float(cleaned) if cleaned not in ("", "-", ".") else None
    except ValueError:
        return None


def price_number(value: float) -> int | float:
    """整数价格序列化为 int，避免 ``45.0`` 与断言里的 ``45`` 不一致。"""
    rounded = round(float(value) + 0.0, 2)
    if abs(rounded - round(rounded)) < 1e-9:
        return int(round(rounded))
    return rounded


def extract_price_from_html(html: str) -> float | None:
    """从原始 HTML 摘要中提取价格。

    优先识别货币符号/“价格/到手价”引导的数字；否则取最后一个数字
    （规格里的 500g、商品编号等通常出现在真实价格之前）。
    """
    import re

    if not html:
        return None
    guided = re.findall(
        r"(?:¥|￥|\$|价格|到手价|成交价|支付价|price)[^0-9]{0,12}([0-9]+(?:\.[0-9]+)?)",
        html,
        flags=re.IGNORECASE,
    )
    for token in reversed(guided):
        value = as_number(token)
        if value is not None and value > 0:
            return value
    numbers = re.findall(r"[0-9]+(?:\.[0-9]+)?", html)
    for token in reversed(numbers):
        value = as_number(token)
        if value is not None and value > 0:
            return value
    return None


def sample_price(sample: dict[str, Any]) -> float | None:
    """样本在“当时生效口径”下可用于基线计算的价格。

    优先取确认时锁定的生效价格，其次取该模板版本解析出的价格，
    最后退回原始 HTML 摘要里的价格数字。
    """
    for key in ("生效价格", "确认价格", "解析价格", "成交价格", "价格", "price"):
        number = as_number(sample.get(key))
        if number is not None and number > 0:
            return number
    html = text(sample, "原始HTML摘要")
    return extract_price_from_html(html)


def mean_price(samples: Iterable[dict[str, Any]]) -> float | None:
    prices = [p for p in (sample_price(s) for s in samples) if p is not None]
    if not prices:
        return None
    return sum(prices) / len(prices)


# ---------------------------------------------------------------------------
# 关系识别
# ---------------------------------------------------------------------------

def template_site(template: dict[str, Any]) -> str:
    return text(template, "适用站点", "站点名称")


def template_version(template: dict[str, Any]) -> str:
    return text(template, "解析版本", "模板版本", "version") or "v1"


def sample_site(sample: dict[str, Any]) -> str:
    return text(sample, "站点名称", "适用站点", "site")


def sample_goods(sample: dict[str, Any]) -> str:
    return text(sample, "商品标识", "goods_id", "goods")


def sample_spec(sample: dict[str, Any]) -> str:
    return text(sample, "归一规格", "规格", "spec")


def sample_version(sample: dict[str, Any]) -> str:
    return text(sample, "模板版本", "解析版本", "template_version") or "v1"


def sample_ref(sample: dict[str, Any]) -> str:
    return text(sample, "来源引用", "source_ref", "样本指纹") or f"sample-{sample.get('id')}"


def sample_template_id(sample: dict[str, Any]) -> int | str | None:
    value = sample.get("模板id", sample.get("模板标识", sample.get("template_id")))
    return value


def sample_matches_template(sample: dict[str, Any], template: dict[str, Any]) -> bool:
    """样本是否由该模板解析。

    显式模板 id/名称优先；没有显式关联时，只要站点一致即视为该站点模板产出，
    但显式指向了其它模板时必须排除。
    """
    site = template_site(template)
    if site and sample_site(sample) and sample_site(sample) != site:
        return False
    tpl_id = template.get("id")
    linked_id = sample_template_id(sample)
    if linked_id not in (None, ""):
        try:
            if tpl_id is not None and int(linked_id) == int(tpl_id):
                return True
        except (TypeError, ValueError):
            if str(linked_id) == str(tpl_id) or str(linked_id) == text(template, "模板编码"):
                return True
        # 显式指向了别的模板。
        tpl_name = text(template, "模板名称")
        linked_name = text(sample, "模板名称")
        if linked_name and tpl_name and linked_name == tpl_name:
            return True
        return False
    tpl_name = text(template, "模板名称")
    linked_name = text(sample, "模板名称")
    if linked_name and tpl_name:
        return linked_name == tpl_name
    return True


def samples_of_version(
    template: dict[str, Any], version: str, *, only_eligible: bool = False
) -> list[dict[str, Any]]:
    """该模板指定版本产出的全部样本（站点 + 模板 + 版本三重隔离）。"""
    result: list[dict[str, Any]] = []
    for sample in store.rows("sample_store"):
        if not sample_matches_template(sample, template):
            continue
        if sample_version(sample) != version:
            continue
        if only_eligible and sample.get("status") in ("已过期",):
            continue
        result.append(sample)
    return result


def baseline_site(baseline: dict[str, Any]) -> str:
    site = text(baseline, "站点名称", "适用站点", "比价站点", "site")
    if site:
        return site
    # 从显式样本引用反推站点。
    for sample in linked_samples(baseline):
        site = sample_site(sample)
        if site:
            return site
    tpl_id = baseline.get("消费模板id", baseline.get("消费模板"))
    if tpl_id not in (None, ""):
        tpl = store.find("page_parser", int(tpl_id)) if str(tpl_id).isdigit() else None
        if tpl is not None:
            return template_site(tpl)
        for tpl in store.rows("page_parser"):
            if text(tpl, "模板名称") == str(tpl_id):
                return template_site(tpl)
    return ""


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        # 容忍 "REF1,REF2" / "REF1、REF2" / JSON 数组字符串。
        if raw.startswith("["):
            import json

            try:
                parsed = json.loads(raw)
                return list(parsed) if isinstance(parsed, list) else []
            except ValueError:
                pass
        for sep in ("，", ",", "、", ";", "；", " "):
            if sep in raw:
                return [part for part in raw.split(sep) if part]
        return [raw]
    return []


def linked_samples(baseline: dict[str, Any]) -> list[dict[str, Any]]:
    """基线通过样本 id / 来源引用显式关联到的样本。"""
    ids = {str(v) for v in _as_list(baseline.get("样本ids", baseline.get("sample_ids")))}
    refs = {str(v) for v in _as_list(baseline.get("样本引用", baseline.get("sample_refs")))}
    result: list[dict[str, Any]] = []
    for sample in store.rows("sample_store"):
        sid = str(sample.get("id", ""))
        if sid in ids:
            result.append(sample)
            continue
        if refs:
            candidates = {
                sample_ref(sample),
                text(sample, "来源引用", "source_ref"),
                text(sample, "样本指纹"),
            }
            if refs & candidates:
                result.append(sample)
    return result


def baseline_pinned_version(baseline: dict[str, Any]) -> str:
    return text(
        baseline,
        "消费模板版本",
        "basis_version",
        "计算依据版本",
        "模板版本",
    )


def find_consumers(
    template: dict[str, Any], version: str
) -> list[dict[str, Any]]:
    """找出实际消费该模板 ``version`` 的基线及导致其需要重算的样本。

    返回 ``[{"baseline": row, "samples": [...], "reason": ...}]``，
    顺序与 ``price_baseline`` 表内 id 一致，保证回执稳定。
    """
    site = template_site(template)
    version_samples = samples_of_version(template, version)
    version_sample_ids = {int(s["id"]) for s in version_samples if s.get("id") is not None}
    version_refs = {sample_ref(s) for s in version_samples}

    consumers: list[dict[str, Any]] = []
    for baseline in store.rows("price_baseline"):
        reasons: list[str] = []
        causing: list[dict[str, Any]] = []

        pinned = baseline_pinned_version(baseline)
        consumed_tpl = baseline.get(
            "消费模板id", baseline.get("消费模板", baseline.get("template_id"))
        )
        tpl_linked = False
        if consumed_tpl not in (None, ""):
            try:
                tpl_linked = int(consumed_tpl) == int(template.get("id"))
            except (TypeError, ValueError):
                tpl_linked = str(consumed_tpl) == text(template, "模板名称") or str(
                    consumed_tpl
                ) == text(template, "模板编码")

        explicit_links = linked_samples(baseline)
        explicit_ids = {int(s["id"]) for s in explicit_links if s.get("id") is not None}

        # 1) 显式声明消费同一模板的同一版本。
        if tpl_linked and (not pinned or pinned == version):
            reasons.append("template-version")
            causing = [
                s for s in explicit_links if not version_samples or s in version_samples
            ] or list(version_samples)

        # 2) 基线显式引用了该版本的样本。
        hit_ids = explicit_ids & version_sample_ids
        if hit_ids or (set(_as_list(baseline.get("样本引用"))) & version_refs):
            reasons.append("sample-ref")
            causing = [
                s
                for s in explicit_links
                if int(s.get("id", -1)) in hit_ids
                or sample_ref(s) in version_refs
            ]
            if not causing:
                causing = [s for s in version_samples if sample_ref(s) in set(
                    _as_list(baseline.get("样本引用"))
                )]

        # 3) 没有显式引用时，按 站点 + 商品 + 规格 的样本关联识别真实消费。
        if not reasons:
            b_site = baseline_site(baseline)
            b_goods = text(baseline, "商品标识", "goods_id")
            b_spec = text(baseline, "归一规格", "spec")
            if site and b_site and b_site != site:
                continue
            if pinned and pinned != version:
                continue
            related = []
            for sample in version_samples:
                if site and sample_site(sample) and b_site and sample_site(sample) != b_site:
                    continue
                if b_goods and sample_goods(sample) and sample_goods(sample) != b_goods:
                    continue
                if b_spec and sample_spec(sample) and sample_spec(sample) != b_spec:
                    continue
                related.append(sample)
            if related and (not site or not b_site or b_site == site):
                reasons.append("goods-spec-site")
                causing = related

        if reasons:
            # 去重并按 id 排序，保证回执里的“肇事样本”可回查。
            uniq = {int(s["id"]): s for s in causing if s.get("id") is not None}
            consumers.append(
                {
                    "baseline": baseline,
                    "samples": [uniq[k] for k in sorted(uniq)],
                    "reason": "+".join(reasons),
                }
            )
    consumers.sort(key=lambda item: int(item["baseline"].get("id", 0)))
    return consumers


# ---------------------------------------------------------------------------
# 重算回执（独立账本，不污染 20 个业务模块的概览统计）
# ---------------------------------------------------------------------------

_receipts: list[dict[str, Any]] = []
_receipt_seq = 0


def _public_sample(sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": sample.get("id"),
        "来源引用": sample_ref(sample),
        "站点名称": sample_site(sample),
        "模板版本": sample_version(sample),
        "解析价格": sample.get("解析价格", sample_price(sample)),
        "生效价格": sample.get("生效价格", sample_price(sample)),
        "status": sample.get("status"),
        "样本指纹": sample.get("样本指纹", ""),
    }


def _public_baseline(baseline: dict[str, Any], samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": baseline.get("id"),
        "基线编号": baseline.get("基线编号", ""),
        "商品标识": baseline.get("商品标识", ""),
        "归一规格": baseline.get("归一规格", ""),
        "站点名称": baseline_site(baseline),
        "status": baseline.get("status"),
        "基线价格": baseline.get("基线价格"),
        "消费模板版本": baseline_pinned_version(baseline),
        "sample_ids": [s.get("id") for s in samples],
        "sample_refs": [sample_ref(s) for s in samples],
        "samples": [_public_sample(s) for s in samples],
    }


def create_receipt(
    *,
    kind: str,
    template: dict[str, Any] | None,
    from_version: str,
    to_version: str,
    consumers: list[dict[str, Any]],
    message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    global _receipt_seq
    _receipt_seq += 1
    rid = _receipt_seq
    affected = [
        _public_baseline(item["baseline"], item["samples"]) for item in consumers
    ]
    flat_samples: dict[int, dict[str, Any]] = {}
    for item in consumers:
        for sample in item["samples"]:
            if sample.get("id") is not None:
                flat_samples[int(sample["id"])] = _public_sample(sample)
    primary_id = affected[0]["id"] if affected else None
    receipt: dict[str, Any] = {
        "id": rid,
        "receipt_id": rid,
        "回执编号": f"HZ-{1000 + rid}",
        "kind": kind,
        "类型": kind,
        "message": message,
        "created_at": now_iso(),
        "模板id": template.get("id") if template else None,
        "模板名称": text(template, "模板名称") if template else "",
        "站点名称": template_site(template) if template else "",
        "from_version": from_version,
        "to_version": to_version,
        "fromVersion": from_version,
        "toVersion": to_version,
        "baseline_id": primary_id,
        "primary_baseline_id": primary_id,
        "affected_count": len(affected),
        "affected_baseline_ids": [row["id"] for row in affected],
        "affected_baselines": affected,
        # 兼容不同命名习惯的调用方。
        "affected": affected,
        "impacted_baselines": affected,
        "baselines": affected,
        "causing_samples": [flat_samples[k] for k in sorted(flat_samples)],
        "sample_ids": sorted(flat_samples),
    }
    if extra:
        receipt.update(extra)
    _receipts.append(receipt)
    return receipt


def list_receipts() -> list[dict[str, Any]]:
    return list(reversed(_receipts))


def get_receipt(identifier: str | int) -> dict[str, Any] | None:
    raw = str(identifier).strip()
    for receipt in _receipts:
        if str(receipt["id"]) == raw or receipt["回执编号"] == raw:
            return receipt
        if raw.isdigit() and int(receipt["id"]) == int(raw):
            return receipt
    return None
