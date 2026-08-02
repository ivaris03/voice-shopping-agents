-- 语音导购平台演示数据
-- 可重复执行；仅用于本地开发/演示。演示账号的初始密码均为 Demo1234!。

BEGIN;

-- 固定范围清理：仅重置本文件维护的演示会话和订单数据，避免待确认订单
-- 在重复导入后已经过期，或订单终态与 session_states 中的 pending 状态冲突。
DELETE FROM session_states
WHERE session_id IN (
    '30000000-0000-4000-8000-000000000001',
    '30000000-0000-4000-8000-000000000002',
    '30000000-0000-4000-8000-000000000003',
    '30000000-0000-4000-8000-000000000004'
);

DELETE FROM orders
WHERE id IN (
    '50000000-0000-4000-8000-000000000001',
    '50000000-0000-4000-8000-000000000002',
    '50000000-0000-4000-8000-000000000003'
);

DELETE FROM session_messages
WHERE session_id IN (
    '30000000-0000-4000-8000-000000000001',
    '30000000-0000-4000-8000-000000000002',
    '30000000-0000-4000-8000-000000000003',
    '30000000-0000-4000-8000-000000000004'
);

DELETE FROM sessions
WHERE id IN (
    '30000000-0000-4000-8000-000000000001',
    '30000000-0000-4000-8000-000000000002',
    '30000000-0000-4000-8000-000000000003',
    '30000000-0000-4000-8000-000000000004'
);

INSERT INTO users (id, email, password_hash, display_name, phone, role, status)
VALUES
    ('00000000-0000-4000-8000-000000000001', 'admin@example.com', crypt('Demo1234!', gen_salt('bf')), '平台管理员', '13800000001', 'platform', 'active'),
    ('00000000-0000-4000-8000-000000000002', 'audio@example.com', crypt('Demo1234!', gen_salt('bf')), '声动数码店主', '13800000002', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000003', 'daily@example.com', crypt('Demo1234!', gen_salt('bf')), '日常家电店主', '13800000003', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000004', 'sports@example.com', crypt('Demo1234!', gen_salt('bf')), '飞跃运动店主', '13800000004', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000005', 'watch@example.com', crypt('Demo1234!', gen_salt('bf')), '恒时腕表店主', '13800000005', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000006', 'beauty@example.com', crypt('Demo1234!', gen_salt('bf')), '花漾美妆店主', '13800000006', 'merchant', 'active'),
    ('00000000-0000-4000-8000-000000000101', 'lin@example.com', crypt('Demo1234!', gen_salt('bf')), '小林', '13900000101', 'customer', 'active'),
    ('00000000-0000-4000-8000-000000000102', 'chen@example.com', crypt('Demo1234!', gen_salt('bf')), '陈晨', '13900000102', 'customer', 'active'),
    ('00000000-0000-4000-8000-000000000103', 'alice@example.com', crypt('Demo1234!', gen_salt('bf')), '爱丽丝', '13900000103', 'customer', 'active'),
    ('00000000-0000-4000-8000-000000000104', 'david@example.com', crypt('Demo1234!', gen_salt('bf')), '大卫', '13900000104', 'customer', 'active'),
    ('00000000-0000-4000-8000-000000000105', 'eric@example.com', crypt('Demo1234!', gen_salt('bf')), '埃里克', '13900000105', 'customer', 'active')
ON CONFLICT (id) DO UPDATE SET
    email = EXCLUDED.email,
    display_name = EXCLUDED.display_name,
    phone = EXCLUDED.phone,
    role = EXCLUDED.role,
    status = EXCLUDED.status;

-- 一级分类可以独立存在；二级分类必须引用一个已存在的一级分类。
INSERT INTO category_l1 (code)
VALUES
    ('ELECTRONICS'),
    ('HOME_APPLIANCES'),
    ('SPORTS'),
    ('FASHION'),
    ('BEAUTY')
ON CONFLICT (code) DO NOTHING;

INSERT INTO category_l2 (
    category_l1_id, category_l1, category_l2, required_slots, optional_slots
)
SELECT g.id, seed.category_l1, seed.category_l2, seed.required_slots, seed.optional_slots
FROM (
    VALUES
        ('ELECTRONICS', 'HEADPHONES', ARRAY['form', 'connectivity'], ARRAY['noiseCancellation', 'batteryHours']),
        ('HOME_APPLIANCES', 'COFFEE_MACHINE', ARRAY['type'], ARRAY['steamWand', 'pressureBar', 'waterTankMl']),
        ('HOME_APPLIANCES', 'ELECTRIC_KETTLE', ARRAY['capacityL'], ARRAY['temperatureControl', 'keepWarm']),
        ('SPORTS', 'RUNNING_SHOES', ARRAY['gender', 'size', 'terrain'], ARRAY['cushion', 'footType']),
        ('FASHION', 'WATCHES', ARRAY['movement'], ARRAY['gender', 'material', 'waterResistance']),
        ('BEAUTY', 'LIPSTICK', ARRAY['shade', 'finish'], ARRAY['skinType'])
) AS seed(category_l1, category_l2, required_slots, optional_slots)
JOIN category_l1 g ON g.code = seed.category_l1
ON CONFLICT (category_l2) DO UPDATE SET
    category_l1_id = EXCLUDED.category_l1_id,
    category_l1 = EXCLUDED.category_l1,
    required_slots = EXCLUDED.required_slots,
    optional_slots = EXCLUDED.optional_slots;

-- 每个槽位在创建时必须同时给出非空枚举；必填/选填只影响澄清是否阻塞。
INSERT INTO category_slots (category_id, key, is_required, enum_values)
SELECT c.id, seed.key, seed.is_required, seed.enum_values
FROM (
    VALUES
        ('HEADPHONES', 'form', true, '["in-ear","over-ear"]'::jsonb),
        ('HEADPHONES', 'connectivity', true, '["bluetooth","wired"]'::jsonb),
        ('HEADPHONES', 'noiseCancellation', false, '[true,false]'::jsonb),
        ('HEADPHONES', 'batteryHours', false, '[5,6,8,24,30,32,45,60]'::jsonb),
        ('COFFEE_MACHINE', 'type', true, '["capsule","semi-automatic"]'::jsonb),
        ('COFFEE_MACHINE', 'steamWand', false, '[true,false]'::jsonb),
        ('COFFEE_MACHINE', 'pressureBar', false, '[9,15,19,20]'::jsonb),
        ('COFFEE_MACHINE', 'waterTankMl', false, '[600,800,1000,1500,2000]'::jsonb),
        ('ELECTRIC_KETTLE', 'capacityL', true, '[1,1.2,1.5,1.7,2]'::jsonb),
        ('ELECTRIC_KETTLE', 'temperatureControl', false, '[true,false]'::jsonb),
        ('ELECTRIC_KETTLE', 'keepWarm', false, '[true,false]'::jsonb),
        ('RUNNING_SHOES', 'gender', true, '["male","female","unisex"]'::jsonb),
        ('RUNNING_SHOES', 'size', true, '[35,36,37,38,39,40,41,42,43,44,45,46]'::jsonb),
        ('RUNNING_SHOES', 'terrain', true, '["road","trail"]'::jsonb),
        ('RUNNING_SHOES', 'cushion', false, '["high","medium"]'::jsonb),
        ('RUNNING_SHOES', 'footType', false, '["neutral","flat","overpronation"]'::jsonb),
        ('WATCHES', 'movement', true, '["automatic","quartz","eco-drive"]'::jsonb),
        ('WATCHES', 'gender', false, '["male","female","unisex"]'::jsonb),
        ('WATCHES', 'material', false, '["steel","titanium","resin"]'::jsonb),
        ('WATCHES', 'waterResistance', false, '[30,50,100,200]'::jsonb),
        ('LIPSTICK', 'shade', true, '["milk-tea","tomato-red","coral","rose","ruby-red"]'::jsonb),
        ('LIPSTICK', 'finish', true, '["matte","satin","glossy"]'::jsonb),
        ('LIPSTICK', 'skinType', false, '["dry","oily","normal"]'::jsonb)
) AS seed(category_l2, key, is_required, enum_values)
JOIN category_l2 c ON c.category_l2 = seed.category_l2
ON CONFLICT (category_id, key) DO UPDATE SET
    is_required = EXCLUDED.is_required,
    enum_values = EXCLUDED.enum_values;

