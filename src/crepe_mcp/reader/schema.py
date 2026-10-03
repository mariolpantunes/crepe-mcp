"""SQLite schema of a document store, shared by the PDF and the pandoc builders."""

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE pages (
    page INTEGER PRIMARY KEY,
    width REAL NOT NULL,
    height REAL NOT NULL,
    label TEXT NOT NULL,
    source TEXT NOT NULL,
    images INTEGER NOT NULL,
    drawings INTEGER NOT NULL,
    markup_annots INTEGER NOT NULL,
    chars INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE lines (
    id INTEGER PRIMARY KEY,
    page INTEGER NOT NULL REFERENCES pages (page),
    seq INTEGER NOT NULL,
    region TEXT NOT NULL,
    col INTEGER NOT NULL,
    x0 REAL NOT NULL,
    y0 REAL NOT NULL,
    x1 REAL NOT NULL,
    y1 REAL NOT NULL,
    text TEXT NOT NULL,
    font TEXT NOT NULL,
    size REAL NOT NULL,
    bold INTEGER NOT NULL,
    italic INTEGER NOT NULL,
    mono INTEGER NOT NULL,
    color INTEGER NOT NULL
);
CREATE INDEX lines_by_page ON lines (page, seq);
CREATE INDEX lines_by_region ON lines (region, page);
CREATE TABLE paragraphs (
    id INTEGER PRIMARY KEY,
    page INTEGER NOT NULL,
    last_page INTEGER NOT NULL,
    kind TEXT NOT NULL,
    section INTEGER NOT NULL,
    first_line INTEGER NOT NULL,
    last_line INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX paragraphs_by_page ON paragraphs (page, id);
CREATE TABLE sections (
    id INTEGER PRIMARY KEY,
    parent INTEGER NOT NULL,
    level INTEGER NOT NULL,
    number TEXT NOT NULL,
    title TEXT NOT NULL,
    page INTEGER NOT NULL,
    paragraph INTEGER NOT NULL
);
CREATE VIRTUAL TABLE paragraph_search USING fts5 (
    text, content = 'paragraphs', content_rowid = 'id', tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE segments (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    first_page INTEGER NOT NULL,
    last_page INTEGER NOT NULL,
    label TEXT NOT NULL,
    evidence TEXT NOT NULL
);
CREATE TABLE structure (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE assets (
    segment INTEGER NOT NULL,
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    number TEXT NOT NULL,
    label TEXT NOT NULL,
    page INTEGER NOT NULL,
    last_page INTEGER NOT NULL,
    x0 REAL,
    y0 REAL,
    x1 REAL,
    y1 REAL,
    caption TEXT NOT NULL,
    content TEXT NOT NULL,
    content_format TEXT NOT NULL,
    method TEXT NOT NULL,
    confidence TEXT NOT NULL,
    paragraph INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    PRIMARY KEY (segment, id)
);
CREATE INDEX assets_by_kind ON assets (kind, seq);
CREATE TABLE mentions (
    id INTEGER PRIMARY KEY,
    segment INTEGER NOT NULL,
    asset TEXT NOT NULL,
    paragraph INTEGER NOT NULL,
    page INTEGER NOT NULL,
    text TEXT NOT NULL,
    strength TEXT NOT NULL
);
CREATE INDEX mentions_by_asset ON mentions (segment, asset, paragraph);
CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
