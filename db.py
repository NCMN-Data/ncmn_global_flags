"""SQLite 저장소: 국가 설정(확대 이미지·필요 깃발 수·시도 목록)과 깃발 등록 정보."""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd
from PIL import Image, ImageOps

DATA_DIR = Path(os.environ.get("NCMN_DATA_DIR", Path(__file__).parent / "data"))
DB_PATH = DATA_DIR / "flags.db"
COUNTRY_IMAGE_DIR = DATA_DIR / "country_images"
PHOTO_DIR = DATA_DIR / "photos"

COUNTRY_IMAGE_MAX_PX = 1800
PHOTO_MAX_PX = 1600

# 국가 설정이 아직 없을 때 쓰는 기본 시도 목록
DEFAULT_PROVINCES = {
    "KOR": [
        "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
        "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원특별자치도",
        "충청북도", "충청남도", "전북특별자치도", "전라남도", "경상북도",
        "경상남도", "제주특별자치도",
    ],
}

REGISTRATION_FIELDS = [
    "iso3", "x", "y", "province", "flag_count", "city", "address", "organization",
    "contact_name", "contact_phone", "email", "registrant_name", "photo_path",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS countries (
    iso3 TEXT PRIMARY KEY,
    required_flags INTEGER NOT NULL DEFAULT 0,
    image_path TEXT,
    provinces TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    iso3 TEXT NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    province TEXT NOT NULL,
    flag_count INTEGER NOT NULL DEFAULT 0,
    city TEXT,
    address TEXT,
    organization TEXT,
    contact_name TEXT,
    contact_phone TEXT,
    email TEXT,
    registrant_name TEXT,
    photo_path TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_registrations_iso3 ON registrations (iso3);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    for directory in (DATA_DIR, COUNTRY_IMAGE_DIR, PHOTO_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _save_image(uploaded_file, directory, prefix, max_px):
    """업로드 이미지를 회전 보정·축소하여 저장하고 DATA_DIR 기준 상대경로를 돌려준다."""
    image = ImageOps.exif_transpose(Image.open(uploaded_file))
    image.thumbnail((max_px, max_px))
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    path = directory / f"{prefix}_{uuid.uuid4().hex[:12]}.jpg"
    image.save(path, "JPEG", quality=90)
    return str(path.relative_to(DATA_DIR))


def _remove_file(relative_path):
    if relative_path:
        (DATA_DIR / relative_path).unlink(missing_ok=True)


def resolve_path(relative_path):
    """저장된 상대경로를 실제 파일 경로로 바꾼다. 파일이 없으면 None."""
    if not relative_path:
        return None
    path = DATA_DIR / relative_path
    return path if path.exists() else None


# ---------------------------------------------------------------- 국가 설정


def get_country(iso3):
    with connect() as conn:
        row = conn.execute("SELECT * FROM countries WHERE iso3 = ?", (iso3,)).fetchone()
    if row is None:
        return {
            "iso3": iso3,
            "required_flags": 0,
            "image_path": None,
            "provinces": list(DEFAULT_PROVINCES.get(iso3, [])),
        }
    provinces = json.loads(row["provinces"]) if row["provinces"] is not None else None
    if provinces is None:
        provinces = list(DEFAULT_PROVINCES.get(iso3, []))
    return {
        "iso3": iso3,
        "required_flags": row["required_flags"],
        "image_path": row["image_path"],
        "provinces": provinces,
    }


def _upsert_country(conn, iso3, **values):
    conn.execute("INSERT OR IGNORE INTO countries (iso3) VALUES (?)", (iso3,))
    assignments = ", ".join(f"{column} = ?" for column in values)
    conn.execute(
        f"UPDATE countries SET {assignments}, updated_at = ? WHERE iso3 = ?",
        (*values.values(), _now(), iso3),
    )


def save_country_settings(iso3, required_flags, provinces):
    cleaned = list(dict.fromkeys(p.strip() for p in provinces if p.strip()))
    with connect() as conn:
        _upsert_country(
            conn, iso3,
            required_flags=int(required_flags),
            provinces=json.dumps(cleaned, ensure_ascii=False),
        )


def save_country_image(iso3, uploaded_file):
    new_path = _save_image(uploaded_file, COUNTRY_IMAGE_DIR, iso3, COUNTRY_IMAGE_MAX_PX)
    old_path = get_country(iso3)["image_path"]
    with connect() as conn:
        _upsert_country(conn, iso3, image_path=new_path)
    _remove_file(old_path)


def add_province(iso3, province):
    country = get_country(iso3)
    if province not in country["provinces"]:
        save_country_settings(
            iso3, country["required_flags"], [*country["provinces"], province]
        )


# ---------------------------------------------------------------- 깃발 등록


def add_registration(iso3, x, y, province, flag_count, photo=None, **details):
    photo_path = _save_image(photo, PHOTO_DIR, iso3, PHOTO_MAX_PX) if photo else None
    record = {
        **{field: None for field in REGISTRATION_FIELDS},
        **details,
        "iso3": iso3,
        "x": float(x),
        "y": float(y),
        "province": province,
        "flag_count": int(flag_count),
        "photo_path": photo_path,
    }
    columns = ", ".join(REGISTRATION_FIELDS)
    placeholders = ", ".join("?" for _ in REGISTRATION_FIELDS)
    with connect() as conn:
        conn.execute(
            f"INSERT INTO registrations ({columns}, created_at) VALUES ({placeholders}, ?)",
            (*(record[field] for field in REGISTRATION_FIELDS), _now()),
        )
    add_province(iso3, province)


def delete_registration(registration_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT photo_path FROM registrations WHERE id = ?", (registration_id,)
        ).fetchone()
        conn.execute("DELETE FROM registrations WHERE id = ?", (registration_id,))
    if row:
        _remove_file(row["photo_path"])


def list_registrations(iso3=None):
    query = "SELECT * FROM registrations"
    params = ()
    if iso3:
        query += " WHERE iso3 = ?"
        params = (iso3,)
    with connect() as conn:
        return pd.read_sql_query(query + " ORDER BY id", conn, params=params)


# ---------------------------------------------------------------- 합계


def country_totals():
    """국가별 필요 깃발 수·등록 깃발 수·등록 건수 (설정이나 등록이 있는 국가만)."""
    query = """
        SELECT iso3, MAX(required_flags) AS required_flags,
               SUM(flag_count) AS flags, SUM(entries) AS entries
        FROM (
            SELECT iso3, required_flags, 0 AS flag_count, 0 AS entries FROM countries
            UNION ALL
            SELECT iso3, 0, flag_count, 1 FROM registrations
        )
        GROUP BY iso3
    """
    with connect() as conn:
        return pd.read_sql_query(query, conn)


def province_totals(iso3):
    """시도별 등록 건수와 깃발 수 합계. 등록이 없는 시도도 0으로 포함한다."""
    with connect() as conn:
        totals = pd.read_sql_query(
            "SELECT province, COUNT(*) AS entries, SUM(flag_count) AS flags "
            "FROM registrations WHERE iso3 = ? GROUP BY province",
            conn, params=(iso3,),
        )
    provinces = get_country(iso3)["provinces"]
    order = [*provinces, *(p for p in totals["province"] if p not in provinces)]
    return (
        totals.set_index("province")
        .reindex(order, fill_value=0)
        .rename_axis("province")
        .reset_index()
    )