INSERT INTO merchants (
    id, owner_user_id, name, slug, description, logo_url, contact_phone,
    is_enabled, disabled_reason
)
VALUES
    (
        '10000000-0000-4000-8000-000000000001',
        '00000000-0000-4000-8000-000000000002',
        '声动数码', 'sound-digital', '专注耳机与便携音频设备。',
        'https://example.com/images/merchants/sound-digital.png', '13800000002', true, NULL
    ),
    (
        '10000000-0000-4000-8000-000000000002',
        '00000000-0000-4000-8000-000000000003',
        '日常家电', 'daily-appliances', '提供实用、易维护的小家电。',
        'https://example.com/images/merchants/daily-appliances.png', '13800000003', true, NULL
    ),
    (
        '10000000-0000-4000-8000-000000000003',
        '00000000-0000-4000-8000-000000000003',
        '日常家电特卖店', 'daily-appliances-outlet', '暂停营业的演示店铺。',
        'https://example.com/images/merchants/daily-appliances-outlet.png', '13800000003', false, '店铺资料审核中'
    ),
    (
        '10000000-0000-4000-8000-000000000004',
        '00000000-0000-4000-8000-000000000004',
        '飞跃运动旗舰店', 'flyover-sports', '提供覆盖日常训练、竞速和越野场景的专业跑鞋。',
        'https://example.com/images/merchants/flyover-sports.png', '13800000004', true, NULL
    ),
    (
        '10000000-0000-4000-8000-000000000005',
        '00000000-0000-4000-8000-000000000005',
        '恒时腕表精品店', 'timeless-watches', '主营机械表、石英表和运动腕表。',
        'https://example.com/images/merchants/timeless-watches.png', '13800000005', true, NULL
    ),
    (
        '10000000-0000-4000-8000-000000000006',
        '00000000-0000-4000-8000-000000000006',
        '花漾美妆', 'bloom-beauty', '提供适合不同肤色和妆效偏好的彩妆商品。',
        'https://example.com/images/merchants/bloom-beauty.png', '13800000006', true, NULL
    )
ON CONFLICT (id) DO UPDATE SET
    owner_user_id = EXCLUDED.owner_user_id,
    name = EXCLUDED.name,
    slug = EXCLUDED.slug,
    description = EXCLUDED.description,
    logo_url = EXCLUDED.logo_url,
    contact_phone = EXCLUDED.contact_phone,
    is_enabled = EXCLUDED.is_enabled,
    disabled_reason = EXCLUDED.disabled_reason,
    deleted_at = NULL;

