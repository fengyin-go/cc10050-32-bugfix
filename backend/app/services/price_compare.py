"""比价计算器领域服务：跨站点计算商品价差比例与最低价站点，负责比价批次的调度与失败重试。

数据链路位置：比价计算层，消费价格基线库的生效基线与各站点采集价格，
产出的比价结果供变价告警与比价导出通道消费。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.services import relations
from app.store import store


RUN_COMPARE_ACTIONS = {"开始计算", "执行比价", "启动比价", "开始比价", "执行计算", "run_compare"}
RETRY_COMPARE_ACTIONS = {"重试计算", "重试比价", "retry"}


class PriceCompareService(BaseModuleService):
    MODULE = "price_compare"
    ENTITY = "比价结果"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["比价批次", "商品标识", "最低价站点", "价差比例", "计算时间"]
    FILTER_FIELDS = ["比价站点", "站点名称"]
    REQUIRED_FIELDS = ["比价批次", "商品标识", "最低价站点"]
    STATUS_ORDER = ["排队中", "已计算", "计算失败"]
    ACTION_RULES = {"开始计算": "已计算", "重试计算": "计算失败"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：跨站点比价与价差计算 ----

    # ---- 动作分派 ----

    def handle_action(self, entry_id: int, action: str, payload: Any | None = None) -> dict[str, Any]:
        if action in RUN_COMPARE_ACTIONS:
            return self.run_compare(entry_id)
        if action in RETRY_COMPARE_ACTIONS:
            retried = self.retry_failed(entry_id)
            if not retried.get("ok"):
                return {"ok": False, "message": retried.get("message", "重试失败")}
            # 重新排队后立即按后端实际依据执行一次，状态由计算结果决定。
            return self.run_compare(entry_id)
        return super().handle_action(entry_id, action, payload)

    # ---- 业务扩展：跨站点比价与价差计算 ----

    def _compare_site(self, entry: dict[str, Any]) -> str:
        site = relations.text(entry, "比价站点", "站点名称", "目标站点")
        if site:
            return site
        batch = str(entry.get("比价批次", ""))
        for name in ("北站", "南站"):
            if name in batch or name in str(entry.get("最低价站点", "")):
                return name
        return ""

    def _baseline_for_compare(
        self, goods_id: str, site: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        same_goods = [
            b for b in store.rows("price_baseline")
            if b.get("商品标识") == goods_id
        ]
        if site:
            scoped = [b for b in same_goods if relations.baseline_site(b) == site]
            effective = next((b for b in scoped if b.get("status") == "已生效"), None)
            if effective is not None:
                return effective, same_goods
            # 没有生效的站点基线时，不要错拿其它站点基线顶替。
            return None, same_goods
        effective = next((b for b in same_goods if b.get("status") == "已生效"), None)
        return effective, same_goods

    def run_compare(self, entry_id: int) -> dict[str, Any]:
        """执行比价：按 商品 + 站点 找到生效基线及其样本依据，计算价差。

        关键不变量：南站基线仍生效时，南站比价必须成功并沿用 29 的依据；
        其它站点（北站）是否在重算不影响南站入口。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        goods_id = entry.get("商品标识", "")
        site = self._compare_site(entry)
        baseline, same_goods = self._baseline_for_compare(goods_id, site)
        if baseline is None:
            blocking = next(
                (
                    b for b in same_goods
                    if (not site or relations.baseline_site(b) == site)
                    and b.get("status") in ("待重算", "计算中")
                ),
                None,
            )
            reason = (
                f"商品 {goods_id} 的{site or ''}基线正在{blocking.get('status')}，暂无可采用的生效依据"
                if blocking is not None
                else f"商品 {goods_id} 没有{site or ''}生效基线，计算失败"
            )
            entry["status"] = "计算失败"
            entry["abnormal"] = True
            entry["pending"] = False
            entry["计算时间"] = datetime.now(timezone.utc).isoformat()
            return {
                "ok": False,
                "message": reason,
                "entry": entry,
                "site": site,
                "baseline_id": blocking.get("id") if blocking else None,
            }

        site_price = relations.as_number(baseline.get("基线价格"))
        # 汇总同商品各站点生效基线，给出最低价站点与价差。
        effective_prices: list[tuple[str, float, dict[str, Any]]] = []
        for other in same_goods:
            if other.get("status") != "已生效":
                continue
            price = relations.as_number(other.get("基线价格"))
            other_site = relations.baseline_site(other) or str(other.get("站点名称", ""))
            if price is not None and other_site:
                effective_prices.append((other_site, price, other))
        lowest_site = site
        lowest_price = site_price
        if effective_prices:
            lowest_site, lowest_price, _ = min(effective_prices, key=lambda item: item[1])
        ratio_text = "0.0%"
        if site_price is not None and lowest_price not in (None, 0):
            ratio = (site_price - lowest_price) / lowest_price
            ratio_text = f"{ratio * 100:.1f}%" if abs(ratio) > 1e-9 else "0.0%"

        sample_refs = list(baseline.get("样本引用", []) or [])
        basis_version = relations.baseline_pinned_version(baseline)
        entry["价差比例"] = ratio_text
        entry["最低价站点"] = lowest_site or entry.get("最低价站点", "")
        entry["计算时间"] = datetime.now(timezone.utc).isoformat()
        entry["status"] = "已计算"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["baseline_id"] = baseline.get("id")
        entry["依据版本"] = basis_version
        entry["依据价格"] = site_price
        entry["样本引用"] = sample_refs
        entry["计算依据"] = (
            f"{site}基线#{baseline.get('id')} {basis_version or ''} "
            f"价格{site_price} 样本{len(sample_refs)}个"
        ).strip()
        return {
            "ok": True,
            "message": (
                f"{site + '比价' if site else '比价计算'}完成：采用基线 "
                f"#{baseline.get('id')} 的生效价格 {relations.price_number(site_price or 0)}，"
                f"最低价站点 {entry['最低价站点']}"
            ),
            "entry": entry,
            "site": site,
            "baseline_id": baseline.get("id"),
            "baseline_price": relations.price_number(site_price or 0),
            "site_price": relations.price_number(site_price or 0),
            "lowest_site": entry["最低价站点"],
            "lowest_price": relations.price_number(lowest_price or 0),
            "price_ratio": ratio_text,
            "basis_version": basis_version,
            "sample_refs": sample_refs,
        }

    def retry_failed(self, entry_id: int) -> dict[str, Any]:
        """重试失败批次，先把失败上下文写入隔离区。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "计算失败":
            return {"ok": False, "message": "批次不存在或非失败状态"}
        rows = store.rows("failure_quarantine")
        fid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": fid,
            "样本指纹": f"compare-{entry_id}",
            "失败阶段": "计算",
            "失败原因": f"批次 {entry.get('比价批次', '')} 缺基线",
            "隔离时间": datetime.now(timezone.utc).isoformat(),
            "重试次数": "1",
            "status": "重试中",
            "pending": True,
            "abnormal": False,
        })
        entry["status"] = "排队中"
        entry["pending"] = True
        entry["abnormal"] = False
        return {"ok": True, "message": "失败上下文已隔离，批次重新排队", "quarantine_id": fid}

    def emit_change_history(self, entry_id: int, old_price: float, new_price: float) -> dict[str, Any]:
        """把价差结果写入变价历史流水。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "批次不存在"}
        if old_price <= 0:
            return {"ok": False, "message": "旧价格无效"}
        ratio = (new_price - old_price) / old_price
        rows = store.rows("change_history")
        hid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": hid,
            "商品标识": entry.get("商品标识", ""),
            "旧价格": f"{old_price:.2f}",
            "新价格": f"{new_price:.2f}",
            "变化幅度": f"{ratio:.2%}",
            "发现时间": datetime.now(timezone.utc).isoformat(),
            "status": "待核实",
            "pending": True,
            "abnormal": ratio < 0,
        })
        return {"ok": True, "message": "变价记录已写入", "history_id": hid, "ratio": f"{ratio:.2%}"}

    def notify_alert(self, entry_id: int) -> dict[str, Any]:
        """检查生效告警规则并触发变价通知。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已计算":
            return {"ok": False, "message": "批次不存在或未完成计算"}
        goods_id = entry.get("商品标识", "")
        rules = [
            r for r in store.rows("price_alert")
            if r.get("status") == "已生效" and r.get("监控商品") == goods_id
        ]
        if not rules:
            return {"ok": True, "message": "无生效告警规则", "triggered": 0}
        return {
            "ok": True,
            "message": "已触发变价告警",
            "triggered": len(rules),
            "channels": [r.get("通知渠道", "") for r in rules],
        }

    def push_to_export(self, entry_id: int) -> dict[str, Any]:
        """把已计算批次推送到比价导出通道。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已计算":
            return {"ok": False, "message": "批次不存在或未完成计算"}
        rows = store.rows("price_export")
        eid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": eid,
            "任务名称": f"导出批次 {entry.get('比价批次', '')}",
            "商品范围": entry.get("商品标识", ""),
            "导出格式": "CSV",
            "时间范围": str(entry.get("计算时间", ""))[:10],
            "生成状态": "排队中",
            "status": "排队中",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "导出任务已创建", "export_id": eid}

    def batch_status(self, batch: str) -> dict[str, Any]:
        """汇总同批次所有比价记录的状态。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("比价批次") == batch]
        if not rows:
            return {"ok": False, "message": f"批次 {batch} 不存在"}
        computed = sum(1 for r in rows if r.get("status") == "已计算")
        return {
            "ok": True,
            "message": "批次状态汇总完成",
            "total": len(rows),
            "computed": computed,
            "pending": len(rows) - computed,
        }
