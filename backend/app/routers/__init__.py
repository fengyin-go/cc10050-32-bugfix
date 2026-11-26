"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import ops, resource

# ops 前缀固定为 /api/ops，先注册以便回执/语义化入口优先生效。
ROUTERS = [ops, resource]