-- 用简单的单位向量填充演示 embedding；生产数据应由 embedding 服务生成。
WITH product_seed (
    id, merchant_id, sku, name, category_l1, category_l2, brand, description, price, stock,
    attributes, selling_points, image_urls, status, embedding_axis
) AS (
    VALUES
        (
            '20000000-0000-4000-8000-000000000001'::uuid,
            '10000000-0000-4000-8000-000000000001'::uuid,
            'HEADPHONE-A1', '云雀 Air 降噪耳机', 'ELECTRONICS', 'HEADPHONES', '云雀',
            '轻量头戴式主动降噪耳机，适合通勤。', 699.00::numeric, 80,
            '{"form":"over-ear","noiseCancellation":true,"batteryHours":45,"color":"雾灰"}'::jsonb,
            ARRAY['主动降噪适合通勤', '约 45 小时续航', '轻量头戴设计'],
            ARRAY['https://example.com/images/products/headphone-a1-1.png'], 'on_sale', 1
        ),
        (
            '20000000-0000-4000-8000-000000000002'::uuid,
            '10000000-0000-4000-8000-000000000001'::uuid,
            'HEADPHONE-B2', '潮汐 Pro 真无线耳机', 'ELECTRONICS', 'HEADPHONES', '潮汐',
            '支持多设备连接的真无线降噪耳机。', 999.00::numeric, 45,
            '{"form":"in-ear","noiseCancellation":true,"batteryHours":32,"waterResistance":"IPX5","color":"曜石黑"}'::jsonb,
            ARRAY['双设备快速切换', '真无线主动降噪', 'IPX5 防水'],
            ARRAY['https://example.com/images/products/headphone-b2-1.png'], 'on_sale', 2
        ),
        (
            '20000000-0000-4000-8000-000000000003'::uuid,
            '10000000-0000-4000-8000-000000000001'::uuid,
            'HEADPHONE-C3', '原野 Lite 蓝牙耳机', 'ELECTRONICS', 'HEADPHONES', '原野',
            '长续航入门头戴式蓝牙耳机。', 329.00::numeric, 120,
            '{"form":"over-ear","noiseCancellation":false,"batteryHours":60,"color":"米白"}'::jsonb,
            ARRAY['约 60 小时长续航', '入门价格友好', '柔软头戴佩戴舒适'],
            ARRAY['https://example.com/images/products/headphone-c3-1.png'], 'on_sale', 3
        ),
        (
            '20000000-0000-4000-8000-000000000004'::uuid,
            '10000000-0000-4000-8000-000000000002'::uuid,
            'COFFEE-M1', '晨雾 Mini 胶囊咖啡机', 'HOME_APPLIANCES', 'COFFEE_MACHINE', '晨雾',
            '小体积胶囊咖啡机，适合单人和小户型。', 599.00::numeric, 35,
            '{"type":"capsule","waterTankMl":650,"pressureBar":19,"color":"奶油白"}'::jsonb,
            ARRAY['机身小巧节省台面空间', '19 Bar 萃取压力', '胶囊操作简单'],
            ARRAY['https://example.com/images/products/coffee-m1-1.png'], 'on_sale', 11
        ),
        (
            '20000000-0000-4000-8000-000000000005'::uuid,
            '10000000-0000-4000-8000-000000000002'::uuid,
            'COFFEE-M2', '山岚半自动咖啡机', 'HOME_APPLIANCES', 'COFFEE_MACHINE', '山岚',
            '带蒸汽棒和压力表，适合进阶家庭咖啡。', 1699.00::numeric, 18,
            '{"type":"semi-automatic","waterTankMl":1700,"pressureBar":15,"steamWand":true,"color":"银色"}'::jsonb,
            ARRAY['专业蒸汽棒可打奶泡', '压力表辅助萃取', '1.7 升大水箱'],
            ARRAY['https://example.com/images/products/coffee-m2-1.png'], 'on_sale', 12
        ),
        (
            '20000000-0000-4000-8000-000000000006'::uuid,
            '10000000-0000-4000-8000-000000000003'::uuid,
            'KETTLE-X1', '清泉恒温水壶', 'HOME_APPLIANCES', 'ELECTRIC_KETTLE', '清泉',
            '来自已禁用店铺的商品，用户端不应展示。', 239.00::numeric, 20,
            '{"capacityL":1.5,"temperatureControl":true,"keepWarm":true}'::jsonb,
            ARRAY['多档温度可调', '1.5 升容量', '支持保温'],
            ARRAY['https://example.com/images/products/kettle-x1-1.png'], 'on_sale', 21
        ),
        -- 跑鞋：覆盖预算、缓震、足型、路面和性别等硬约束。
        (
            '20000000-0000-4000-8000-000000000101'::uuid,
            '10000000-0000-4000-8000-000000000004'::uuid,
            'RUN-001', 'Nike Pegasus 40 缓震跑鞋', 'SPORTS', 'RUNNING_SHOES', 'Nike',
            'Air Zoom 中底提供稳定缓震，适合日常慢跑和长距离训练。', 899.00::numeric, 50,
            '{"cushion":"high","weightClass":"medium","gender":"unisex","sizeRange":[36,46],"terrain":"road","originalPrice":1099,"isNewArrival":false}'::jsonb,
            ARRAY['缓震充足', '透气鞋面', '日常训练百搭'],
            ARRAY['https://example.com/images/products/run-001.png'], 'on_sale', 101
        ),
        (
            '20000000-0000-4000-8000-000000000102'::uuid,
            '10000000-0000-4000-8000-000000000004'::uuid,
            'RUN-002', 'Adidas Ultraboost 22 跑鞋', 'SPORTS', 'RUNNING_SHOES', 'Adidas',
            'Boost 中底带来柔软回弹，针织鞋面贴合自然。', 1299.00::numeric, 30,
            '{"cushion":"high","weightClass":"medium","gender":"unisex","sizeRange":[38,46],"terrain":"road","originalPrice":1499,"isNewArrival":false}'::jsonb,
            ARRAY['柔软缓震', '回弹明显', '适合长距离'],
            ARRAY['https://example.com/images/products/run-002.png'], 'on_sale', 102
        ),
        (
            '20000000-0000-4000-8000-000000000103'::uuid,
            '10000000-0000-4000-8000-000000000004'::uuid,
            'RUN-003', 'Saucony Endorphin Speed 3 竞速跑鞋', 'SPORTS', 'RUNNING_SHOES', 'Saucony',
            '尼龙板搭配轻量泡棉，兼顾日常训练与比赛节奏。', 1399.00::numeric, 20,
            '{"cushion":"medium","weightClass":"light","gender":"unisex","sizeRange":[39,45],"terrain":"road","plate":"nylon","originalPrice":1599,"isNewArrival":true}'::jsonb,
            ARRAY['轻量设计', '推进感清晰', '训练比赛兼用'],
            ARRAY['https://example.com/images/products/run-003.png'], 'on_sale', 103
        ),
        (
            '20000000-0000-4000-8000-000000000104'::uuid,
            '10000000-0000-4000-8000-000000000004'::uuid,
            'RUN-004', 'Asics Gel-Kayano 30 稳定跑鞋', 'SPORTS', 'RUNNING_SHOES', 'Asics',
            '稳定支撑系统搭配高缓震中底，适合扁平足和大体重跑者。', 1599.00::numeric, 25,
            '{"cushion":"high","weightClass":"heavy","gender":"unisex","sizeRange":[38,46],"terrain":"road","footType":["flat","overpronation"],"originalPrice":1699,"isNewArrival":false}'::jsonb,
            ARRAY['稳定支撑', '缓震持久', '适合扁平足'],
            ARRAY['https://example.com/images/products/run-004.png'], 'on_sale', 104
        ),
        (
            '20000000-0000-4000-8000-000000000105'::uuid,
            '10000000-0000-4000-8000-000000000004'::uuid,
            'RUN-005', 'HOKA Clifton 9 轻量缓震跑鞋', 'SPORTS', 'RUNNING_SHOES', 'HOKA',
            '厚底缓震和轻量化设计兼顾跑步与日常步行。', 1180.00::numeric, 40,
            '{"cushion":"high","weightClass":"light","gender":"unisex","sizeRange":[38,46],"terrain":"road","originalPrice":1380,"isNewArrival":false}'::jsonb,
            ARRAY['厚底舒适', '轻量缓震', '通勤跑步兼用'],
            ARRAY['https://example.com/images/products/run-005.png'], 'on_sale', 105
        ),
        (
            '20000000-0000-4000-8000-000000000106'::uuid,
            '10000000-0000-4000-8000-000000000004'::uuid,
            'RUN-006', 'Nike Pegasus 39 入门跑鞋', 'SPORTS', 'RUNNING_SHOES', 'Nike',
            '经典上一代日常训练鞋，适合作为入门选择。', 599.00::numeric, 60,
            '{"cushion":"medium","weightClass":"medium","gender":"unisex","sizeRange":[36,45],"terrain":"road","originalPrice":799,"isNewArrival":false}'::jsonb,
            ARRAY['价格友好', '经典耐穿', '适合入门训练'],
            ARRAY['https://example.com/images/products/run-006.png'], 'on_sale', 106
        ),
        (
            '20000000-0000-4000-8000-000000000107'::uuid,
            '10000000-0000-4000-8000-000000000004'::uuid,
            'RUN-007', 'New Balance FuelCell 女款跑鞋', 'SPORTS', 'RUNNING_SHOES', 'New Balance',
            '轻量 FuelCell 中底和女性鞋楦，适合日常路跑。', 699.00::numeric, 15,
            '{"cushion":"medium","weightClass":"light","gender":"female","color":"pink","sizeRange":[35,40],"terrain":"road","originalPrice":899,"isNewArrival":false}'::jsonb,
            ARRAY['女性专属鞋楦', '轻量回弹', '粉色外观'],
            ARRAY['https://example.com/images/products/run-007.png'], 'on_sale', 107
        ),
        (
            '20000000-0000-4000-8000-000000000108'::uuid,
            '10000000-0000-4000-8000-000000000004'::uuid,
            'RUN-008', 'HOKA Speedgoat 5 越野跑鞋', 'SPORTS', 'RUNNING_SHOES', 'HOKA',
            '专业越野鞋底提供复杂路面的抓地与保护。', 1580.00::numeric, 10,
            '{"cushion":"high","weightClass":"medium","gender":"unisex","sizeRange":[39,46],"terrain":"trail","outsole":"Vibram","originalPrice":1780,"isNewArrival":false}'::jsonb,
            ARRAY['越野专用', '强力抓地', '足部保护充分'],
            ARRAY['https://example.com/images/products/run-008.png'], 'on_sale', 108
        ),
        (
            '20000000-0000-4000-8000-000000000109'::uuid,
            '10000000-0000-4000-8000-000000000004'::uuid,
            'RUN-009', 'Nike Vaporfly 3 碳板竞速跑鞋', 'SPORTS', 'RUNNING_SHOES', 'Nike',
            '全掌碳板搭配轻量高回弹中底，面向竞速和个人最佳成绩。', 1999.00::numeric, 8,
            '{"cushion":"medium","weightClass":"ultralight","gender":"unisex","sizeRange":[39,45],"terrain":"road","plate":"carbon","originalPrice":2299,"isNewArrival":true}'::jsonb,
            ARRAY['全掌碳板', '极致轻量', '竞速取向'],
            ARRAY['https://example.com/images/products/run-009.png'], 'on_sale', 109
        ),
        (
            '20000000-0000-4000-8000-000000000110'::uuid,
            '10000000-0000-4000-8000-000000000004'::uuid,
            'RUN-010', 'Asics Cumulus 25 日常训练鞋', 'SPORTS', 'RUNNING_SHOES', 'Asics',
            '中性支撑和均衡缓震，适合稳定完成日常训练。', 899.00::numeric, 35,
            '{"cushion":"medium","weightClass":"medium","gender":"unisex","sizeRange":[38,46],"terrain":"road","footType":["neutral"],"originalPrice":1099,"isNewArrival":false}'::jsonb,
            ARRAY['均衡缓震', '中性支撑', '日常训练百搭'],
            ARRAY['https://example.com/images/products/run-010.png'], 'on_sale', 110
        ),
        -- 腕表：覆盖机芯、材质、防水、性别和预算区间。
        (
            '20000000-0000-4000-8000-000000000201'::uuid,
            '10000000-0000-4000-8000-000000000005'::uuid,
            'WAT-001', 'Seiko 5 Sports 机械腕表', 'ACCESSORIES', 'WATCHES', 'Seiko',
            '自动机械机芯搭配钢制表壳，适合入门机械表用户。', 2280.00::numeric, 40,
            '{"movement":"automatic","material":"steel","gender":"male","waterResistance":"100m","originalPrice":2580,"isNewArrival":false}'::jsonb,
            ARRAY['入门机械表', '100 米防水', '性价比突出'],
            ARRAY['https://example.com/images/products/wat-001.png'], 'on_sale', 201
        ),
        (
            '20000000-0000-4000-8000-000000000202'::uuid,
            '10000000-0000-4000-8000-000000000005'::uuid,
            'WAT-002', 'Casio G-Shock GA-2100', 'ACCESSORIES', 'WATCHES', 'Casio',
            '轻量树脂表壳，兼顾抗震性能和街头风格。', 899.00::numeric, 50,
            '{"movement":"quartz","material":"resin","gender":"unisex","waterResistance":"200m","originalPrice":1099,"isNewArrival":false}'::jsonb,
            ARRAY['强力抗震', '200 米防水', '轻量街头造型'],
            ARRAY['https://example.com/images/products/wat-002.png'], 'on_sale', 202
        ),
        (
            '20000000-0000-4000-8000-000000000203'::uuid,
            '10000000-0000-4000-8000-000000000005'::uuid,
            'WAT-003', 'Citizen 光动能 AT8020', 'ACCESSORIES', 'WATCHES', 'Citizen',
            '光动能驱动搭配钛金属表壳，支持电波校时。', 3680.00::numeric, 20,
            '{"movement":"eco-drive","material":"titanium","gender":"male","waterResistance":"200m","radioControlled":true,"originalPrice":4280,"isNewArrival":false}'::jsonb,
            ARRAY['光能驱动', '轻量钛金属', '电波自动校时'],
            ARRAY['https://example.com/images/products/wat-003.png'], 'on_sale', 203
        ),
        (
            '20000000-0000-4000-8000-000000000204'::uuid,
            '10000000-0000-4000-8000-000000000005'::uuid,
            'WAT-004', 'Tissot 力洛克机械腕表', 'ACCESSORIES', 'WATCHES', 'Tissot',
            '经典商务外观，自动机械机芯提供约 80 小时动力储存。', 3880.00::numeric, 15,
            '{"movement":"automatic","material":"steel","gender":"male","waterResistance":"50m","powerReserveHours":80,"originalPrice":4280,"isNewArrival":false}'::jsonb,
            ARRAY['经典商务设计', '80 小时动储', '瑞士机械机芯'],
            ARRAY['https://example.com/images/products/wat-004.png'], 'on_sale', 204
        ),
        (
            '20000000-0000-4000-8000-000000000205'::uuid,
            '10000000-0000-4000-8000-000000000005'::uuid,
            'WAT-005', 'Casio 简约石英女表', 'ACCESSORIES', 'WATCHES', 'Casio',
            '小表径简约设计，适合日常通勤搭配。', 399.00::numeric, 80,
            '{"movement":"quartz","material":"steel","gender":"female","waterResistance":"30m","originalPrice":599,"isNewArrival":false}'::jsonb,
            ARRAY['通勤百搭', '价格亲民', '小巧表径'],
            ARRAY['https://example.com/images/products/wat-005.png'], 'on_sale', 205
        ),
        (
            '20000000-0000-4000-8000-000000000206'::uuid,
            '10000000-0000-4000-8000-000000000005'::uuid,
            'WAT-006', 'Casio GW-B5600 智能运动表', 'ACCESSORIES', 'WATCHES', 'Casio',
            '经典方形电子表，支持蓝牙连接和自动校时。', 1680.00::numeric, 25,
            '{"movement":"digital","material":"resin","gender":"unisex","waterResistance":"200m","bluetooth":true,"originalPrice":1880,"isNewArrival":true}'::jsonb,
            ARRAY['蓝牙连接', '多功能运动模式', '200 米防水'],
            ARRAY['https://example.com/images/products/wat-006.png'], 'on_sale', 206
        ),
        (
            '20000000-0000-4000-8000-000000000207'::uuid,
            '10000000-0000-4000-8000-000000000005'::uuid,
            'WAT-007', 'Seiko Presage 机械女表', 'ACCESSORIES', 'WATCHES', 'Seiko',
            '复古珐琅质感表盘搭配自动机械机芯。', 4580.00::numeric, 10,
            '{"movement":"automatic","material":"steel","gender":"female","waterResistance":"50m","dial":"enamel-style","originalPrice":4980,"isNewArrival":false}'::jsonb,
            ARRAY['复古表盘工艺', '自动机械机芯', '优雅女表设计'],
            ARRAY['https://example.com/images/products/wat-007.png'], 'on_sale', 207
        ),
        (
            '20000000-0000-4000-8000-000000000208'::uuid,
            '10000000-0000-4000-8000-000000000005'::uuid,
            'WAT-008', 'Seiko Prospex 潜水腕表', 'ACCESSORIES', 'WATCHES', 'Seiko',
            '大表径专业潜水表，提供可靠防水和夜光读取。', 5280.00::numeric, 12,
            '{"movement":"automatic","material":"steel","gender":"male","waterResistance":"200m","diameterMm":44,"originalPrice":5680,"isNewArrival":false}'::jsonb,
            ARRAY['专业潜水规格', '44 毫米大表径', '夜光显示'],
            ARRAY['https://example.com/images/products/wat-008.png'], 'on_sale', 208
        ),
        (
            '20000000-0000-4000-8000-000000000209'::uuid,
            '10000000-0000-4000-8000-000000000005'::uuid,
            'WAT-009', 'Tissot 经典三针石英表', 'ACCESSORIES', 'WATCHES', 'Tissot',
            '简洁三针设计，适合商务和日常通勤。', 2480.00::numeric, 18,
            '{"movement":"quartz","material":"steel","gender":"male","waterResistance":"30m","originalPrice":2980,"isNewArrival":false}'::jsonb,
            ARRAY['商务通勤', '经典三针', '维护简单'],
            ARRAY['https://example.com/images/products/wat-009.png'], 'on_sale', 209
        ),
        (
            '20000000-0000-4000-8000-000000000210'::uuid,
            '10000000-0000-4000-8000-000000000005'::uuid,
            'WAT-010', 'Seiko GMT 限量机械表', 'ACCESSORIES', 'WATCHES', 'Seiko',
            '支持双时区显示的限量自动机械腕表。', 7880.00::numeric, 5,
            '{"movement":"automatic","material":"steel","gender":"male","waterResistance":"100m","gmt":true,"limitedUnits":500,"originalPrice":8880,"isNewArrival":true}'::jsonb,
            ARRAY['GMT 双时区', '限量 500 枚', '收藏属性'],
            ARRAY['https://example.com/images/products/wat-010.png'], 'on_sale', 210
        ),
        -- 品牌耳机：补足同品类内的价格和属性差异，便于召回与精排。
        (
            '20000000-0000-4000-8000-000000000301'::uuid,
            '10000000-0000-4000-8000-000000000001'::uuid,
            'HDP-001', 'Sony WH-1000XM5 降噪耳机', 'ELECTRONICS', 'HEADPHONES', 'Sony',
            '头戴式旗舰主动降噪耳机，兼顾通勤降噪和通话清晰度。', 2399.00::numeric, 30,
            '{"form":"over-ear","noiseCancellation":true,"noiseCancellationLevel":"high","batteryHours":30,"connectivity":"bluetooth","originalPrice":2899,"isNewArrival":true}'::jsonb,
            ARRAY['旗舰主动降噪', '约 30 小时续航', '通话清晰'],
            ARRAY['https://example.com/images/products/hdp-001.png'], 'on_sale', 301
        ),
        (
            '20000000-0000-4000-8000-000000000302'::uuid,
            '10000000-0000-4000-8000-000000000001'::uuid,
            'HDP-002', 'Apple AirPods Pro 2', 'ELECTRONICS', 'HEADPHONES', 'Apple',
            '入耳式主动降噪耳机，适合 Apple 设备用户。', 1899.00::numeric, 40,
            '{"form":"in-ear","noiseCancellation":true,"noiseCancellationLevel":"high","batteryHours":6,"connectivity":"bluetooth","ecosystem":"Apple","originalPrice":1999,"isNewArrival":true}'::jsonb,
            ARRAY['Apple 生态协同', '自适应降噪', '便携入耳设计'],
            ARRAY['https://example.com/images/products/hdp-002.png'], 'on_sale', 302
        ),
        (
            '20000000-0000-4000-8000-000000000303'::uuid,
            '10000000-0000-4000-8000-000000000001'::uuid,
            'HDP-003', 'Bose QuietComfort 45', 'ELECTRONICS', 'HEADPHONES', 'Bose',
            '强调长时间佩戴舒适性的经典头戴式降噪耳机。', 2199.00::numeric, 20,
            '{"form":"over-ear","noiseCancellation":true,"noiseCancellationLevel":"high","batteryHours":24,"connectivity":"bluetooth","originalPrice":2499,"isNewArrival":false}'::jsonb,
            ARRAY['经典主动降噪', '轻量舒适佩戴', '约 24 小时续航'],
            ARRAY['https://example.com/images/products/hdp-003.png'], 'on_sale', 303
        ),
        (
            '20000000-0000-4000-8000-000000000304'::uuid,
            '10000000-0000-4000-8000-000000000001'::uuid,
            'HDP-004', 'Sennheiser Momentum 4', 'ELECTRONICS', 'HEADPHONES', 'Sennheiser',
            '注重音质表现并提供超长续航的头戴式无线耳机。', 2599.00::numeric, 15,
            '{"form":"over-ear","noiseCancellation":true,"noiseCancellationLevel":"medium","batteryHours":60,"connectivity":"bluetooth","originalPrice":2999,"isNewArrival":false}'::jsonb,
            ARRAY['高解析音质', '约 60 小时续航', '支持主动降噪'],
            ARRAY['https://example.com/images/products/hdp-004.png'], 'on_sale', 304
        ),
        (
            '20000000-0000-4000-8000-000000000305'::uuid,
            '10000000-0000-4000-8000-000000000001'::uuid,
            'HDP-005', 'Edifier 入门真无线耳机', 'ELECTRONICS', 'HEADPHONES', 'Edifier',
            '价格友好的真无线耳机，满足日常通勤和基础通话。', 299.00::numeric, 100,
            '{"form":"in-ear","noiseCancellation":false,"batteryHours":8,"connectivity":"bluetooth","originalPrice":499,"isNewArrival":false}'::jsonb,
            ARRAY['价格亲民', '日常通勤够用', '小巧便携'],
            ARRAY['https://example.com/images/products/hdp-005.png'], 'on_sale', 305
        ),
        -- 口红：覆盖颜色、妆效、品牌和价格敏感度。
        (
            '20000000-0000-4000-8000-000000000401'::uuid,
            '10000000-0000-4000-8000-000000000006'::uuid,
            'LIP-001', 'YSL 小金条口红 52', 'BEAUTY', 'LIPSTICK', 'YSL',
            '温柔豆沙色搭配哑光妆效，适合日常通勤。', 380.00::numeric, 80,
            '{"shade":"soft-rose","finish":"matte","skinType":"all","originalPrice":420,"isNewArrival":false}'::jsonb,
            ARRAY['温柔豆沙色', '哑光高级妆效', '日常显白'],
            ARRAY['https://example.com/images/products/lip-001.png'], 'on_sale', 401
        ),
        (
            '20000000-0000-4000-8000-000000000402'::uuid,
            '10000000-0000-4000-8000-000000000006'::uuid,
            'LIP-002', 'Dior 烈艳蓝金口红 999', 'BEAUTY', 'LIPSTICK', 'Dior',
            '经典正红色和缎光妆效，适合正式场合。', 360.00::numeric, 60,
            '{"shade":"classic-red","finish":"satin","skinType":"all","originalPrice":400,"isNewArrival":false}'::jsonb,
            ARRAY['经典正红', '缎光质感', '正式场合百搭'],
            ARRAY['https://example.com/images/products/lip-002.png'], 'on_sale', 402
        ),
        (
            '20000000-0000-4000-8000-000000000403'::uuid,
            '10000000-0000-4000-8000-000000000006'::uuid,
            'LIP-003', '3CE 云朵雾面口红 908', 'BEAUTY', 'LIPSTICK', '3CE',
            '柔和奶茶色雾面口红，适合预算有限的年轻用户。', 140.00::numeric, 120,
            '{"shade":"milk-tea","finish":"matte","skinType":"normal","originalPrice":180,"isNewArrival":false}'::jsonb,
            ARRAY['学生预算友好', '温柔奶茶色', '轻盈雾面'],
            ARRAY['https://example.com/images/products/lip-003.png'], 'on_sale', 403
        ),
        (
            '20000000-0000-4000-8000-000000000404'::uuid,
            '10000000-0000-4000-8000-000000000006'::uuid,
            'LIP-004', 'MAC 子弹头口红 Ruby Woo', 'BEAUTY', 'LIPSTICK', 'MAC',
            '复古红色搭配丝绒哑光妆效。', 220.00::numeric, 90,
            '{"shade":"retro-red","finish":"matte","skinType":"all","originalPrice":260,"isNewArrival":false}'::jsonb,
            ARRAY['复古红调', '丝绒质感', '经典色号'],
            ARRAY['https://example.com/images/products/lip-004.png'], 'on_sale', 404
        ),
        (
            '20000000-0000-4000-8000-000000000405'::uuid,
            '10000000-0000-4000-8000-000000000006'::uuid,
            'LIP-005', 'Armani 红管口红 405', 'BEAUTY', 'LIPSTICK', 'Armani',
            '鲜活番茄红色和丝绒哑光妆效，适合提亮肤色。', 320.00::numeric, 50,
            '{"shade":"tomato-red","finish":"matte","skinType":"all","originalPrice":360,"isNewArrival":true}'::jsonb,
            ARRAY['鲜活番茄红', '显白提气色', '丝绒哑光'],
            ARRAY['https://example.com/images/products/lip-005.png'], 'on_sale', 405
        )
)
INSERT INTO products (
    id, merchant_id, sku, name, category_l1, category_l2, brand, description, price, stock,
    attributes, selling_points, image_urls, status, embedding
)
SELECT
    ps.id, ps.merchant_id, ps.sku, ps.name, ps.category_l1, ps.category_l2, ps.brand, ps.description,
    ps.price, ps.stock,
    ps.attributes,
    ps.selling_points, ps.image_urls, ps.status,
    (
        SELECT array_agg(
            CASE WHEN dimension_no = ps.embedding_axis THEN 1.0::real ELSE 0.0::real END
            ORDER BY dimension_no
        )::vector(1024)
        FROM generate_series(1, 1024) AS dimensions(dimension_no)
    )
