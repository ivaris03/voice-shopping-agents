# 数据库迁移

通过仓库根目录的 `pnpm db:migrate` 执行迁移。执行器使用 PostgreSQL advisory lock 串行化部署，
并将已应用版本、SHA-256 校验和和时间写入 `voice_shopping_schema_migrations`。

新迁移必须命名为 `YYYYMMDD_description.sql`，按文件名顺序执行。迁移文件由执行器包在一个
事务中，因此不能自行包含顶层 `BEGIN;` 或 `COMMIT;`。一旦任何环境已应用某个版本，绝不能
修改该文件；修复必须新增一个迁移。

`00000000_initial_schema.sql` 是不可变的初始 schema。`sql/schema.sql` 保留为可阅读的当前快照，
本地演示数据由 `pnpm db:migrate --seed-demo` 单独加载，不会在生产迁移中自动写入演示数据。

历史 profile 迁移会把可映射的分类、品牌和最近浏览数据写入当前 profile 表；旧的属性偏好和
会话兴趣没有无损目标，因此旧表改名为 `legacy_user_static_profiles`、
`legacy_user_dynamic_profiles`，不自动删除。
