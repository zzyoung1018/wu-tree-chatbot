-- story.db：由 JSONL 生成的检索库，可随时删掉重建
PRAGMA foreign_keys = ON;

CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- 父：条目卡。不进关键词索引，也没有向量
CREATE TABLE entries (
  unit_id        TEXT PRIMARY KEY,                  -- shbl:story-002
  entry_no       INTEGER NOT NULL UNIQUE,
  title          TEXT NOT NULL,
  district       TEXT NOT NULL,
  location       TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN ('extant', 'gone', 'partial')),
  species        TEXT NOT NULL,                     -- JSON 数组
  official_codes TEXT NOT NULL,                     -- JSON 数组
  tree_ids       TEXT NOT NULL,                     -- JSON 数组
  site_ids       TEXT NOT NULL,                     -- JSON 数组
  review_status  TEXT NOT NULL,
  card           TEXT NOT NULL                      -- entries_story.jsonl 的整行，原样
);

CREATE TABLE items (
  unit_id       TEXT NOT NULL REFERENCES entries(unit_id),
  item_no       INTEGER NOT NULL,
  location      TEXT NOT NULL,
  species       TEXT NOT NULL,                      -- JSON 数组
  status        TEXT NOT NULL CHECK (status IN ('extant', 'gone')),
  status_source TEXT NOT NULL,
  PRIMARY KEY (unit_id, item_no)
);

-- 子：检索单位
CREATE TABLE chunks (
  chunk_id       TEXT PRIMARY KEY,                  -- shbl:story-002:body-2
  unit_id        TEXT NOT NULL REFERENCES entries(unit_id),
  seq            INTEGER NOT NULL,                  -- 在条目卡 children 里的位置，从 1 起
  item_no        INTEGER NOT NULL,
  content_type   TEXT NOT NULL CHECK (content_type IN ('story', 'fieldnote', 'interview', 'background')),
  context_header TEXT NOT NULL,
  text           TEXT NOT NULL,
  printed_pages  TEXT NOT NULL,                     -- JSON 数组
  review_status  TEXT NOT NULL,
  content_hash   TEXT NOT NULL,
  chunk          TEXT NOT NULL,                     -- chunks_story.jsonl 的整行，原样
  UNIQUE (unit_id, seq),
  FOREIGN KEY (unit_id, item_no) REFERENCES items(unit_id, item_no)
);

-- 关键词索引：grams 是 context_header、text、search_terms 的字符二元组，用空格隔开
CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, grams, tokenize = 'unicode61');

-- 向量：向量化那一步再填；content_hash 与 chunks 表不一致的向量作废
CREATE TABLE chunk_vectors (
  chunk_id     TEXT PRIMARY KEY REFERENCES chunks(chunk_id),
  model        TEXT NOT NULL,                       -- 例如 bge-m3
  dim          INTEGER NOT NULL,                    -- bge-m3 为 1024
  content_hash TEXT NOT NULL,
  vec          BLOB NOT NULL                        -- float32 小端，已归一化
);

-- 图片：只存元数据，文件在 pilot/images/story/
CREATE TABLE images (
  image_id      TEXT PRIMARY KEY,                   -- shbl-img-p013-1
  unit_id       TEXT NOT NULL REFERENCES entries(unit_id),
  seq           INTEGER NOT NULL,                   -- 在条目卡 images 里的位置，从 1 起
  item_no       INTEGER,
  role          TEXT NOT NULL CHECK (role IN ('index', 'story')),
  file          TEXT NOT NULL,
  printed_page  INTEGER NOT NULL,
  kind          TEXT NOT NULL,
  caption       TEXT,
  credit        TEXT,
  rights_status TEXT NOT NULL,
  UNIQUE (unit_id, seq)
);