FROM product_seed AS ps
ON CONFLICT (id) DO UPDATE SET
    merchant_id = EXCLUDED.merchant_id,
    sku = EXCLUDED.sku,
    name = EXCLUDED.name,
    category_l1 = EXCLUDED.category_l1,
    category_l2 = EXCLUDED.category_l2,
    brand = EXCLUDED.brand,
    description = EXCLUDED.description,
    price = EXCLUDED.price,
    stock = EXCLUDED.stock,
    attributes = EXCLUDED.attributes,
    selling_points = EXCLUDED.selling_points,
    image_urls = EXCLUDED.image_urls,
    status = EXCLUDED.status,
    embedding = EXCLUDED.embedding,
    deleted_at = NULL;

INSERT INTO user_static_profiles (
    user_id, category_scores, brand_scores, attribute_preferences,
    price_min, price_max, version, last_event_at
)
VALUES
    (
        '00000000-0000-4000-8000-000000000101',
        '{"HEADPHONES":0.86,"COFFEE_MACHINE":0.28}',
        '{"云雀":0.72,"潮汐":0.55}',
        '{"noiseCancellation":0.90,"longBattery":0.75,"lightweight":0.64}',
        300.00, 1100.00, 3, CURRENT_TIMESTAMP - interval '1 day'
    ),
    (
        '00000000-0000-4000-8000-000000000102',
        '{"COFFEE_MACHINE":0.81,"HEADPHONES":0.22}',
        '{"山岚":0.68,"晨雾":0.62}',
        '{"steamWand":0.80,"compact":0.56}',
        500.00, 2000.00, 2, CURRENT_TIMESTAMP - interval '2 days'
    ),
    (
        '00000000-0000-4000-8000-000000000103',
        '{"RUNNING_SHOES":0.88,"LIPSTICK":0.32}',
        '{"Nike":0.75,"Asics":0.68,"HOKA":0.46}',
        '{"cushionHigh":0.85,"terrainRoad":0.90,"lightweight":0.58}',
        500.00, 1700.00, 4, CURRENT_TIMESTAMP - interval '6 hours'
    ),
    (
        -- 冷启动用户：保留空画像，用于验证无历史行为时的推荐降级。
        '00000000-0000-4000-8000-000000000104',
        '{}', '{}', '{}',
        NULL, NULL, 1, NULL
    ),
    (
        '00000000-0000-4000-8000-000000000105',
        '{"HEADPHONES":0.74,"WATCHES":0.52}',
        '{"Sony":0.61,"Casio":0.55,"Seiko":0.42}',
        '{"noiseCancellation":0.78,"longBattery":0.64,"waterResistance":0.40}',
        300.00, 2600.00, 3, CURRENT_TIMESTAMP - interval '12 hours'
    )
