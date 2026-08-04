-- Re-apply the product-facing copy after all earlier same-day migrations.
-- This keeps fresh databases aligned with the demo seed.

WITH demo_product_base AS (
    SELECT
        id,
        name,
        category_l2,
        attributes,
        right(id::text, 3)::integer AS product_no
    FROM products
    WHERE id::text LIKE '20000000-0000-4000-8000-%'
),
demo_products AS (
    SELECT
        *,
        CASE category_l2
            WHEN 'HEADPHONES' THEN (ARRAY['晨间通勤', '午后专注', '远程连线', '轻松出行', '夜间放松'])[((product_no - 1) % 5) + 1]
            WHEN 'COFFEE_MACHINE' THEN (ARRAY['清晨醒脑', '午后小聚', '居家慢享', '办公提神', '周末招待'])[((product_no - 41) % 5) + 1]
            WHEN 'ELECTRIC_KETTLE' THEN (ARRAY['早茶冲泡', '办公补水', '夜读热饮', '多人分享', '餐后收尾'])[((product_no - 61) % 5) + 1]
            WHEN 'RUNNING_SHOES' THEN (ARRAY['城市慢跑', '节奏训练', '周末拉练', '轻量恢复', '户外探索'])[((product_no - 81) % 5) + 1]
            WHEN 'WATCHES' THEN (ARRAY['日常通勤', '周末会面', '轻户外出行', '正式场合', '旅行记录'])[((product_no - 121) % 5) + 1]
            ELSE (ARRAY['通勤上妆', '周末约会', '镜前试色', '轻松出行', '晚间聚会'])[((product_no - 161) % 5) + 1]
        END AS copy_scene
    FROM demo_product_base
),
copy_content AS (
    SELECT
        source.*,
        CASE source.category_l2
            WHEN 'HEADPHONES' THEN CASE source.attributes ->> 'form'
                WHEN 'in-ear' THEN '入耳式耳机'
                ELSE '头戴式耳机'
            END
            WHEN 'COFFEE_MACHINE' THEN CASE source.attributes ->> 'type'
                WHEN 'capsule' THEN '胶囊咖啡机'
                ELSE '半自动咖啡机'
            END
            WHEN 'ELECTRIC_KETTLE' THEN CASE
                WHEN source.attributes ? 'capacityL'
                    THEN (source.attributes ->> 'capacityL') || ' L 电热水壶'
                ELSE '电热水壶'
            END
            WHEN 'RUNNING_SHOES' THEN CASE source.attributes ->> 'terrain'
                WHEN 'trail' THEN '越野跑鞋'
                ELSE '公路跑鞋'
            END
            WHEN 'WATCHES' THEN CASE source.attributes ->> 'movement'
                WHEN 'automatic' THEN '自动机械腕表'
                WHEN 'eco-drive' THEN '光动能腕表'
                ELSE '石英腕表'
            END
            ELSE format(
                '%s%s口红',
                CASE source.attributes ->> 'shade'
                    WHEN 'milk-tea' THEN '奶茶色'
                    WHEN 'tomato-red' THEN '番茄红'
                    WHEN 'coral' THEN '珊瑚色'
                    WHEN 'rose' THEN '玫瑰色'
                    ELSE '正红色'
                END,
                CASE source.attributes ->> 'finish'
                    WHEN 'matte' THEN '雾面'
                    WHEN 'satin' THEN '缎光'
                    ELSE '光泽'
                END
            )
        END AS copy_item,
        CASE source.category_l2
            WHEN 'HEADPHONES' THEN CASE
                WHEN (source.attributes ->> 'noiseCancellation') = 'true'
                    THEN '蓝牙连接配合降噪模式，听得更沉浸'
                ELSE '蓝牙连接保持轻松，周围声音也不会缺席'
            END
            WHEN 'COFFEE_MACHINE' THEN CASE source.attributes ->> 'type'
                WHEN 'capsule' THEN '一键萃取的节奏，早晨也能从容开场'
                ELSE CASE WHEN (source.attributes ->> 'steamWand') = 'true'
                    THEN '蒸汽棒让奶泡和拉花多一点可玩性'
                    ELSE '手动萃取的过程，让一杯咖啡更有仪式感'
                END
            END
            WHEN 'ELECTRIC_KETTLE' THEN CASE
                WHEN (source.attributes ->> 'temperatureControl') = 'true'
                     AND (source.attributes ->> 'keepWarm') = 'true'
                    THEN '温控与保温，让每一杯热饮都不必着急'
                WHEN (source.attributes ->> 'temperatureControl') = 'true'
                    THEN '温控设定更适合慢慢冲泡'
                WHEN (source.attributes ->> 'keepWarm') = 'true'
                    THEN '保温状态让下一杯也不用等待'
                ELSE '烧水步骤简洁，热饮随手就能安排'
            END
            WHEN 'RUNNING_SHOES' THEN CASE source.attributes ->> 'cushion'
                WHEN 'high' THEN '高缓震脚感，让慢跑节奏更从容'
                ELSE '轻快回弹，为节奏跑增添一点活力'
            END
            WHEN 'WATCHES' THEN CASE source.attributes ->> 'movement'
                WHEN 'automatic' THEN '自动机械机芯，让抬腕多一份节奏感'
                WHEN 'eco-drive' THEN '光动能设定，日常佩戴更省心'
                ELSE '石英机芯，时间表达干净利落'
            END
            ELSE CASE source.attributes ->> 'finish'
                WHEN 'matte' THEN '雾面妆效显得干净利落'
                WHEN 'satin' THEN '缎光妆效让色彩更有层次'
                ELSE '光泽妆效让双唇更有活力'
            END
        END AS copy_feature,
        CASE source.category_l2
            WHEN 'HEADPHONES' THEN CASE source.attributes ->> 'form'
                WHEN 'in-ear' THEN '入耳式轻盈佩戴'
                ELSE '头戴式包裹感'
            END
            WHEN 'COFFEE_MACHINE' THEN CASE source.attributes ->> 'type'
                WHEN 'capsule' THEN '胶囊操作，早晨更省心'
                ELSE '半自动制作，多一点手作感'
            END
            WHEN 'ELECTRIC_KETTLE' THEN CASE
                WHEN source.attributes ? 'capacityL'
                    THEN (source.attributes ->> 'capacityL') || ' L 容量，热饮不用反复续水'
                ELSE '日常容量，热饮随手就能准备'
            END
            WHEN 'RUNNING_SHOES' THEN CASE source.attributes ->> 'terrain'
                WHEN 'trail' THEN '越野路线，步伐更有底气'
                ELSE '公路跑步，步频更容易进入状态'
            END
            WHEN 'WATCHES' THEN CASE source.attributes ->> 'movement'
                WHEN 'automatic' THEN '自动机械，腕间多一点仪式感'
                WHEN 'eco-drive' THEN '光动能设定，日常佩戴少些顾虑'
                ELSE '石英机芯，读时简单直接'
            END
            ELSE CASE source.attributes ->> 'shade'
                WHEN 'milk-tea' THEN '奶茶色调，自然提气色'
                WHEN 'tomato-red' THEN '番茄红调，轻松显元气'
                WHEN 'coral' THEN '珊瑚色调，轻松衬肤色'
                WHEN 'rose' THEN '玫瑰色调，温柔不挑场合'
                ELSE '正红色调，出门更有气场'
            END
        END AS primary_point,
        CASE source.category_l2
            WHEN 'HEADPHONES' THEN CASE
                WHEN (source.attributes ->> 'noiseCancellation') = 'true'
                    THEN '降噪模式，通勤少些干扰'
                ELSE '自然听感，留意周围变化'
            END
            WHEN 'COFFEE_MACHINE' THEN CASE
                WHEN (source.attributes ->> 'steamWand') = 'true'
                    THEN '蒸汽棒加持，奶泡灵感随手来'
                ELSE '简洁操作，一杯咖啡不必复杂'
            END
            WHEN 'ELECTRIC_KETTLE' THEN CASE
                WHEN (source.attributes ->> 'temperatureControl') = 'true'
                    THEN '温控设定，冲泡节奏更自在'
                WHEN (source.attributes ->> 'keepWarm') = 'true'
                    THEN '保温功能，下一杯也不用等待'
                ELSE '一键烧水，随手准备一杯热饮'
            END
            WHEN 'RUNNING_SHOES' THEN CASE source.attributes ->> 'cushion'
                WHEN 'high' THEN '高缓震回弹，慢跑更从容'
                ELSE '中等缓震，节奏更轻快'
            END
            WHEN 'WATCHES' THEN CASE source.attributes ->> 'material'
                WHEN 'titanium' THEN '钛金属材质，轻盈又有存在感'
                WHEN 'resin' THEN '树脂表壳，轻松应对活力日程'
                ELSE '精钢表壳，通勤也好搭配'
            END
            ELSE CASE source.attributes ->> 'finish'
                WHEN 'matte' THEN '雾面妆效，干净利落'
                WHEN 'satin' THEN '缎光妆效，柔和有层次'
                ELSE '光泽妆效，双唇更有活力'
            END
        END AS secondary_point
    FROM demo_products AS source
)
UPDATE products AS target
SET
    description = format(
        (ARRAY[
            '%1$s 是为%2$s准备的%3$s，%4$s，适合融入不紧不慢的日常。',
            '围绕%2$s的使用节奏，%1$s 以%3$s的姿态出现，%4$s，轻松应对每天的小需求。',
            '%1$s 把%3$s的体验放进%2$s，%4$s，让选择更有画面感。',
            '当你需要一款适合%2$s的%3$s，%1$s 会用%4$s，让日常安排多一份从容。',
            '从%2$s出发，%1$s 作为%3$s，%4$s，留下一点恰到好处的惊喜。'
        ])[((source.product_no - 1) % 5) + 1],
        source.name,
        source.copy_scene,
        source.copy_item,
        source.copy_feature
    ),
    selling_points = ARRAY[
        source.primary_point,
        source.secondary_point,
        format(
            (ARRAY[
                '%1$s，为%2$s留出一点从容',
                '%1$s，让%2$s多一份轻松',
                '%1$s，陪你走过%2$s',
                '%1$s，把%2$s安排得更有序',
                '%1$s，为%2$s添一点小心思'
            ])[((source.product_no - 1) % 5) + 1],
            source.name,
            source.copy_scene
        )
    ]
FROM copy_content AS source
WHERE target.id = source.id;



-- Remove spacing that was appropriate between English words but not before Chinese copy.

UPDATE products
SET description = regexp_replace(description, ' ([是以会作为把])', '\1', 'g')
WHERE id::text LIKE '20000000-0000-4000-8000-%';
