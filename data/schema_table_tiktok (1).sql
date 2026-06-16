
-- bảng task_logs lưu trữ lịch sử thực thi của các tác vụ định kỳ (scrape_posts, update_metrics, generate_analytics) để theo dõi hiệu suất và phát hiện lỗi
CREATE TABLE task_logs (
        id INTEGER NOT NULL,
        task_name VARCHAR(100) NOT NULL,
        status VARCHAR(20),
        started_at DATETIME,
        completed_at DATETIME,
        duration_seconds FLOAT,
        items_processed INTEGER,
        errors_count INTEGER,
        error_message TEXT,
        created_at DATETIME,
        PRIMARY KEY (id)
);
CREATE INDEX idx_task_name_date ON task_logs (task_name, created_at);

-- bảng tiktok_sessions lưu trữ thông tin phiên đăng nhập/cookie/device fingerprint của TikTok để quản lý truy cập dữ liệu
CREATE TABLE tiktok_sessions (
        id INTEGER NOT NULL,
        ms_token TEXT NOT NULL,
        is_active BOOLEAN NOT NULL,
        is_valid BOOLEAN NOT NULL,
        last_verified DATETIME,
        expires_at DATETIME,
        created_at DATETIME,
        PRIMARY KEY (id)
);

-- bảng sources lưu trữ thông tin về các nguồn dữ liệu TikTok mà người dùng theo dõi (user, hashtag, sound, keyword...)
CREATE TABLE sources (
        id INTEGER NOT NULL,
        source_type VARCHAR(10) NOT NULL,
        identifier VARCHAR(100) NOT NULL,
        display_name VARCHAR(255),
        tiktok_url VARCHAR(255),
        follower_count INTEGER,
        is_active BOOLEAN,
        
        max_days_old INTEGER,
        is_accessible BOOLEAN,
        created_at DATETIME,
        last_scraped DATETIME,
        next_scrape DATETIME,
        schedule_tier INTEGER DEFAULT NULL,
        schedule_override_minutes INTEGER DEFAULT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_user_source UNIQUE (source_type, identifier),
        CONSTRAINT ck_sources_type CHECK (source_type IN ('user', 'hashtag', 'sound', 'keyword'))
);
CREATE INDEX idx_source_user_active ON sources (is_active);
CREATE INDEX idx_source_accessible ON sources (is_accessible);
CREATE INDEX idx_source_next_scrape ON sources (next_scrape);