ON CONFLICT (user_id) DO UPDATE SET
    category_scores = EXCLUDED.category_scores,
    brand_scores = EXCLUDED.brand_scores,
    attribute_preferences = EXCLUDED.attribute_preferences,
    price_min = EXCLUDED.price_min,
    price_max = EXCLUDED.price_max,
    version = EXCLUDED.version,
    last_event_at = EXCLUDED.last_event_at;

INSERT INTO user_dynamic_profiles (
    user_id, category_scores, product_scores, session_interests,
    version, last_event_at, expires_at
)
VALUES
    (
        '00000000-0000-4000-8000-000000000101',
        '{"HEADPHONES":0.94}',
        '{"20000000-0000-4000-8000-000000000001":0.82,"20000000-0000-4000-8000-000000000002":0.61}',
        '{"budgetMax":1000,"useCase":"commute","required":{"noiseCancellation":true}}',
        5, CURRENT_TIMESTAMP - interval '20 minutes', CURRENT_TIMESTAMP + interval '7 days'
    ),
    (
        '00000000-0000-4000-8000-000000000102',
        '{"COFFEE_MACHINE":0.88}',
        '{"20000000-0000-4000-8000-000000000005":0.75}',
        '{"useCase":"home","experience":"beginner"}',
        2, CURRENT_TIMESTAMP - interval '2 hours', CURRENT_TIMESTAMP + interval '7 days'
    ),
    (
        '00000000-0000-4000-8000-000000000103',
        '{"RUNNING_SHOES":0.93}',
        '{"20000000-0000-4000-8000-000000000101":0.76,"20000000-0000-4000-8000-000000000106":0.58,"20000000-0000-4000-8000-000000000110":0.64}',
        '{"budgetMax":1000,"terrain":"road","useCase":"daily-training"}',
        6, CURRENT_TIMESTAMP - interval '15 minutes', CURRENT_TIMESTAMP + interval '7 days'
    ),
    (
        '00000000-0000-4000-8000-000000000104',
        '{}', '{}', '{}',
        1, NULL, CURRENT_TIMESTAMP + interval '7 days'
    ),
    (
        '00000000-0000-4000-8000-000000000105',
        '{"HEADPHONES":0.82,"WATCHES":0.45}',
        '{"20000000-0000-4000-8000-000000000301":0.72,"20000000-0000-4000-8000-000000000202":0.38}',
        '{"useCase":"commute","preferredForm":"over-ear"}',
        4, CURRENT_TIMESTAMP - interval '1 hour', CURRENT_TIMESTAMP + interval '7 days'
    )
