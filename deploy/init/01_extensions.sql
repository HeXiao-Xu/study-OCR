-- =====================================================================
-- 初始化 SQL：仅在 PostgreSQL 数据卷**首次创建**时执行一次
-- （docker-entrypoint-initdb.d 语义：卷里已有数据目录就跳过）
--
-- 想让改动生效必须重建数据卷：
--   docker compose -f deploy/docker-compose.dev.yml down -v
-- 这会清空开发库，执行前确认没有要保留的数据。
-- =====================================================================

-- pgcrypto：提供 digest()/hmac()/gen_random_bytes() 等函数。
-- 本课题的密码运算全部在 Python 侧（gmssl / cryptography）完成，
-- 这里只是预留：将来做审计链锚定或数据库侧摘要校验时不必再改镜像初始化。
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 校验：扩展装上了就输出一行，便于从 `docker compose logs postgres` 确认
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto') THEN
        RAISE NOTICE '[init] pgcrypto ready';
    ELSE
        RAISE WARNING '[init] pgcrypto 未安装成功';
    END IF;
END
$$;