CREATE TABLE IF NOT EXISTS hashtags (
    id  INTEGER      NOT NULL,
    tag VARCHAR(100) NOT NULL,   -- lowercase, không có dấu #
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_hashtags_tag ON hashtags (tag);

-- bảng post_hashtags liên kết posts với hashtags (many-to-many)
CREATE TABLE IF NOT EXISTS post_hashtags (
    post_id    INTEGER NOT NULL,
    hashtag_id INTEGER NOT NULL,
    PRIMARY KEY (post_id, hashtag_id),
    FOREIGN KEY (post_id)    REFERENCES posts    (id) ON DELETE CASCADE,
    FOREIGN KEY (hashtag_id) REFERENCES hashtags (id) ON DELETE CASCADE
);
-- index chiều ngược: từ hashtag → posts (dùng cho query theo tag)
CREATE INDEX IF NOT EXISTS idx_post_hashtags_hashtag ON post_hashtags (hashtag_id);

-- bảng posts lưu trữ thông tin video TikTok của từng nguồn cùng trạng thái theo dõi metric
CREATE TABLE posts (
        id INTEGER NOT NULL,
        source_id INTEGER NOT NULL,
        tiktok_video_id VARCHAR(100) NOT NULL,
        tiktok_url VARCHAR(500) NOT NULL,
        description TEXT,
        duration_seconds INTEGER,
        cover_url VARCHAR(500),
        posted_at DATETIME NOT NULL,
        created_at DATETIME,
        is_tracked BOOLEAN,
        tracking_until DATETIME,
        is_deleted BOOLEAN,
        last_metric_update DATETIME,
        metric_tier VARCHAR(20) NOT NULL DEFAULT 'bootstrap',
        next_metric_update DATETIME,
        last_engagement_velocity FLOAT,
        cold_check_count INTEGER NOT NULL DEFAULT 0,
        metric_scan_miss_count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (id),
        FOREIGN KEY(source_id) REFERENCES sources (id)
);
CREATE INDEX idx_post_last_update ON posts (last_metric_update);
CREATE INDEX idx_post_source ON posts (source_id);
CREATE UNIQUE INDEX ix_posts_tiktok_video_id ON posts (tiktok_video_id);
CREATE INDEX idx_post_posted_at ON posts (posted_at);
CREATE INDEX idx_post_metric_due ON posts (is_tracked, next_metric_update);

-- bảng analytics_cache lưu kết quả tổng hợp chỉ số (likes, shares, comments, views) của từng source theo ngày
CREATE TABLE analytics_cache (
        id INTEGER NOT NULL,
        source_id INTEGER NOT NULL,
        date DATETIME NOT NULL,
        total_posts INTEGER,
        total_likes INTEGER,
        total_shares INTEGER,
        total_comments INTEGER,
        total_views INTEGER,
        avg_likes_per_post FLOAT,
        top_post_id VARCHAR(100),
        growth_rate FLOAT,
        cached_at DATETIME,
        PRIMARY KEY (id),
        CONSTRAINT uq_analytics_cache UNIQUE (source_id, date),
        FOREIGN KEY(source_id) REFERENCES sources (id)
);
CREATE INDEX idx_analytics_source_date ON analytics_cache (source_id, date);

-- bảng post_metrics lưu lịch sử thay đổi chỉ số (likes, shares, comments, views, plays) theo thời gian
CREATE TABLE post_metrics (
        id              INTEGER NOT NULL,
        post_id         INTEGER NOT NULL,
        likes_count     INTEGER,
        shares_count    INTEGER,
        comments_count  INTEGER,
        views_count     INTEGER,
        bookmarks_count INTEGER,  -- lượt lưu / yêu thích (collectCount)
        reposts_count   INTEGER,  -- lượt repost
        recorded_at     DATETIME,
        job_id          INTEGER REFERENCES pipeline_jobs(id) ON DELETE SET NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(post_id) REFERENCES posts (id)
);
CREATE INDEX ix_post_metrics_recorded_at ON post_metrics (recorded_at);
CREATE INDEX idx_metric_post_date ON post_metrics (post_id, recorded_at);
CREATE INDEX idx_post_metrics_job_time ON post_metrics (job_id, recorded_at);

-- bảng comments lưu bình luận trên các video TikTok
CREATE TABLE comments (
        id INTEGER NOT NULL,
        post_id INTEGER NOT NULL,
        parent_id INTEGER,
        tiktok_comment_id VARCHAR(100) NOT NULL,
        commenter_id VARCHAR(50),
        commenter_name VARCHAR(255),
        comment_text TEXT,
        likes_count INTEGER,
        reply_count INTEGER,
        created_at DATETIME,
        last_updated DATETIME NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(post_id) REFERENCES posts (id),
        FOREIGN KEY(parent_id) REFERENCES comments (id),
        UNIQUE (tiktok_comment_id)
);
CREATE INDEX idx_comment_tiktok_id ON comments (tiktok_comment_id);
CREATE INDEX idx_comment_post ON comments (post_id);

-- bảng pipeline_jobs theo dõi toàn bộ pipeline (scrape_24h, scraper_job, update_metric, analytics)
CREATE TABLE pipeline_jobs (
    id              INTEGER PRIMARY KEY,
    job_type        VARCHAR(20) NOT NULL DEFAULT 'scraper_job'
                    CHECK (job_type IN ('scrape_24h', 'scraper_job', 'update_metric', 'analytics')),

    source_id       INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    session_id      INTEGER REFERENCES tiktok_sessions(id) ON DELETE SET NULL,

    status          VARCHAR(10) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'done', 'failed')),

    posts_found     INTEGER NOT NULL DEFAULT 0,
    posts_new       INTEGER NOT NULL DEFAULT 0,
    items_total     INTEGER NOT NULL DEFAULT 0,
    items_updated   INTEGER NOT NULL DEFAULT 0,
    items_failed    INTEGER NOT NULL DEFAULT 0,

    error_message   TEXT,
    started_at      DATETIME,
    finished_at     DATETIME
);

CREATE INDEX idx_pipeline_jobs_source_time ON pipeline_jobs (source_id, started_at);
CREATE INDEX idx_pipeline_jobs_type_status ON pipeline_jobs (job_type, status, started_at);

-- bảng pipeline_logs lưu log chi tiết cho từng pipeline job để debug
CREATE TABLE pipeline_logs (
    id              INTEGER PRIMARY KEY,

    job_id          INTEGER REFERENCES pipeline_jobs(id) ON DELETE SET NULL,
    source_id       INTEGER REFERENCES sources(id),
    log_level       VARCHAR(20),
    message         TEXT NOT NULL,
    error_type      VARCHAR(100),
    error_details   TEXT,

    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_pipeline_logs_job     ON pipeline_logs (job_id, created_at);
CREATE INDEX idx_pipeline_logs_source  ON pipeline_logs (source_id, created_at);
CREATE INDEX idx_pipeline_logs_level   ON pipeline_logs (log_level, created_at);