ON CONFLICT (user_id) DO UPDATE SET
    category_scores = EXCLUDED.category_scores,
    product_scores = EXCLUDED.product_scores,
    session_interests = EXCLUDED.session_interests,
    version = EXCLUDED.version,
    last_event_at = EXCLUDED.last_event_at,
    expires_at = EXCLUDED.expires_at;

INSERT INTO sessions (
    id, user_id, status, conversation_summary, last_turn_id,
    started_at, last_active_at, ended_at
)
VALUES
    (
        '30000000-0000-4000-8000-000000000001',
        '00000000-0000-4000-8000-000000000101',
        'active', '用户想找千元以内、适合通勤且带主动降噪的耳机。',
        '31000000-0000-4000-8000-000000000002',
        CURRENT_TIMESTAMP - interval '10 minutes', CURRENT_TIMESTAMP - interval '2 minutes', NULL
    ),
    (
        '30000000-0000-4000-8000-000000000002',
        '00000000-0000-4000-8000-000000000102',
        'closed', '用户查询了家用半自动咖啡机并完成下单。',
        '32000000-0000-4000-8000-000000000001',
        CURRENT_TIMESTAMP - interval '2 days', CURRENT_TIMESTAMP - interval '2 days',
        CURRENT_TIMESTAMP - interval '2 days' + interval '8 minutes'
    ),
    (
        '30000000-0000-4000-8000-000000000003',
        '00000000-0000-4000-8000-000000000104',
        'closed', '冷启动用户提出写周报请求，系统识别为不支持的请求并使用固定兜底回复。',
        '33000000-0000-4000-8000-000000000001',
        CURRENT_TIMESTAMP - interval '1 hour', CURRENT_TIMESTAMP - interval '58 minutes',
        CURRENT_TIMESTAMP - interval '58 minutes'
    ),
    (
        '30000000-0000-4000-8000-000000000004',
        '00000000-0000-4000-8000-000000000103',
        'closed', '用户经过逐轮澄清，确定购买千元内用于日常路跑的跑鞋。',
        '34000000-0000-4000-8000-000000000003',
        CURRENT_TIMESTAMP - interval '30 minutes', CURRENT_TIMESTAMP - interval '20 minutes',
        CURRENT_TIMESTAMP - interval '20 minutes'
    )
