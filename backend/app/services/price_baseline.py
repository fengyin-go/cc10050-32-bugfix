"""价格基线库领域服务：按滚动采样窗口计算商品规格级基准价格，负责基线生效与重算。

数据链路位置：基准计算层，消费规格归一器的统一规格与货币换算器的统一币种，
产出的基线价格供比价计算器判定价差。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.services import relations
from app.store import store


CONTINUE_OLD_ACTIONS = {
    "继续重算",
    "继续旧的重算",
    "继续旧算",
    "继续这笔重算",
    "继续这笔基线重算",
    "继续重算基线",
    "完成重算",
    "continue_old",
}
RECOMPUTE_ACTIONS = {"重算", "执行重算", "recompute"}
REPRODUCE_OLD_ACTIONS = {
    "复算旧版",
    "旧算复算",
    "按当时生效价格复算",
    "复算旧价格",
    "reproduce_old",
}
CONFIRM_PRICE_ACTIONS = {
    "确认价格",
    "重新确认价格",
    "重新确认",
    "按新版本确认",
    "整理样本并确认价格",
    "重新整理有资格的样本并确认价格",
    "confirm_price",
}
IMPACT_BASELINE_ACTIONS = {"核对受影响范围", "核对影响范围", "影响范围核对"}


class PriceBaselineService(BaseModuleService):
    MODULE = "price_baseline"
    ENTITY = "价格基线"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["商品标识", "归一规格", "基线价格", "采样窗口", "更新时间"]
    FILTER_FIELDS = ["站点名称", "基线编号", "消费模板版本", "basis_version"]
    REQUIRED_FIELDS = ["商品标识", "归一规格", "基线价格"]
    STATUS_ORDER = ["计算中", "已生效", "待重算"]
    ACTION_RULES = {"生效基线": "已生效", "重算基线": "待重算"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：基线计算与滚动窗口 ----

    def activate_baseline(self, entry_id: int) -> dict[str, Any]:
        """生效价格基线，要求归一规格与采样窗口齐备。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        spec = entry.get("归一规格", "")
        window = entry.get("采样窗口", "")
        if not spec:
            return {"ok": False, "message": "缺少归一规格，不能生效"}
        if not window:
            entry["采样窗口"] = "7d"
        price = str(entry.get("基线价格", "") or "")
        try:
            float(price)
        except ValueError:
            return {"ok": False, "message": "基线价格必须是数值"}
        entry["status"] = "已生效"
        entry["pending"] = False
        entry["更新时间"] = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "message": "基线已生效", "spec": spec}

    # ---- 动作分派 ----

    def handle_action(self, entry_id: int, action: str, payload: Any | None = None) -> dict[str, Any]:
        extra = {}
        if payload is not None:
            extra = getattr(payload, "model_extra", None) or {}
        if action in CONTINUE_OLD_ACTIONS:
            return self.continue_old_recompute(entry_id)
        if action in REPRODUCE_OLD_ACTIONS:
            return self.reproduce_old_price(entry_id)
        if action in CONFIRM_PRICE_ACTIONS:
            return self.confirm_price(
                entry_id, target_version=str(extra.get("target_version") or "") or None
            )
        if action == "重算基线" or action in RECOMPUTE_ACTIONS:
            # 页面上的“重算基线”是一笔已有重算的继续：若基线固定在旧版本，
            # 就按旧版样本继续，避免升级后混用两个版本。
            entry = store.find(self.MODULE, entry_id)
            if entry is not None and self._has_pinned_old_run(entry):
                return self.continue_old_recompute(entry_id)
            return self.recompute_baseline(entry_id)
        if action in IMPACT_BASELINE_ACTIONS:
            return self.impact_for_baseline(entry_id)
        return super().handle_action(entry_id, action, payload)

    # ---- 业务扩展：基线计算与滚动窗口 ----

    def _has_pinned_old_run(self, entry: dict[str, Any]) -> bool:
        pinned = relations.baseline_pinned_version(entry)
        if not pinned:
            return False
        tpl = self._template_for_baseline(entry)
        current = relations.template_version(tpl) if tpl else pinned
        return bool(entry.get("in_flight")) or entry.get("status") == "计算中" or pinned != current

    def _template_for_baseline(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        tpl_id = entry.get("消费模板id", entry.get("消费模板"))
        if tpl_id not in (None, "") and str(tpl_id).isdigit():
            found = store.find("page_parser", int(tpl_id))
            if found is not None:
                return found
        site = relations.baseline_site(entry)
        if site:
            enabled = [
                t for t in store.rows("page_parser")
                if relations.template_site(t) == site
            ]
            active = next((t for t in enabled if t.get("status") == "已启用"), None)
            return active or (enabled[0] if enabled else None)
        if tpl_id not in (None, ""):
            return next(
                (t for t in store.rows("page_parser") if t.get("模板名称") == tpl_id), None
            )
        return None

    def _eligible_samples(
        self, entry: dict[str, Any], version: str, *, reorganize: bool = False
    ) -> list[dict[str, Any]]:
        """整理基线在指定模板版本下“有资格”的样本。

        资格：同站点、同商品、同规格、模板版本严格等于 ``version``、未过期。
        默认在基线维护的显式样本引用集合内整理，保证旧算可复算；
        ``reorganize=True``（新版本重新确认）时按关系重新整理全量合格样本，
        不再被旧版本引用集合限制，但仍然严格限定单一版本。
        """
        site = relations.baseline_site(entry)
        goods = relations.text(entry, "商品标识", "goods_id", "goods")
        spec = relations.text(entry, "归一规格", "spec", "规格")
        explicit = relations.linked_samples(entry)
        explicit_ids = {int(s["id"]) for s in explicit if s.get("id") is not None}
        explicit_refs = {relations.sample_ref(s) for s in explicit}

        candidates: list[dict[str, Any]] = []
        for sample in store.rows("sample_store"):
            if sample.get("status") == "已过期":
                continue
            if relations.sample_version(sample) != version:
                continue
            s_site = relations.sample_site(sample)
            if site and s_site and s_site != site:
                continue
            s_goods = relations.sample_goods(sample)
            if goods and s_goods and s_goods != goods:
                continue
            s_spec = relations.sample_spec(sample)
            if spec and s_spec and s_spec != spec:
                continue
            if not reorganize and (explicit_ids or explicit_refs):
                sid = int(sample.get("id", -1))
                if sid not in explicit_ids and relations.sample_ref(sample) not in explicit_refs:
                    continue
            candidates.append(sample)
        return candidates

    def _settle_inflight_samples(self, entry: dict[str, Any], version: str) -> list[int]:
        """“旧样本完成”：把本笔重算引用的在途已保存旧样本置为已解析。"""
        finished: list[int] = []
        for sample in relations.linked_samples(entry):
            if relations.sample_version(sample) != version:
                continue
            if sample.get("status") == "已保存":
                sample["status"] = "已解析"
                sample["pending"] = False
                finished.append(int(sample.get("id", 0)))
        return finished

    def _finish_with_samples(
        self,
        entry: dict[str, Any],
        samples: list[dict[str, Any]],
        *,
        version: str,
        mode: str,
        message_prefix: str,
        receipt_kind: str,
        template: dict[str, Any] | None,
        finished_samples: list[int] | None = None,
    ) -> dict[str, Any]:
        prices = [p for p in (relations.sample_price(s) for s in samples) if p is not None]
        if not prices:
            entry["status"] = "待重算"
            entry["pending"] = True
            return {
                "ok": False,
                "message": f"{message_prefix}失败：版本 {version} 下没有可用的生效价格样本，基线保持待重算",
                "entry": entry,
                "baseline_id": entry.get("id"),
                "basis_version": version,
                "mixed_versions": False,
            }
        value = relations.price_number(sum(prices) / len(prices))
        old_pinned = relations.baseline_pinned_version(entry)
        # 首次落算前锁定“原始版本依据”（样本引用 + 版本 + 当时生效价快照），
        # 之后重新确认新版本只会更新当前依据，旧计算永远可按锚点复算。
        entry.setdefault("原始消费模板版本", old_pinned or version)
        entry.setdefault("原始样本ids", list(entry.get("样本ids", []) or []))
        entry.setdefault("原始样本引用", list(entry.get("样本引用", []) or []))
        previous_snapshot = entry.get("effective_price_snapshot")
        if (
            mode == "confirm-current"
            and old_pinned
            and old_pinned != version
            and isinstance(previous_snapshot, dict)
        ):
            history = entry.setdefault("历史版本依据", [])
            if not any(item.get("version") == old_pinned for item in history if isinstance(item, dict)):
                history.append({
                    "version": old_pinned,
                    "基线价格": entry.get("基线价格"),
                    "样本ids": list(entry.get("样本ids", []) or []),
                    "样本引用": list(entry.get("样本引用", []) or []),
                    "snapshot": previous_snapshot,
                })
        entry["基线价格"] = value
        entry["status"] = "已生效"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["更新时间"] = relations.now_iso()
        entry["采样窗口"] = entry.get("采样窗口") or "7d"
        entry["消费模板版本"] = version
        entry["last_confirmed_version"] = version
        entry["last_confirm_mode"] = mode
        entry["样本ids"] = [s.get("id") for s in samples]
        entry["样本引用"] = [relations.sample_ref(s) for s in samples]
        # 锁定本次确认时的生效价格，后续可用这份快照复算对应版本的结论。
        snapshot = {
            "version": version,
            "confirmed_at": relations.now_iso(),
            "prices": {
                relations.sample_ref(s): relations.sample_price(s) for s in samples
            },
            "mean": value,
        }
        entry["effective_price_snapshot"] = snapshot
        # 常见命名别名，方便不同用例回查“当时的生效价格”。
        entry["price_snapshot"] = snapshot
        entry["effective_prices"] = snapshot["prices"]
        if mode != "confirm-current" or old_pinned == version:
            entry["历史生效价格"] = value
        entry["version_conflict"] = False
        entry["版本冲突提示"] = ""
        entry["in_flight"] = False

        consumers = [{
            "baseline": entry,
            "samples": samples,
            "reason": mode,
        }]
        receipt = relations.create_receipt(
            kind=receipt_kind,
            template=template,
            from_version=version,
            to_version=version,
            consumers=consumers,
            message=(
                f"{message_prefix}：仅采用 {version} 的 {len(samples)} 个有资格样本，"
                f"确认价格 {value}"
            ),
            extra={
                "baseline_id": entry.get("id"),
                "price": value,
                "new_price": value,
                "basis_version": version,
                "mode": mode,
                "finished_samples": finished_samples or [],
            },
        )
        entry["last_receipt_id"] = receipt["id"]
        entry["最近回执"] = receipt["回执编号"]
        return {
            "ok": True,
            "message": f"{message_prefix}完成，依据{version}样本 {len(samples)} 个，价格 {value}",
            "entry": entry,
            "baseline_id": entry.get("id"),
            "new_price": value,
            "price": value,
            "基线价格": value,
            "basis_version": version,
            "template_version": version,
            "mode": mode,
            "mixed_versions": False,
            "samples_used": len(samples),
            "sample_ids": [s.get("id") for s in samples],
            "sample_refs": [relations.sample_ref(s) for s in samples],
            "finished_samples": finished_samples or [],
            "receipt": receipt,
            "receipt_id": receipt["id"],
        }

    def _anchor_version(self, entry: dict[str, Any]) -> str:
        return relations.text(entry, "原始消费模板版本", "old_basis_version") or (
            relations.baseline_pinned_version(entry) or "v1"
        )

    def _anchor_samples(self, entry: dict[str, Any], version: str) -> list[dict[str, Any]]:
        """原始那笔重算固定的样本集合（锚点引用，不随后续重新确认而改变）。"""
        anchor_ids = {
            str(v) for v in relations._as_list(  # noqa: SLF001
                entry.get("原始样本ids", entry.get("样本ids"))
            )
        }
        anchor_refs = {
            str(v) for v in relations._as_list(  # noqa: SLF001
                entry.get("原始样本引用", entry.get("样本引用"))
            )
        }
        result = []
        for sample in store.rows("sample_store"):
            sid = str(sample.get("id", ""))
            if anchor_ids and sid in anchor_ids:
                result.append(sample)
            elif anchor_refs and (
                relations.sample_ref(sample) in anchor_refs
                or relations.text(sample, "来源引用") in anchor_refs
            ):
                result.append(sample)
        if result:
            versioned = [s for s in result if relations.sample_version(s) == version]
            return versioned or result
        # 没有锚点引用时，按站点+商品+规格+版本重新识别。
        return self._eligible_samples(entry, version, reorganize=True)

    def continue_old_recompute(self, entry_id: int) -> dict[str, Any]:
        """继续升级前那笔在途重算：固定使用当时的模板版本与生效价格。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "基线不存在"}
        version = self._anchor_version(entry)
        template = self._template_for_baseline(entry)
        # 旧样本此刻完成解析，但仍属于同一个旧版本，不构成版本混合。
        finished = self._settle_anchor_samples(entry, version)
        samples = self._anchor_samples(entry, version)
        result = self._finish_with_samples(
            entry,
            samples,
            version=version,
            mode="continue-old",
            message_prefix="继续旧版重算",
            receipt_kind="基线重算回执(旧版继续)",
            template=template,
            finished_samples=finished,
        )
        if not result["ok"]:
            return result
        result["old_price"] = result["new_price"]
        result["message"] = (
            f"已继续并完成原重算：按当时生效的{version}价格复算为 {result['new_price']}"
            + (f"，{len(finished)} 个旧样本在过程中完成" if finished else "")
        )
        return result

    def _settle_anchor_samples(self, entry: dict[str, Any], version: str) -> list[int]:
        finished: list[int] = []
        for sample in self._anchor_samples(entry, version):
            if relations.sample_version(sample) != version:
                continue
            if sample.get("status") == "已保存":
                sample["status"] = "已解析"
                sample["pending"] = False
                finished.append(int(sample.get("id", 0)))
        return finished

    def reproduce_old_price(self, entry_id: int) -> dict[str, Any]:
        """只读复算：用当时锁定的生效价格快照重现旧结论，不改变当前确认值。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "基线不存在"}
        version = self._anchor_version(entry)
        snapshot = entry.get("effective_price_snapshot")
        # 若当前快照已经是新版本，从历史依据里找回旧快照。
        old_snapshot: dict[str, Any] | None = None
        if isinstance(snapshot, dict) and snapshot.get("version") == version:
            old_snapshot = snapshot
        for item in entry.get("历史版本依据", []) or []:
            if isinstance(item, dict) and item.get("version") == version:
                old_snapshot = item.get("snapshot") if isinstance(item.get("snapshot"), dict) else None
                if old_snapshot is None and item.get("基线价格") is not None:
                    old_snapshot = {"version": version, "mean": item.get("基线价格"), "prices": {}}
                break
        samples = self._anchor_samples(entry, version)
        mean = relations.mean_price(samples)
        if old_snapshot and old_snapshot.get("mean") is not None:
            old_value = relations.price_number(float(old_snapshot["mean"]))
        elif mean is not None:
            old_value = relations.price_number(mean)
        else:
            return {"ok": False, "message": f"缺少 {version} 旧依据，无法复算", "entry": entry}
        return {
            "ok": True,
            "message": f"旧计算可按当时生效价格复算：{version} -> {old_value}，当前确认价 {entry.get('基线价格')}",
            "entry": entry,
            "baseline_id": entry_id,
            "old_price": old_value,
            "recomputed_old_price": old_value,
            "basis_version": version,
            "sample_refs": [relations.sample_ref(s) for s in samples],
            "sample_ids": [s.get("id") for s in samples],
            "current_price": entry.get("基线价格"),
        }

    def confirm_price(
        self, entry_id: int, target_version: str | None = None
    ) -> dict[str, Any]:
        """重新整理当前模板版本下有资格的样本并确认价格。

        严格只取一个模板版本的样本；如果调用方显式提交了跨版本样本集合，
        直接以 ``mixed_versions`` 拒绝，杜绝两版本混合数据。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "基线不存在"}
        template = self._template_for_baseline(entry)
        version = target_version or (
            relations.template_version(template) if template else ""
        ) or relations.baseline_pinned_version(entry) or "v1"

        # 重新整理：按 站点+商品+规格+当前版本 重建合格样本集合，不沿用旧版本引用。
        samples = self._eligible_samples(entry, version, reorganize=True)
        versions = {relations.sample_version(s) for s in samples}
        if len(versions) > 1:
            entry["status"] = "待重算"
            entry["pending"] = True
            return {
                "ok": False,
                "mixed_versions": True,
                "message": "检测到两个模板版本的样本混在同一确认集合中，已拒绝重新确认",
                "entry": entry,
            }
        return self._finish_with_samples(
            entry,
            samples,
            version=version,
            mode="confirm-current",
            message_prefix="整理有资格样本并确认价格",
            receipt_kind="价格确认回执(新版本)",
            template=template,
        )

    def guard_mixed_versions(self, entry_id: int, sample_ids: list[int]) -> dict[str, Any]:
        """供接口/页面在确认前校验所选样本是否跨模板版本。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "基线不存在"}
        versions = {
            relations.sample_version(s)
            for sid in sample_ids
            if (s := store.find("sample_store", int(sid))) is not None
        }
        if len(versions) > 1:
            return {
                "ok": False,
                "mixed_versions": True,
                "message": "不能用两个版本的混合数据重新确认价格",
                "versions": sorted(versions),
            }
        return {"ok": True, "mixed_versions": False, "versions": sorted(versions)}

    def impact_for_baseline(self, entry_id: int) -> dict[str, Any]:
        """从基线侧回查：是哪个模板版本、哪些样本导致它成为受影响候选。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "基线不存在"}
        template = self._template_for_baseline(entry)
        version = relations.baseline_pinned_version(entry) or (
            relations.template_version(template) if template else "v1"
        )
        samples = self._eligible_samples(entry, version) or relations.linked_samples(entry)
        return {
            "ok": True,
            "message": f"基线 {entry.get('基线编号') or entry_id} 的影响样本可回查",
            "entry": entry,
            "baseline_id": entry_id,
            "basis_version": version,
            "sample_ids": [s.get("id") for s in samples],
            "sample_refs": [relations.sample_ref(s) for s in samples],
            "causing_samples": [
                {
                    "id": s.get("id"),
                    "来源引用": relations.sample_ref(s),
                    "站点名称": relations.sample_site(s),
                    "模板版本": relations.sample_version(s),
                    "解析价格": s.get("解析价格", relations.sample_price(s)),
                    "status": s.get("status"),
                }
                for s in samples
            ],
        }

    def recompute_baseline(self, entry_id: int) -> dict[str, Any]:
        """重算基线：按基线固定的模板版本整理合格样本并计算均价。

        没有固定版本时使用站点当前启用模板的版本；任何情况下都只取同一版本，
        不允许新旧版本样本混合。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "基线不存在"}
        template = self._template_for_baseline(entry)
        version = relations.baseline_pinned_version(entry) or (
            relations.template_version(template) if template else "v1"
        )
        samples = self._eligible_samples(entry, version)
        result = self._finish_with_samples(
            entry,
            samples,
            version=version,
            mode="recompute",
            message_prefix="基线重算",
            receipt_kind="基线重算回执",
            template=template,
        )
        if not result["ok"]:
            return result
        result["message"] = (
            f"基线已按{version}重算，采用 {result['samples_used']} 个同版本样本，"
            f"新价格 {result['new_price']}"
        )
        return result

    def publish_to_compare(self, entry_id: int) -> dict[str, Any]:
        """把生效基线发布给比价计算器排队计算。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已生效":
            return {"ok": False, "message": "基线不存在或未生效"}
        rows = store.rows("price_compare")
        cid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": cid,
            "比价批次": f"baseline-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}",
            "商品标识": entry.get("商品标识", ""),
            "最低价站点": "",
            "价差比例": "0%",
            "计算时间": datetime.now(timezone.utc).isoformat(),
            "status": "排队中",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "基线已发布比价", "compare_id": cid}

    def apply_currency(self, entry_id: int, rate: float) -> dict[str, Any]:
        """按汇率把基线价格换算到目标币种。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "基线不存在"}
        if rate <= 0:
            return {"ok": False, "message": "汇率必须为正数"}
        try:
            price = float(entry.get("基线价格", "0") or 0)
        except ValueError:
            return {"ok": False, "message": "基线价格无效"}
        converted = round(price * rate, 2)
        entry["基线价格"] = f"{converted:.2f}"
        entry["更新时间"] = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "message": "基线已按汇率换算", "new_price": entry.get("基线价格")}

    def detect_stale(self, entry_id: int) -> dict[str, Any]:
        """检测基线是否过期需要重算。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "基线不存在"}
        updated = str(entry.get("更新时间", "") or "")
        stale = not updated or updated[:10] < datetime.now(timezone.utc).date().isoformat()
        if stale and entry.get("status") == "已生效":
            entry["status"] = "待重算"
            entry["pending"] = True
        return {"ok": True, "message": "基线新鲜度检测完成", "stale": stale}

    def register_goods_link(self, entry_id: int) -> dict[str, Any]:
        """把基线商品标识登记到商品档案中心形成主数据关联。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "基线不存在"}
        goods_id = entry.get("商品标识", "")
        exists = any(
            g.get("商品标识") == goods_id for g in store.rows("goods_registry")
        )
        if exists:
            return {"ok": True, "message": "商品档案已存在", "goods_id": goods_id}
        rows = store.rows("goods_registry")
        gid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": gid,
            "商品标识": goods_id,
            "商品名称": f"基线商品 {goods_id}",
            "品牌": "未登记",
            "类目": "未分类",
            "登记来源": "price_baseline",
            "status": "待合并",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "商品档案已登记", "registry_id": gid}
