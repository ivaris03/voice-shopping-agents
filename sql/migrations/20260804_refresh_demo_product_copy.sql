-- Give every deterministic demo product its own description and selling-point copy.
-- This leaves merchant-created catalog records untouched.

WITH demo_product_base AS (
    SELECT
        id,
        name,
        category_l2,
        right(id::text, 3)::integer AS product_no
    FROM products
    WHERE id::text LIKE '20000000-0000-4000-8000-%'
),
demo_products AS (
    SELECT
        *,
        lpad(product_no::text, 3, '0') AS copy_no,
        CASE category_l2
            WHEN 'HEADPHONES' THEN '音频设备'
            WHEN 'COFFEE_MACHINE' THEN '咖啡设备'
            WHEN 'ELECTRIC_KETTLE' THEN '热饮水具'
            WHEN 'RUNNING_SHOES' THEN '跑步鞋履'
            WHEN 'WATCHES' THEN '腕间配饰'
            ELSE '唇妆单品'
        END AS copy_category,
        CASE category_l2
            WHEN 'HEADPHONES' THEN (ARRAY['晨间通勤', '午后专注', '远程连线', '轻松出行', '夜间放松'])[((product_no - 1) % 5) + 1]
            WHEN 'COFFEE_MACHINE' THEN (ARRAY['清晨醒脑', '午后小聚', '居家慢享', '办公提神', '周末招待'])[((product_no - 41) % 5) + 1]
            WHEN 'ELECTRIC_KETTLE' THEN (ARRAY['早茶冲泡', '办公补水', '夜读热饮', '多人分享', '餐后收尾'])[((product_no - 61) % 5) + 1]
            WHEN 'RUNNING_SHOES' THEN (ARRAY['城市慢跑', '节奏训练', '周末拉练', '轻量恢复', '户外探索'])[((product_no - 81) % 5) + 1]
            WHEN 'WATCHES' THEN (ARRAY['日常通勤', '周末会面', '轻户外出行', '正式场合', '旅行记录'])[((product_no - 121) % 5) + 1]
            ELSE (ARRAY['通勤上妆', '周末约会', '镜前试色', '轻松出行', '晚间聚会'])[((product_no - 161) % 5) + 1]
        END AS copy_scene
    FROM demo_product_base
)
UPDATE products AS target
SET
    description = format(
        (ARRAY[
            '%1$s 是本地目录中第 %2$s 款%3$s，作为%4$s场景的演示选择。',
            '在本地目录的第 %2$s 号位置，%1$s 被设为一款%3$s，用来呈现%4$s的浏览效果。',
            '%1$s 对应演示款 %2$s，以%3$s为主题，方便展示%4$s的商品卡片。',
            '编号 %2$s 的%1$s 是面向%3$s的演示组合，本页围绕%4$s安排展示。',
            '为丰富%4$s的选择，目录加入了%1$s 这款%3$s，演示编号为 %2$s。'
        ])[((source.product_no - 1) % 5) + 1],
        source.name,
        source.copy_no,
        source.copy_category,
        source.copy_scene
    ),
    selling_points = ARRAY[
        format(
            (ARRAY['款号 %s：%s', '目录 %s：%s', '展示编号 %s：%s'])[((source.product_no - 1) % 3) + 1],
            source.copy_no,
            source.name
        ),
        format(
            (ARRAY['筛选主题 %s：%s', '配置标签 %s：%s', '分类记录 %s：%s'])[((source.product_no - 1) % 3) + 1],
            source.copy_no,
            source.copy_category
        ),
        format(
            (ARRAY['场景记录 %s：%s', '浏览提示 %s：%s', '演示路径 %s：%s'])[((source.product_no - 1) % 3) + 1],
            source.copy_no,
            source.copy_scene
        )
    ]
FROM demo_products AS source
WHERE target.id = source.id;