ON CONFLICT (id) DO UPDATE SET
    user_id = EXCLUDED.user_id,
    status = EXCLUDED.status,
    conversation_summary = EXCLUDED.conversation_summary,
    last_turn_id = EXCLUDED.last_turn_id,
    started_at = EXCLUDED.started_at,
    last_active_at = EXCLUDED.last_active_at,
    ended_at = EXCLUDED.ended_at;

INSERT INTO session_messages (
    id, session_id, turn_id, seq, role, message_type, content, metadata
)
VALUES
    (
        '40000000-0000-4000-8000-000000000001',
        '30000000-0000-4000-8000-000000000001',
        '31000000-0000-4000-8000-000000000001', 0, 'user', 'transcript',
        '我想买一个通勤用的降噪耳机，预算一千以内。',
        '{"asrModel":"qwen-audio-3.0-asr-flash-streaming"}'
    ),
    (
        '40000000-0000-4000-8000-000000000002',
        '30000000-0000-4000-8000-000000000001',
        '31000000-0000-4000-8000-000000000001', 1, 'assistant', 'product_cards',
        '已为你找到 3 款耳机。',
        '{"productIds":["20000000-0000-4000-8000-000000000001","20000000-0000-4000-8000-000000000002","20000000-0000-4000-8000-000000000003"]}'
    ),
    (
        '40000000-0000-4000-8000-000000000003',
        '30000000-0000-4000-8000-000000000001',
        '31000000-0000-4000-8000-000000000002', 0, 'user', 'transcript',
        '就买第一款。', '{}'
    ),
    (
        '40000000-0000-4000-8000-000000000004',
        '30000000-0000-4000-8000-000000000001',
        '31000000-0000-4000-8000-000000000002', 1, 'assistant', 'order',
        '已生成待确认订单，请确认是否购买云雀 Air 降噪耳机。', '{}'
    ),
    (
        '40000000-0000-4000-8000-000000000101',
        '30000000-0000-4000-8000-000000000003',
        '33000000-0000-4000-8000-000000000001', 0, 'user', 'transcript',
        '帮我写一份周报。', '{}'
    ),
    (
        '40000000-0000-4000-8000-000000000102',
        '30000000-0000-4000-8000-000000000003',
        '33000000-0000-4000-8000-000000000001', 1, 'assistant', 'text',
        '抱歉，我目前只能协助商品推荐、查询、对比和下单。你可以告诉我想买什么商品。',
        '{"fallback":true,"intent":"UNSUPPORTED_REQUEST"}'
    ),
    (
        '40000000-0000-4000-8000-000000000103',
        '30000000-0000-4000-8000-000000000004',
        '34000000-0000-4000-8000-000000000001', 0, 'user', 'transcript',
        '我想买双跑鞋。', '{}'
    ),
    (
        '40000000-0000-4000-8000-000000000104',
        '30000000-0000-4000-8000-000000000004',
        '34000000-0000-4000-8000-000000000001', 1, 'assistant', 'text',
        '你的预算上限是多少？', '{"clarificationStatus":"ASK","slot":"budgetMax"}'
    ),
    (
        '40000000-0000-4000-8000-000000000105',
        '30000000-0000-4000-8000-000000000004',
        '34000000-0000-4000-8000-000000000002', 0, 'user', 'transcript',
        '一千元以内。', '{}'
    ),
    (
        '40000000-0000-4000-8000-000000000106',
        '30000000-0000-4000-8000-000000000004',
        '34000000-0000-4000-8000-000000000002', 1, 'assistant', 'text',
        '主要用于什么场景？', '{"clarificationStatus":"ASK","slot":"useCase"}'
    ),
    (
        '40000000-0000-4000-8000-000000000107',
        '30000000-0000-4000-8000-000000000004',
        '34000000-0000-4000-8000-000000000003', 0, 'user', 'transcript',
        '主要是日常路跑训练。', '{}'
    ),
    (
        '40000000-0000-4000-8000-000000000108',
        '30000000-0000-4000-8000-000000000004',
        '34000000-0000-4000-8000-000000000003', 1, 'assistant', 'product_cards',
        '已为你找到 3 双符合千元预算的日常路跑鞋。',
        '{"productIds":["20000000-0000-4000-8000-000000000101","20000000-0000-4000-8000-000000000110","20000000-0000-4000-8000-000000000106"]}'
    )
ON CONFLICT (id) DO UPDATE SET
    session_id = EXCLUDED.session_id,
    turn_id = EXCLUDED.turn_id,
    seq = EXCLUDED.seq,
    role = EXCLUDED.role,
    message_type = EXCLUDED.message_type,
    content = EXCLUDED.content,
    metadata = EXCLUDED.metadata;

INSERT INTO orders (
    id, user_id, merchant_id, product_id, session_id, source_turn_id,
    idempotency_key, status, quantity, unit_price, merchant_snapshot,
    product_snapshot, failure_reason, expires_at, confirmed_at, created_at
)
VALUES
    (
        '50000000-0000-4000-8000-000000000001',
        '00000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000001',
        '20000000-0000-4000-8000-000000000001',
        '30000000-0000-4000-8000-000000000001',
        '31000000-0000-4000-8000-000000000002',
        'voice-order-session-001-turn-002', 'pending', 1, 699.00,
        '{"merchantId":"10000000-0000-4000-8000-000000000001","name":"声动数码"}',
        '{"productId":"20000000-0000-4000-8000-000000000001","sku":"HEADPHONE-A1","name":"云雀 Air 降噪耳机","categoryL1":"ELECTRONICS","categoryL2":"HEADPHONES","imageUrl":"https://example.com/images/products/headphone-a1-1.png"}',
        NULL, CURRENT_TIMESTAMP + interval '15 minutes', NULL, CURRENT_TIMESTAMP
    ),
    (
        '50000000-0000-4000-8000-000000000002',
        '00000000-0000-4000-8000-000000000102',
        '10000000-0000-4000-8000-000000000002',
        '20000000-0000-4000-8000-000000000005',
        '30000000-0000-4000-8000-000000000002',
        '32000000-0000-4000-8000-000000000001',
        'voice-order-session-002-turn-001', 'success', 1, 1699.00,
        '{"merchantId":"10000000-0000-4000-8000-000000000002","name":"日常家电"}',
        '{"productId":"20000000-0000-4000-8000-000000000005","sku":"COFFEE-M2","name":"山岚半自动咖啡机","categoryL1":"HOME_APPLIANCES","categoryL2":"COFFEE_MACHINE","imageUrl":"https://example.com/images/products/coffee-m2-1.png"}',
        NULL, CURRENT_TIMESTAMP - interval '2 days' + interval '15 minutes',
        CURRENT_TIMESTAMP - interval '2 days' + interval '5 minutes',
        CURRENT_TIMESTAMP - interval '2 days'
    ),
    (
        '50000000-0000-4000-8000-000000000003',
        '00000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000001',
        '20000000-0000-4000-8000-000000000002',
        NULL, NULL,
        'voice-order-expired-demo-001', 'fail', 1, 999.00,
        '{"merchantId":"10000000-0000-4000-8000-000000000001","name":"声动数码"}',
        '{"productId":"20000000-0000-4000-8000-000000000002","sku":"HEADPHONE-B2","name":"潮汐 Pro 真无线耳机","categoryL1":"ELECTRONICS","categoryL2":"HEADPHONES","imageUrl":"https://example.com/images/products/headphone-b2-1.png"}',
        'confirmation_timeout', CURRENT_TIMESTAMP - interval '3 days' + interval '15 minutes',
        NULL, CURRENT_TIMESTAMP - interval '3 days'
    )
