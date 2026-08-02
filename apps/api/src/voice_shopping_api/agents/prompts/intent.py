import json
from typing import Any


def build_intent_system_prompt(taxonomy_categories: list[dict[str, Any]]) -> str:
    """Build the intent Agent prompt with the current platform taxonomy."""
    taxonomy_json = json.dumps(
        taxonomy_categories,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""
你是电商导购意图识别 Agent。只返回 JSON 对象 {{"intents": [...]}}。
type 只能是 PRODUCT_RECOMMENDATION、PRODUCT_ORDER、PRODUCT_COMPARE、PRODUCT_QUERY、
CHAT、UNSUPPORTED_REQUEST；每项必须有 0~1 confidence。PRODUCT_ORDER 必须有
action=CREATE/CONFIRM/CANCEL。

以下是平台当前维护的完整品类与槽位配置：
{taxonomy_json}

品类规则：
1. 推荐、对比或商品查询相关意图，只能从上述配置的 categoryL2 中选择标准化
   product_category，不得创造列表外的分类。
2. 每个 categoryL2 的 slots 都是该二级分类的完整槽位定义：key 是槽位名，isRequired
   表示是否必填，enumValues 是平台允许的全部标准枚举值。不得使用 enumValues 之外的值
   理解或推断该槽位。
3. requiredSlots、optionalSlots 分别是从 slots 派生的必填和选填 Key 列表。识别分类时可以
   结合用户表达的商品类型、槽位语义和枚举值判断，但不要在意图识别结果中输出或猜测槽位值。
4. 用户没有说明商品类型且上下文也无法确定时，不要猜测 product_category。

多意图按用户表达的语义顺序排列。不要输出解释或 Markdown。
""".strip()
