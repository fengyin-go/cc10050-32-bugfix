"""页面解析模板领域服务：管理商品页HTML选择器与字段映射，负责把原始页面解析为结构化价格字段。

数据链路位置：解析层，消费样本库中的页面样本，按模板选择器提取价格字段，
产出的结构化数据交给规格归一器与解析审计。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.services import relations
from app.store import store


# 升级/核对类动作的不同命名都汇聚到同一领域逻辑，避免页面文案差异导致动作失联。
UPGRADE_ACTIONS = {"升级版本", "升级模板", "版本升级", "升级解析版本", "bump_version"}
IMPACT_ACTIONS = {
    "核对受影响范围",
    "核对影响范围",
    "影响范围核对",
    "核对影响",
    "查看影响范围",
    "check_impact",
}


class PageParserService(BaseModuleService):
    MODULE = "page_parser"
    ENTITY = "解析模板"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["模板名称", "适用站点", "选择器路径", "字段映射", "解析版本"]
    REQUIRED_FIELDS = ["模板名称", "适用站点", "选择器路径"]
    STATUS_ORDER = ["草稿", "已启用", "已废弃"]
    ACTION_RULES = {"启用模板": "已启用", "废弃模板": "已废弃"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：模板解析与字段提取 ----

    def enable_template(self, entry_id: int) -> dict[str, Any]:
        """启用解析模板，校验选择器与字段映射完整性。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        selector = entry.get("选择器路径", "")
        mapping = entry.get("字段映射", "")
        if not selector or "price" not in selector:
            return {"ok": False, "message": "选择器路径缺少价格节点，不能启用"}
        if not mapping:
            return {"ok": False, "message": "字段映射为空，不能启用"}
        entry["status"] = "已启用"
        entry["pending"] = False
        return {"ok": True, "message": f"模板 {entry.get('模板名称', '')} 已启用"}

    def deprecate_template(self, entry_id: int) -> dict[str, Any]:
        """废弃模板并检查是否有站点仍依赖该模板。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "模板不存在"}
        site = entry.get("适用站点", "")
        pending_samples = [
            s for s in store.rows("sample_store")
            if s.get("站点名称") == site and s.get("status") == "已保存"
        ]
        entry["status"] = "已废弃"
        entry["abnormal"] = bool(pending_samples)
        return {
            "ok": True,
            "message": "模板已废弃",
            "blocking_samples": len(pending_samples),
        }

    def parse_sample(self, entry_id: int, sample_id: int) -> dict[str, Any]:
        """用模板解析指定样本，产出结构化字段并更新样本状态。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已启用":
            return {"ok": False, "message": "模板不存在或未启用"}
        sample = store.find("sample_store", sample_id)
        if sample is None:
            return {"ok": False, "message": f"样本 {sample_id} 不存在"}
        if sample.get("站点名称") != entry.get("适用站点"):
            return {"ok": False, "message": "样本站点与模板适用站点不匹配"}
        sample["status"] = "已解析"
        sample["pending"] = False
        return {
            "ok": True,
            "message": "样本解析完成",
            "fields": entry.get("字段映射", ""),
            "version": entry.get("解析版本", ""),
        }

    def report_to_audit(self, entry_id: int, hit_rate: float) -> dict[str, Any]:
        """向解析质量审计上报字段命中率。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "模板不存在"}
        rows = store.rows("parse_audit")
        aid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rate_text = f"{hit_rate:.2%}"
        status = "正常" if hit_rate >= 0.9 else "命中率下降"
        rows.append({
            "id": aid,
            "审计时间": datetime.now(timezone.utc).isoformat(),
            "解析模板": entry.get("模板名称", ""),
            "样本总数": "1",
            "字段命中率": rate_text,
            "失败原因分布": "" if hit_rate >= 0.9 else "选择器失配",
            "status": status,
            "pending": True,
            "abnormal": hit_rate < 0.9,
        })
        return {"ok": True, "message": "命中率已上报审计", "audit_id": aid, "rate": rate_text}

    def route_failures(self, entry_id: int, reason: str) -> dict[str, Any]:
        """解析失败时把样本送入失败隔离区。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "模板不存在"}
        rows = store.rows("failure_quarantine")
        fid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": fid,
            "样本指纹": f"parse-{entry_id}-{fid}",
            "失败阶段": "解析",
            "失败原因": reason,
            "隔离时间": datetime.now(timezone.utc).isoformat(),
            "重试次数": "0",
            "status": "待处理",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "失败样本已隔离", "quarantine_id": fid}

    # ---- 业务扩展：模板版本升级、影响范围核对与重算回执 ----

    def handle_action(self, entry_id: int, action: str, payload: Any | None = None) -> dict[str, Any]:
        """把页面动作名分派到升级/影响核对等富领域逻辑。"""
        if action in UPGRADE_ACTIONS:
            return self.bump_version(entry_id)
        if action in IMPACT_ACTIONS:
            extra = {}
            if payload is not None:
                extra = getattr(payload, "model_extra", None) or {}
            return self.check_impact(entry_id, **{k: v for k, v in extra.items() if k in {"create_receipt_flag"}})
        result = super().handle_action(entry_id, action, payload)
        # 启用/废弃等动作也保持与后端状态一致的返回。
        return result

    def _next_version(self, current: str) -> str:
        raw = str(current or "v1").strip()
        digits = raw.lstrip("vV")
        try:
            return f"v{int(digits or 1) + 1}"
        except ValueError:
            return f"{raw}-next"

    def check_impact(self, entry_id: int, *, create_receipt_flag: bool = True) -> dict[str, Any]:
        """核对模板（最近一次升级前版本）实际影响哪些基线与样本。

        只识别通过“站点 + 模板版本 + 样本引用”真实消费该版本的基线，
        同规格但属于其它站点的基线不会进入影响范围。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "模板不存在", "affected_baselines": []}
        from_version = str(
            entry.get("上一版本")
            or entry.get("last_bumped_from")
            or entry.get("解析版本")
            or "v1"
        )
        to_version = relations.template_version(entry)
        # 若还没升级过，核对的就是当前版本的消费方。
        if not entry.get("上一版本"):
            from_version = to_version
        consumers = relations.find_consumers(entry, from_version)
        south_before = {
            str(b.get("id")): b.get("status")
            for b in store.rows("price_baseline")
            if relations.baseline_site(b) not in (relations.template_site(entry), "")
        }
        receipt = None
        if create_receipt_flag:
            receipt = relations.create_receipt(
                kind="影响核对",
                template=entry,
                from_version=from_version,
                to_version=to_version,
                consumers=consumers,
                message=(
                    f"核对完成：模板「{entry.get('模板名称', '')}」{from_version} "
                    f"共有 {len(consumers)} 笔实际消费基线需要关注"
                ),
            )
            entry["last_receipt_id"] = receipt["id"]
            entry["最近回执"] = receipt["回执编号"]
            for item in consumers:
                item["baseline"]["last_receipt_id"] = receipt["id"]
                item["baseline"]["最近回执"] = receipt["回执编号"]
        return {
            "ok": True,
            "message": (
                f"核对受影响范围完成：{len(consumers)} 笔基线实际消费模板版本 {from_version}"
            ),
            "entry": entry,
            "action": "核对受影响范围",
            "from_version": from_version,
            "to_version": to_version,
            "version": to_version,
            "receipt": receipt,
            "receipt_id": receipt["id"] if receipt else entry.get("last_receipt_id"),
            "baseline_id": receipt["baseline_id"] if receipt else None,
            "affected_count": len(consumers),
            "affected_baseline_ids": [item["baseline"].get("id") for item in consumers],
            "affected_baselines": receipt["affected_baselines"] if receipt else [],
            "causing_samples": receipt["causing_samples"] if receipt else [],
            "south_status_before": south_before,
        }

    def bump_version(self, entry_id: int) -> dict[str, Any]:
        """升级模板解析版本，只把实际消费旧版本的基线纳入影响范围。

        - 已生效的真实消费基线 → 待重算；
        - 已有一笔旧版重算在途（计算中）的基线保留其重算上下文，不打断，
          由值班员选择“继续旧的重算”或“按新版本重新确认”；
        - 其它站点（例如只用南站样本的基线）状态完全不动。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "模板不存在"}
        from_version = relations.template_version(entry)
        to_version = self._next_version(from_version)
        entry["解析版本"] = to_version
        entry["上一版本"] = from_version
        entry["last_bumped_from"] = from_version
        entry["bumped_at"] = relations.now_iso()

        consumers = relations.find_consumers(entry, from_version)
        moved: list[int] = []
        in_flight: list[int] = []
        for item in consumers:
            base = item["baseline"]
            prior_status = base.get("status")
            if prior_status == "计算中":
                in_flight.append(int(base.get("id", 0)))
            # 固定本笔重算依据的旧版本，供“继续旧算”复现当时生效价；
            # 即便状态被统一置为待重算，在途上下文与样本锚点也不丢失。
            base.setdefault("消费模板id", entry.get("id"))
            base["消费模板"] = entry.get("模板名称", "")
            base.setdefault("消费模板版本", from_version)
            base["升级后模板版本"] = to_version
            base["prior_status"] = prior_status
            base["in_flight"] = prior_status == "计算中" or bool(base.get("in_flight"))
            base["version_conflict"] = base["in_flight"]
            base["版本冲突提示"] = (
                f"模板已由{from_version}升级到{to_version}，本笔重算已固定按{from_version}继续，"
                "不会混入新版本样本"
                if base["in_flight"]
                else ""
            )
            # 真实消费该旧版本的基线统一进入待重算；在途那笔仍可“继续旧的重算”。
            base["status"] = "待重算"
            base["pending"] = True
            moved.append(int(base.get("id", 0)))

        untouched_sites = sorted({
            relations.baseline_site(b)
            for b in store.rows("price_baseline")
            if b not in [item["baseline"] for item in consumers]
            and relations.baseline_site(b)
        })
        receipt = relations.create_receipt(
            kind="升级影响回执",
            template=entry,
            from_version=from_version,
            to_version=to_version,
            consumers=consumers,
            message=(
                f"模板已由{from_version}升级到{to_version}；"
                f"{len(consumers)} 笔实际消费基线纳入影响范围，"
                f"其中在途重算 {len(in_flight)} 笔，其它站点基线不受影响"
            ),
            extra={
                "moved_to_recompute": moved,
                "in_flight_baseline_ids": in_flight,
                "untouched_sites": untouched_sites,
            },
        )
        entry["last_receipt_id"] = receipt["id"]
        entry["最近回执"] = receipt["回执编号"]
        for item in consumers:
            item["baseline"]["last_receipt_id"] = receipt["id"]
            item["baseline"]["最近回执"] = receipt["回执编号"]

        return {
            "ok": True,
            "message": f"解析版本已升级到 {to_version}，影响范围已核对并生成回执 {receipt['回执编号']}",
            "entry": entry,
            "action": "升级版本",
            "version": to_version,
            "from_version": from_version,
            "to_version": to_version,
            "receipt": receipt,
            "receipt_id": receipt["id"],
            "baseline_id": receipt["baseline_id"],
            "affected_count": len(consumers),
            "affected_baseline_ids": receipt["affected_baseline_ids"],
            "affected_baselines": receipt["affected_baselines"],
            "causing_samples": receipt["causing_samples"],
            "moved_to_recompute": moved,
            "in_flight_baseline_ids": in_flight,
            "untouched_sites": untouched_sites,
        }