ON CONFLICT (id) DO UPDATE SET
    user_id = EXCLUDED.user_id,
    merchant_id = EXCLUDED.merchant_id,
    product_id = EXCLUDED.product_id,
    session_id = EXCLUDED.session_id,
    source_turn_id = EXCLUDED.source_turn_id,
    idempotency_key = EXCLUDED.idempotency_key,
    quantity = EXCLUDED.quantity,
    unit_price = EXCLUDED.unit_price,
    merchant_snapshot = EXCLUDED.merchant_snapshot,
    product_snapshot = EXCLUDED.product_snapshot;

INSERT INTO session_states (
    id, session_id, turn_id, workflow_state, user_profile_snapshot,
    pending_order_id, langgraph_checkpoint_id
)
VALUES
    (
        '60000000-0000-4000-8000-000000000001',
        '30000000-0000-4000-8000-000000000001',
        '31000000-0000-4000-8000-000000000001',
        '{"utterance":"我想买一个通勤用的降噪耳机，预算一千以内。","intents":[{"type":"PRODUCT_RECOMMENDATION","confidence":0.98}],"actionQueue":["PRODUCT_RECOMMENDATION"],"productCategory":"HEADPHONES","requiredSlots":["budget","useCase","noiseCancellation"],"slots":{"budget":{"max":1000},"useCase":"commute","noiseCancellation":true},"clarificationStatus":"READY","productCards":[{"productId":"20000000-0000-4000-8000-000000000001"},{"productId":"20000000-0000-4000-8000-000000000002"},{"productId":"20000000-0000-4000-8000-000000000003"}],"emotionStyle":"warm-professional"}',
        '{"static":{"categoryScores":{"HEADPHONES":0.86},"priceRange":{"min":300,"max":1100}},"dynamic":{"categoryScores":{"HEADPHONES":0.94},"useCase":"commute"},"capturedAt":"2026-08-02T00:00:00Z"}',
        NULL, 'demo-checkpoint-001'
    ),
    (
        '60000000-0000-4000-8000-000000000002',
        '30000000-0000-4000-8000-000000000001',
        '31000000-0000-4000-8000-000000000002',
        '{"utterance":"就买第一款。","intents":[{"type":"PRODUCT_ORDER","action":"CREATE","confidence":0.99}],"actionQueue":["PRODUCT_ORDER"],"pendingOrder":{"orderId":"50000000-0000-4000-8000-000000000001","status":"pending","expiresInSeconds":900}}',
        '{"static":{"categoryScores":{"HEADPHONES":0.86},"priceRange":{"min":300,"max":1100}},"dynamic":{"categoryScores":{"HEADPHONES":0.94},"useCase":"commute"},"capturedAt":"2026-08-02T00:02:00Z"}',
        '50000000-0000-4000-8000-000000000001', 'demo-checkpoint-002'
    ),
    (
        '60000000-0000-4000-8000-000000000101',
        '30000000-0000-4000-8000-000000000003',
        '33000000-0000-4000-8000-000000000001',
        '{"utterance":"帮我写一份周报。","intents":[{"type":"UNSUPPORTED_REQUEST","confidence":0.99}],"actionQueue":["UNSUPPORTED_REQUEST"],"finalReply":"抱歉，我目前只能协助商品推荐、查询、对比和下单。你可以告诉我想买什么商品。"}',
        '{"static":{"categoryScores":{}},"dynamic":{"categoryScores":{}},"coldStart":true,"capturedAt":"2026-08-02T01:00:00Z"}',
        NULL, 'demo-checkpoint-unsupported-001'
    ),
    (
        '60000000-0000-4000-8000-000000000102',
        '30000000-0000-4000-8000-000000000004',
        '34000000-0000-4000-8000-000000000001',
        '{"utterance":"我想买双跑鞋。","intents":[{"type":"PRODUCT_RECOMMENDATION","confidence":0.98}],"actionQueue":["PRODUCT_RECOMMENDATION"],"productCategory":"RUNNING_SHOES","requiredSlots":["budgetMax","useCase"],"slots":{},"clarificationStatus":"ASK","missingSlots":["budgetMax","useCase"],"pendingQuestion":{"slot":"budgetMax","question":"你的预算上限是多少？"}}',
        '{"static":{"categoryScores":{"RUNNING_SHOES":0.88},"priceRange":{"min":500,"max":1700}},"dynamic":{"categoryScores":{"RUNNING_SHOES":0.93}},"capturedAt":"2026-08-02T01:30:00Z"}',
        NULL, 'demo-checkpoint-clarify-001'
    ),
    (
        '60000000-0000-4000-8000-000000000103',
        '30000000-0000-4000-8000-000000000004',
        '34000000-0000-4000-8000-000000000002',
        '{"utterance":"一千元以内。","intents":[{"type":"PRODUCT_RECOMMENDATION","confidence":0.97}],"actionQueue":["PRODUCT_RECOMMENDATION"],"productCategory":"RUNNING_SHOES","requiredSlots":["budgetMax","useCase"],"slots":{"budgetMax":1000},"clarificationStatus":"ASK","missingSlots":["useCase"],"pendingQuestion":{"slot":"useCase","question":"主要用于什么场景？"}}',
        '{"static":{"categoryScores":{"RUNNING_SHOES":0.88},"priceRange":{"min":500,"max":1700}},"dynamic":{"categoryScores":{"RUNNING_SHOES":0.93}},"capturedAt":"2026-08-02T01:32:00Z"}',
        NULL, 'demo-checkpoint-clarify-002'
    ),
    (
        '60000000-0000-4000-8000-000000000104',
        '30000000-0000-4000-8000-000000000004',
        '34000000-0000-4000-8000-000000000003',
        '{"utterance":"主要是日常路跑训练。","intents":[{"type":"PRODUCT_RECOMMENDATION","confidence":0.98}],"actionQueue":["PRODUCT_RECOMMENDATION"],"productCategory":"RUNNING_SHOES","requiredSlots":["budgetMax","useCase"],"slots":{"budgetMax":1000,"useCase":"daily-road-running"},"clarificationStatus":"READY","missingSlots":[],"productCards":[{"productId":"20000000-0000-4000-8000-000000000101"},{"productId":"20000000-0000-4000-8000-000000000110"},{"productId":"20000000-0000-4000-8000-000000000106"}],"emotionStyle":"encouraging-professional"}',
        '{"static":{"categoryScores":{"RUNNING_SHOES":0.88},"priceRange":{"min":500,"max":1700}},"dynamic":{"categoryScores":{"RUNNING_SHOES":0.93},"budgetMax":1000,"useCase":"daily-road-running"},"capturedAt":"2026-08-02T01:35:00Z"}',
        NULL, 'demo-checkpoint-clarify-003'
    )
ON CONFLICT (id) DO UPDATE SET
    session_id = EXCLUDED.session_id,
    turn_id = EXCLUDED.turn_id,
    workflow_state = EXCLUDED.workflow_state,
    user_profile_snapshot = EXCLUDED.user_profile_snapshot,
    pending_order_id = EXCLUDED.pending_order_id,
    langgraph_checkpoint_id = EXCLUDED.langgraph_checkpoint_id;

COMMIT;
